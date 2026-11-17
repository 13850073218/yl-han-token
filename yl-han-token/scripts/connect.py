"""Portable OpenCode configuration, masked setup guides and minimal API probes."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from connection_input import AGENTS, normalize


def target_path():
    return Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'opencode' / 'opencode.json'


def apply_opencode(configs, path, keep_default=False):
    path = path.expanduser().resolve()
    if path.with_suffix('.jsonc').exists():
        raise ValueError('Found opencode.jsonc; use client UI or merge JSONC without losing comments')
    old = path.read_bytes() if path.exists() else None
    doc = json.loads(old.decode('utf-8-sig')) if old is not None else {}
    providers = doc.setdefault('provider', {})
    # A separate stable provider per exact model keeps distinct credentials intact.
    import hashlib
    choices = []
    for cfg in configs:
        provider = 'hanrouter-' + hashlib.sha256(cfg['id'].encode()).hexdigest()[:16]
        existing = providers.get(provider, {})
        if existing and existing.get('npm') != '@ai-sdk/openai-compatible':
            raise ValueError('Provider collision; existing provider was not changed')
        entry = dict(existing)
        options = dict(entry.get('options', {}))
        options.update(baseURL=cfg['url'], apiKey=cfg['apiKey'])
        models = dict(entry.get('models', {}))
        models[cfg['id']] = dict(models.get(cfg['id'], {}), name=cfg.get('name', cfg['id']))
        entry.update(npm='@ai-sdk/openai-compatible', name='HanRouter', options=options, models=models)
        providers[provider] = entry
        choices.append(provider + '/' + cfg['id'])
    if not keep_default:
        doc['model'] = choices[0]
    content = (json.dumps(doc, ensure_ascii=False, indent=2) + '\n').encode()
    if old == content:
        return {'written': False, 'readback': True, 'models': [c['id'] for c in configs]}
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if old is not None:
        fd, backup = tempfile.mkstemp(prefix=path.name + '.backup-', dir=str(path.parent))
        with os.fdopen(fd, 'wb') as f:
            f.write(old)
        os.chmod(backup, 0o600)
    fd, temporary = tempfile.mkstemp(prefix='.hanrouter-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    assert json.loads(path.read_text(encoding='utf-8')) == doc
    return {'written': True, 'readback': True, 'backupCreated': backup is not None,
            'models': [c['id'] for c in configs], 'networkVerified': False}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(cfg, chat=False):
    url = cfg['url'].removesuffix('/chat/completions')
    path = '/chat/completions' if chat else '/models'
    headers = {'Authorization': 'Bearer ' + cfg['apiKey'], 'Content-Type': 'application/json'}
    payload = {'model': cfg['id'], 'messages': [{'role': 'user', 'content': 'Reply OK'}], 'max_tokens': 8, 'stream': False}
    req = urllib.request.Request(url + path, data=json.dumps(payload).encode() if chat else None, headers=headers)
    started = time.monotonic()
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as r:
            status = r.status
            body = json.loads(r.read(2_000_000))
        if chat:
            choices = body.get('choices', [])
            ok = bool(choices and (choices[0].get('message', {}).get('content') or choices[0].get('message', {}).get('tool_calls')))
            result = {'requestAccepted': bool(choices), 'nonemptyOutput': ok,
                      'note': 'Minimal chat probe; does not verify agent tool calls or all capabilities'}
        else:
            listed = any(m.get('id') == cfg['id'] for m in body.get('data', []))
            ok = listed
            result = {'modelListed': listed, 'note': 'Model listing only; generation not verified'}
        return dict(model=cfg['id'], http=status, ok=ok, **result)
    except urllib.error.HTTPError as e:
        return {'model': cfg['id'], 'http': e.code, 'ok': False,
                'reason': {401: 'authentication_failed', 403: 'permission_denied', 404: 'endpoint_or_model_not_found', 429: 'rate_or_quota_limit'}.get(e.code, 'upstream_error'),
                'note': 'Response body withheld to prevent credential leakage'}
    except Exception:
        return {'model': cfg['id'], 'ok': False, 'reason': 'network_or_response_error'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['apply', 'guide', 'probe'])
    p.add_argument('--agent', choices=AGENTS, required=True)
    sources = p.add_mutually_exclusive_group(required=True)
    sources.add_argument('--stdin', action='store_true')
    sources.add_argument('--input', type=Path)
    p.add_argument('--keep-default', action='store_true')
    p.add_argument('--chat', action='store_true', help='Explicit paid minimal chat test, text models only')
    a = p.parse_args()
    configs = normalize(a.input.read_text(encoding='utf-8-sig') if a.input else sys.stdin.read(), a.agent)
    if a.action == 'apply':
        if a.agent != 'OpenCode':
            raise ValueError('Automatic portable apply supports OpenCode; use hantoken.py for Codex/WorkBuddy/Trae, guide for others')
        if not shutil.which('opencode'):
            raise ValueError('OpenCode CLI not installed; no configuration written')
        result = apply_opencode(configs, target_path(), a.keep_default)
    elif a.action == 'probe':
        result = [probe(c, a.chat) for c in configs]
    else:
        result = {'agent': a.agent, 'configurationWritten': False, 'protocol': 'OpenAI compatible (not Anthropic)',
                  'models': [{'id': c['id'], 'url': c['url'], 'name': c.get('name', c['id']), 'apiKey': '****************'} for c in configs],
                  'reference': 'references/agent-compatibility.md',
                  'note': 'Use the original key in the client. Guide generation is not installation or verification.'}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if a.action == 'probe' and any(not r['ok'] for r in result):
        return 2
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print('操作未完成：请检查输入、客户端和配置格式；未输出密钥或原始异常。', file=sys.stderr)
        raise SystemExit(1)
