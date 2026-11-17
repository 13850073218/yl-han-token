"""Conversation payload normalization. Never infer or alter model ID casing."""
import json
from urllib.parse import urlsplit, urlunsplit

BASE = 'https://api.hanrouter.ai/v1'
AGENTS = ('Codex', 'WorkBuddy', 'Trae', 'OpenCode', 'Cursor', 'Cline', 'RooCode', 'Continue', 'Aider', 'ClaudeCode', 'Generic')


def normalize(raw, agent):
    if agent not in AGENTS:
        raise ValueError('Unsupported agent')
    data = json.loads(raw)
    shared = {}
    if isinstance(data, dict) and 'models' in data:
        shared = {k: v for k, v in data.items() if k != 'models'}
        items = data['models']
    else:
        items = data if isinstance(data, list) else [data]
    if not isinstance(items, list) or not items:
        raise ValueError('Provide at least one model')
    result = {}
    for item in items:
        item = {'id': item} if isinstance(item, str) else item
        if not isinstance(item, dict):
            raise ValueError('Invalid model item')
        cfg = dict(shared, **item)
        if 'id' not in cfg:
            cfg['id'] = cfg.pop('modelId', cfg.pop('model', None))
        if 'apiKey' not in cfg:
            cfg['apiKey'] = cfg.pop('key', None)
        if 'url' not in cfg:
            cfg['url'] = cfg.pop('baseUrl', BASE)
        for field in ('id', 'apiKey', 'url'):
            if not isinstance(cfg.get(field), str) or not cfg[field].strip():
                raise ValueError('Missing id, apiKey or valid URL')
            cfg[field] = cfg[field].strip()
        if any(c in cfg['id'] for c in '\r\n\x00'):
            raise ValueError('Invalid model ID')
        u = urlsplit(cfg['url'])
        if u.username or u.password or u.query or u.fragment or not u.hostname:
            raise ValueError('URL must not contain credentials, query or fragment')
        if u.scheme != 'https' and not (u.scheme == 'http' and u.hostname in ('localhost', '127.0.0.1', '::1')):
            raise ValueError('Use HTTPS, or HTTP on loopback only')
        path = u.path.rstrip('/')
        if path.endswith('/chat/completions'):
            path = path[:-len('/chat/completions')]
        elif path.endswith('/responses'):
            path = path[:-len('/responses')]
        if not path:
            path = '/v1'
        if agent == 'WorkBuddy':
            path += '/chat/completions'
        cfg['url'] = urlunsplit((u.scheme, u.netloc, path, '', ''))
        result[cfg['id']] = cfg
    return list(result.values())
