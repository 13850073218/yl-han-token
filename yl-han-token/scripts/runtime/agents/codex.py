# -*- coding: utf-8 -*-
"""Codex providers, profiles and Windows per-user API keys.

Keys never enter TOML. All files and environment variables are staged before
commit; a failed commit restores the original files and user environment.
"""

import datetime
import hashlib
import copy
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import threading
from collections.abc import MutableMapping

from agents.base import AgentAdapter
from core.schema import validate_codex_url
from core.codex_probe import require_desktop_compatibility
from core.bridge_runtime import BridgePlan, start_plans
from core.codex_config import prepare_codex_config

_WRITE_LOCK = threading.Lock()
_MISSING = object()
_DISCOVERY_CACHE = {}


def _discovery_key(command, kind):
    """Only cache offline CLI facts, invalidated when the executable changes."""
    try:
        path = pathlib.Path(command).resolve()
        info = path.stat()
        return (kind, str(path), info.st_mtime_ns, info.st_ctime_ns, info.st_size)
    except (OSError, TypeError):
        return None


def _remember_discovery(key, value):
    if key is not None:
        # Bound memory across executable upgrades; return copies to callers.
        if len(_DISCOVERY_CACHE) >= 16:
            _DISCOVERY_CACHE.clear()
        _DISCOVERY_CACHE[key] = copy.deepcopy(value)
    return value


def config_dir():
    value = os.environ.get("CODEX_HOME", "").strip()
    if value:
        return pathlib.Path(os.path.expandvars(value)).expanduser().absolute()
    return pathlib.Path.home() / ".codex"


def config_path():
    return config_dir() / "config.toml"


def _find_cli():
    explicit = os.environ.get("CODEX_EXE", "").strip()
    if explicit:
        path = pathlib.Path(os.path.expandvars(explicit)).expanduser()
        return str(path) if path.is_file() else None
    command = shutil.which("codex")
    if command:
        return command
    local = os.environ.get("LOCALAPPDATA")
    if local:
        folder = pathlib.Path(local) / "OpenAI" / "Codex" / "bin"
        try:
            candidates = list(folder.glob("*/codex.exe"))
            if candidates:
                return str(max(candidates, key=lambda item: item.stat().st_mtime))
        except OSError:
            pass
    return None


def _profile_format():
    """Older releases use [profiles]; newer CLIs layer named TOML files."""
    command = _find_cli()
    if command and pathlib.Path(command).suffix.lower() not in (".cmd", ".bat"):
        key = _discovery_key(command, "profile")
        if key in _DISCOVERY_CACHE:
            return _DISCOVERY_CACHE[key]
        try:
            result = subprocess.run(
                [command, "--help"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode == 0:
                layout = "files" if ("<name>.config.toml" in result.stdout
                                     or "CONFIG_PROFILE_V2" in result.stdout) else "legacy"
                return _remember_discovery(key, layout)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return "legacy"


def _tomlkit():
    try:
        import tomlkit
    except ImportError:
        raise RuntimeError("缺少依赖 tomlkit，请先执行：python -m pip install -r requirements.txt") from None
    return tomlkit


def _bundled_catalog():
    """Discover catalog support offline without reading the user's credentials."""
    command = _find_cli()
    if not command or pathlib.Path(command).suffix.lower() in (".cmd", ".bat"):
        return None
    key = _discovery_key(command, "catalog")
    if key in _DISCOVERY_CACHE:
        return copy.deepcopy(_DISCOVERY_CACHE[key])
    try:
        with tempfile.TemporaryDirectory(prefix="hantoken-catalog-") as root:
            result = subprocess.run(
                [command, "debug", "models", "--bundled"], cwd=root,
                env=dict(os.environ, CODEX_HOME=root), capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        if result.returncode:
            return None
        data = json.loads(result.stdout)
        if not isinstance(data, dict) or not isinstance(data.get("models"), list):
            return None
        return _remember_discovery(key, data)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _custom_model(cfg):
    # Conservative capabilities: do not inherit GPT model tools or reasoning.
    return {
        "slug": cfg["id"], "display_name": cfg["name"],
        "description": "User configured Responses model",
        "visibility": "list", "supported_in_api": True,
        "default_reasoning_level": "none", "supported_reasoning_levels": [],
        "shell_type": "unified_exec", "priority": 1000,
        "support_verbosity": False,
        "truncation_policy": {"mode": "tokens", "limit": 10000},
        "experimental_supported_tools": [], "input_modalities": ["text"],
        "supports_search_tool": False, "supports_image_detail_original": False,
        "use_responses_lite": False, "tool_mode": "direct",
        "base_instructions": "You are a coding assistant. Use the provided tools to help the user.",
    }


def _catalog_base(document, home, bundled):
    existing = document.get("model_catalog_json")
    if not existing:
        return copy.deepcopy(bundled)
    path = pathlib.Path(str(existing)).expanduser()
    if not path.is_absolute():
        path = home / path
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or not isinstance(data.get("models"), list):
            raise ValueError()
        if any(not isinstance(m, dict) or not isinstance(m.get("slug"), str) for m in data["models"]):
            raise ValueError()
        return data
    except (OSError, ValueError):
        raise RuntimeError("无法读取已有模型目录：%s；已停止写入。" % path) from None


def _stage_catalog(staged, target, base, configs, provider_id, bundled):
    data = copy.deepcopy(base)
    managed = data.get("hantoken_provider_models", {})
    if not isinstance(managed, dict):
        raise RuntimeError("已有模型目录的 hantoken_provider_models 格式无效。")
    # The picker selects a model ID; it does not route each ID to a provider.
    models = {m["slug"]: m for m in bundled["models"]}
    models.update({m["slug"]: m for m in data["models"]
                   if m["slug"] not in managed or managed[m["slug"]] == provider_id})
    managed = {slug: owner for slug, owner in managed.items() if owner == provider_id}
    for cfg in configs:
        models[cfg["id"]] = _custom_model(cfg)
        managed[cfg["id"]] = provider_id
    # Keep stock metadata, but do not offer unverified models on another endpoint.
    for slug, model in models.items():
        if slug not in managed:
            model["visibility"] = "hide"
    data["models"] = list(models.values())
    data["hantoken_provider_models"] = managed
    content = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    original = target.read_bytes() if target.exists() else None
    staged[target] = (original, content)
    return str(target)


def _validate_catalogs(staged):
    command = _find_cli()
    for target, (_, content) in staged.items():
        if target.suffix != ".json" or "models" not in json.loads(content):
            continue
        try:
            with tempfile.TemporaryDirectory(prefix="hantoken-validate-") as root:
                home = pathlib.Path(root)
                catalog = home / "models.json"
                catalog.write_bytes(content)
                (home / "config.toml").write_text(
                    "model_catalog_json = " + json.dumps(str(catalog)) + "\n", encoding="utf-8",
                )
                result = subprocess.run(
                    [command, "debug", "models"], cwd=root,
                    env=dict(os.environ, CODEX_HOME=root), capture_output=True,
                    text=True, encoding="utf-8", errors="replace", timeout=20,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if result.returncode:
                    raise ValueError()
                actual = {m["slug"] for m in json.loads(result.stdout)["models"]}
                expected = {m["slug"] for m in json.loads(content)["models"]}
                if not expected.issubset(actual):
                    raise ValueError()
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
            raise RuntimeError("本机 Codex 未能加载模型目录：%s；尚未写入配置。" % target) from None


def _load(path):
    tomlkit = _tomlkit()
    try:
        raw = path.read_bytes() if path.exists() else None
        text = raw.decode("utf-8-sig") if raw is not None else ""
        return raw, tomlkit.parse(text)
    except (OSError, UnicodeError):
        raise RuntimeError("无法读取配置文件：%s；已停止写入。" % path) from None
    except Exception:
        # Parser exceptions may contain portions of a config file, including keys.
        raise RuntimeError("配置文件不是合法 TOML：%s；已停止写入，请先修复。" % path) from None


def _table(parent, key, path):
    if key not in parent:
        parent[key] = _tomlkit().table()
    table = parent[key]
    if not isinstance(table, MutableMapping):
        raise RuntimeError("配置项 %s 必须是 TOML 表，已停止写入。" % path)
    return table


def _identity(cfg):
    identity = "\0".join((cfg["id"], cfg["name"], cfg.get("codex_url", cfg["url"])))
    options = cfg.get("codex", {})
    if options.get("providerId") or options.get("envKey"):
        identity += "\0" + options.get("providerId", "") + "\0" + options.get("envKey", "")
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    slug = re.sub(r"[^a-z0-9]+", "_", cfg["id"].lower()).strip("_")[:32] or "model"
    return "hantoken_%s_%s" % (slug, digest)

def _provider_identity(cfg):
    options = cfg.get("codex", {})
    if options.get("providerId"):
        return options["providerId"]
    protocol = prepare_codex_config(cfg)["codex"]["wireApi"]
    url = cfg.get("codex_url", cfg["url"])
    identity = "\0".join((url, protocol, options.get("envKey", "")))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return "hantoken_provider_%s" % digest


def _backup(path, raw):
    if raw is None:
        return None
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(path.name + ".bak-" + stamp)
    seq = 1
    while True:
        try:
            with candidate.open("xb") as stream:
                stream.write(raw)
            return candidate
        except FileExistsError:
            candidate = path.with_name(path.name + ".bak-%s-%d" % (stamp, seq))
            seq += 1


def _atomic_write(path, content):
    fd, temporary = tempfile.mkstemp(prefix=".hantoken-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class WindowsUserEnvironment:
    """Persist only independent HANTOKEN_CODEX_* keys under HKCU."""

    @staticmethod
    def _registry():
        if os.name != "nt":
            raise RuntimeError("当前密钥保存方式仅支持 Windows 当前用户环境变量。")
        import winreg
        return winreg

    def read(self, name):
        registry = self._registry()
        try:
            with registry.OpenKey(registry.HKEY_CURRENT_USER, "Environment") as key:
                return registry.QueryValueEx(key, name)
        except FileNotFoundError:
            return None

    def write(self, name, value):
        registry = self._registry()
        with registry.CreateKeyEx(
            registry.HKEY_CURRENT_USER, "Environment", 0, registry.KEY_SET_VALUE
        ) as key:
            if value is None:
                try:
                    registry.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
            else:
                registry.SetValueEx(key, name, 0, value[1], value[0])

    def encode(self, value):
        return (value, self._registry().REG_SZ)

    @staticmethod
    def notify():
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            function = ctypes.windll.user32.SendMessageTimeoutW
            function.argtypes = [
                wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR,
                wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
            ]
            function.restype = wintypes.LPARAM
            result = ctypes.c_size_t()
            return bool(function(0xFFFF, 0x001A, 0, "Environment", 0x0002, 1000, ctypes.byref(result)))


def _commit(staged, secrets, store, finalize=None):
    changed = [(path, old, new) for path, (old, new) in staged.items() if old != new]
    backups = []
    written = []
    env_written = []
    snapshots = {}
    phase = "读取用户环境变量"
    try:
        for name in secrets:
            snapshots[name] = (store.read(name), os.environ.get(name, _MISSING))
        for path, old, new in changed:
            phase = "备份配置文件：%s" % path
            path.parent.mkdir(parents=True, exist_ok=True)
            current = path.read_bytes() if path.exists() else None
            if current != old:
                raise RuntimeError("配置文件在处理期间发生变化，请重试：%s" % path)
            backup = _backup(path, old)
            if backup:
                backups.append(backup)
        for name, value in secrets.items():
            phase = "保存当前用户密钥变量：%s" % name
            # Track even a partially successful registry write for rollback.
            env_written.append(name)
            store.write(name, store.encode(value))
            if store.read(name) != store.encode(value):
                raise RuntimeError("用户环境变量回读校验失败。")
            os.environ[name] = value
        for path, old, new in changed:
            phase = "写入配置文件：%s" % path
            current = path.read_bytes() if path.exists() else None
            if current != old:
                raise RuntimeError("配置文件在处理期间发生变化，请重试：%s" % path)
            _atomic_write(path, new)
            written.append((path, old, new))
            if path.read_bytes() != new:
                raise RuntimeError("配置文件回读校验失败。")
        if finalize:
            phase = "启动当前用户的本地协议转换服务"
            finalize()
    except Exception as error:
        rollback_failed = False
        for path, old, new in reversed(written):
            try:
                # Do not overwrite a concurrent edit by another program.
                if path.read_bytes() != new:
                    rollback_failed = True
                elif old is None:
                    path.unlink()
                else:
                    _atomic_write(path, old)
            except OSError:
                rollback_failed = True
        for name in reversed(env_written):
            previous, process_value = snapshots[name]
            try:
                current = store.read(name)
                if current not in (previous, store.encode(secrets[name])):
                    rollback_failed = True
                    continue
                store.write(name, previous)
                if process_value is _MISSING:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = process_value
            except Exception:
                rollback_failed = True
        detail = "部分回滚失败，请检查备份和用户环境变量。" if rollback_failed else "本批次修改已回滚。"
        # Never include third-party exception text; it may contain an API key.
        recovery = "\n" + "\n".join("备份：%s" % backup for backup in backups) if backups else ""
        raise RuntimeError(
            "Codex 写入失败（%s；%s）。%s%s" % (type(error).__name__, phase, detail, recovery)
        ) from None
    notified = True
    try:
        notified = store.notify() is not False
    except Exception:
        notified = False
    return backups, len(changed), notified


class CodexAdapter(AgentAdapter):
    display_name = "Codex"

    def __init__(self, environment_store=None):
        self.environment_store = environment_store or WindowsUserEnvironment()

    def detect(self):
        command = _find_cli()
        if not command:
            return False, "未找到 Codex CLI，请先安装 Codex 或设置 CODEX_EXE 指向有效程序"
        path = config_path()
        if path.is_file():
            return True, "已找到 Codex 配置：%s" % path
        return True, "已找到 Codex：%s，配置将创建于：%s" % (command, path)

    def running_processes(self):
        if os.name != "nt":
            return []
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq Codex.exe", "/NH"],
                capture_output=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return [
                line for line in result.stdout.decode("gbk", "replace").splitlines()
                if line.lower().startswith("codex.exe")
            ]
        except (OSError, subprocess.TimeoutExpired):
            return []

    def apply(self, model_config):
        return self.apply_many([model_config])

    def logout(self):
        self.require_installed()
        command = _find_cli()
        result = subprocess.run(
            [command, "logout"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            raise RuntimeError("Codex 退出登录失败，请稍后重试。")
        return "已退出 Codex 登录。请重启客户端后重新登录；已导入的第三方服务商密钥仍保留。"

    def restore_defaults(self):
        self.require_installed()
        if not _WRITE_LOCK.acquire(blocking=False):
            raise RuntimeError("另一个 Codex 配置操作正在进行。")
        try:
            path = config_path()
            snapshot = path.parent / "hantoken.previous-default.json"
            if not snapshot.is_file():
                raise RuntimeError("没有新版工具保存的恢复记录。请使用此前的配置备份恢复原服务商。")
            try:
                state = json.loads(snapshot.read_text(encoding="utf-8"))
                before, after = state["before"], state["after"]
                allowed = {"model", "model_provider", "model_catalog_json", "model_reasoning_effort", "profile", "web_search"}
                if set(before) - allowed or set(after) - allowed or not set(before).issubset(after):
                    raise ValueError()
            except (ValueError, KeyError, TypeError):
                raise RuntimeError("默认配置恢复记录损坏，已停止。") from None
            original, document = _load(path)
            provider_state = state.get("provider")
            if provider_state:
                from core.desktop_router import PROVIDER_KEYS
                try:
                    if any(set(provider_state[k]) - set(PROVIDER_KEYS) for k in ("before", "after")):
                        raise ValueError()
                    table = document["model_providers"][provider_state["id"]]
                    actual = {key: table[key] for key in PROVIDER_KEYS if key in table}
                    if actual not in (provider_state["before"], provider_state["after"]):
                        raise RuntimeError("原服务商配置已被其他程序修改，停止恢复以保留你的改动。")
                except (KeyError, TypeError, ValueError):
                    raise RuntimeError("服务商恢复记录损坏，已停止。") from None
            current = {key: document[key].unwrap() if hasattr(document.get(key), "unwrap") else document.get(key)
                       for key in after if key in document}
            if current == before and (not provider_state or actual == provider_state["before"]):
                return "原 Codex 默认配置已恢复，无需再次修改。请在新会话中使用常规模型。"
            if current not in (before, {key: value for key, value in after.items() if value is not None}):
                raise RuntimeError("默认配置已被其他程序修改，停止恢复以保留你的改动。")
            for key in after:
                if key in before:
                    document[key] = before[key]
                elif key in document:
                    del document[key]
            if provider_state:
                for key in PROVIDER_KEYS:
                    if key in provider_state["before"]:
                        table[key] = provider_state["before"][key]
                    elif key in table:
                        del table[key]
            staged = {path: (original, _tomlkit().dumps(document).encode("utf-8"))}
            backups, _, _ = _commit(staged, {}, self.environment_store)
            return "已恢复原 Codex 模型、服务商和模型目录。请重启后使用新会话。\n" + "\n".join("备份：%s" % b for b in backups)
        finally:
            _WRITE_LOCK.release()

    def apply_many(self, model_configs, *, desktop_groups=False, default_model=None):
        if not _WRITE_LOCK.acquire(blocking=False):
            raise RuntimeError("另一个 Codex 配置写入正在进行，请稍后重试。")
        try:
            return self._apply_many(model_configs, desktop_groups=desktop_groups, default_model=default_model)
        finally:
            _WRITE_LOCK.release()

    def _apply_many(self, model_configs, *, desktop_groups=False, default_model=None):
        self.require_installed()
        configs = [prepare_codex_config(dict(cfg, url=cfg.get("codex_url", cfg["url"])))
                   for cfg in (model_configs or [])]
        if not configs:
            raise RuntimeError("没有可写入的模型配置。")
        by_profile = {}
        requested_format = set()
        for cfg in configs:
            validate_codex_url(cfg["url"])
            if cfg["provider"] == "custom_anthropic_compatible":
                raise RuntimeError("Codex 需要 Responses 兼容接口，不支持 Anthropic Messages 协议。")
            options = cfg.get("codex", {})
            identity = _identity(cfg)
            profile = options.get("profile", identity)
            previous = by_profile.get(profile)
            if previous and (previous["id"], previous["name"], previous["url"], _provider_identity(previous)) != (
                cfg["id"], cfg["name"], cfg["url"], _provider_identity(cfg)
            ):
                raise RuntimeError("多个不同模型使用了相同的 Codex profile，请分别命名。")
            by_profile[profile] = cfg
            if options.get("profileFormat", "auto") != "auto":
                requested_format.add(options["profileFormat"])
        if len(requested_format) > 1:
            raise RuntimeError("同一批次的 Codex profileFormat 必须一致。")
        layout = next(iter(requested_format)) if requested_format else _profile_format()
        defaults = [
            (profile, cfg) for profile, cfg in by_profile.items()
            if cfg.get("codex", {}).get("setDefault", False)
        ]
        if len(defaults) > 1:
            raise RuntimeError("一批模型只能设置一个默认模型，请只保留一项 setDefault: true。")
        if default_model is not None and (not desktop_groups or defaults):
            raise RuntimeError("原 GPT 默认模型必须通过两组模式单独指定，不能同时设置自定义默认模型。")

        provider_configs = {}
        for cfg in by_profile.values():
            provider_configs.setdefault(_provider_identity(cfg), []).append(cfg)
        for related in provider_configs.values():
            contracts = {(cfg["url"], cfg["codex"]["wireApi"],
                          cfg["codex"].get("envKey"), cfg["api_key"]) for cfg in related}
            if len(contracts) != 1:
                raise RuntimeError("同一服务商的地址、协议、密钥变量和 API Key 必须一致；不同账号请指定独立的 providerId。")

        path = config_path()
        original, document = _load(path)
        previous_document = copy.deepcopy(document)
        bundled = _bundled_catalog()
        catalog_base = _catalog_base(document, path.parent, bundled) if bundled else None
        providers = _table(document, "model_providers", "model_providers")
        staged = {}
        secrets = {}
        entries = []
        provider_values = {}
        bridge_plans = {}
        for profile, cfg in by_profile.items():
            options = cfg.get("codex", {})
            provider_id = _provider_identity(cfg)
            related = provider_configs[provider_id]
            env_name = options.get(
                "envKey", "HANTOKEN_CODEX_" + provider_id.removeprefix("hantoken_").upper() + "_API_KEY"
            )
            values = {
                "base_url": cfg["url"], "wire_api": "responses",
                "env_key": env_name, "requires_openai_auth": False,
            }
            if options.get("wireApi") == "chat":
                if provider_id not in bridge_plans:
                    bridge_plans[provider_id] = BridgePlan(path.parent,provider_id,related,env_name,staged)
                values["base_url"] = bridge_plans[provider_id].url
                values["request_max_retries"] = 0
                values["stream_max_retries"] = 0
            if provider_id in provider_values and provider_values[provider_id] != values:
                raise RuntimeError("同一 providerId 的服务地址和密钥变量必须一致。")
            provider_values[provider_id] = values
            if env_name in secrets and secrets[env_name] != cfg["api_key"]:
                raise RuntimeError("同一密钥环境变量不能对应不同的 apiKey。")
            secrets[env_name] = cfg["api_key"]
            provider = _table(providers, provider_id, "model_providers." + provider_id)
            provider["name"] = cfg["name"]
            provider.update(values)
            if layout == "files":
                profile_path = path.parent / (profile + ".config.toml")
                old, profile_document = _load(profile_path)
                profile_document["model"] = cfg["id"]
                profile_document["model_provider"] = provider_id
                # File profiles must remain usable after the main config is restored.
                profile_providers = _table(profile_document, "model_providers", "model_providers")
                profile_provider = _table(profile_providers, provider_id, "model_providers." + provider_id)
                profile_provider.update(provider.unwrap())
                profile_document["web_search"] = "disabled"
                _table(profile_document, "features", "features")["multi_agent"] = False
                if bundled:
                    profile_document["model_catalog_json"] = _stage_catalog(
                        staged, path.parent / (profile + ".models.json"),
                        _catalog_base(profile_document, path.parent, bundled)
                        if profile_document.get("model_catalog_json") else catalog_base,
                        related, provider_id, bundled,
                    )
                    profile_document["model_reasoning_effort"] = "none"
                staged[profile_path] = (old, _tomlkit().dumps(profile_document).encode("utf-8"))
            else:
                profiles = _table(document, "profiles", "profiles")
                profile_document = _table(profiles, profile, "profiles." + profile)
                profile_document["model"] = cfg["id"]
                profile_document["model_provider"] = provider_id
                profile_document["web_search"] = "disabled"
                _table(profile_document, "features", "features")["multi_agent"] = False
                if bundled:
                    profile_document["model_catalog_json"] = _stage_catalog(
                        staged, path.parent / (profile + ".models.json"), catalog_base,
                        related, provider_id, bundled,
                    )
                    profile_document["model_reasoning_effort"] = "none"
            entries.append((profile, provider_id, env_name))

        from core.desktop_router import stage_router, uses_router, PROVIDER as ROUTER_PROVIDER
        router_plan = None
        settings_path = path.parent / "hantoken_bridge" / (ROUTER_PROVIDER + ".json")
        previous_settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
        if desktop_groups and not bundled:
            raise RuntimeError("本机 Codex 不支持模型目录，无法启用两组模型；未修改配置。")
        if bundled and (desktop_groups or defaults or uses_router(previous_document, previous_settings)):
            router_plan = stage_router(
                path.parent, document, previous_document, bundled,
                list(by_profile.values()), entries, staged, secrets, self.environment_store,
                default_model=default_model,
            )
            if router_plan:
                bridge_plans[ROUTER_PROVIDER] = router_plan
            elif desktop_groups:
                raise RuntimeError("未找到原 GPT 服务商的 Responses 地址，无法合并两组；请先配置原服务商。")
        if defaults and not router_plan:
            profile, cfg = defaults[0]
            document["model"] = cfg["id"]
            document["model_provider"] = _provider_identity(cfg)
            # A legacy default profile would otherwise override the new model.
            if document.get("profile"):
                if layout == "legacy":
                    document["profile"] = profile
                else:
                    del document["profile"]
            if bundled:
                document["model_reasoning_effort"] = "none"
            if cfg.get("codex",{}).get("wireApi") == "chat":
                document["web_search"] = "disabled"
        active_provider = document.get("model_provider", "openai")
        active_configs = [cfg for cfg in by_profile.values()
                          if _provider_identity(cfg) == active_provider]
        if router_plan:
            active_configs = list(by_profile.values())
        if bundled and active_configs and not router_plan:
            document["model_catalog_json"] = _stage_catalog(
                staged, path.parent / "hantoken.models.json", catalog_base,
                active_configs, active_provider, bundled,
            )
        # Never replace a working default merely because TOML/catalog parsing succeeds.
        for cfg in active_configs:
            require_desktop_compatibility(cfg)
        if defaults or router_plan:
            state_path = path.parent / "hantoken.previous-default.json"
            previous = _tomlkit().parse(original.decode("utf-8-sig")) if original else {}
            keys = ("model", "model_provider", "model_catalog_json", "model_reasoning_effort", "profile", "web_search")
            before = {key: previous[key] for key in keys if key in previous}
            if (str(previous.get("model_provider", "")).startswith("hantoken_")
                    or (router_plan and router_plan.was_managed)) and state_path.exists():
                try:
                    before = json.loads(state_path.read_text(encoding="utf-8"))["before"]
                except (ValueError, KeyError):
                    raise RuntimeError("默认配置恢复记录损坏，已停止写入。") from None
            state = {"before": before, "after": {key: document.get(key) for key in keys}}
            if router_plan:
                state["provider"] = router_plan.provider_restore
            staged[state_path] = (state_path.read_bytes() if state_path.exists() else None,
                                  json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8"))
        staged[path] = (original, _tomlkit().dumps(document).encode("utf-8"))
        for target, (_, content) in staged.items():
            if target.suffix == ".json":
                json.loads(content.decode("utf-8"))
            elif target.suffix == ".toml":
                _tomlkit().parse(content.decode("utf-8"))
        _validate_catalogs(staged)
        backups, count, notified = _commit(staged, secrets, self.environment_store,
                                           finalize=lambda:start_plans(list(bridge_plans.values())))
        lines = [
            "已写入 %d 个 Codex 模型配置；更新 %d 个配置文件。" % (len(by_profile), count),
            "主配置：%s" % path,
            "配置档案格式：%s。" % ("独立 .config.toml 文件" if layout == "files" else "config.toml 内的 [profiles]"),
        ]
        for profile, provider_id, env_name in entries:
            lines.append("启动命令：codex -p %s" % profile)
            lines.append("  密钥变量：%s（当前 Windows 用户）" % env_name)
            if layout == "files":
                lines.append("  档案文件：%s" % (path.parent / (profile + ".config.toml")))
        if default_model is not None:
            lines.append("已设置默认模型：%s。" % default_model)
        else:
            lines.append("已设置默认模型：%s。" % defaults[0][1]["name"] if defaults else "已保留原默认模型设置。")
        lines.extend("备份：%s" % backup for backup in backups)
        if len(configs) != len(by_profile):
            lines.append("批次内重复配置已合并，保留最后一项。")
        lines.append("API Key 已保存到当前用户环境变量；auth.json 未修改。")
        lines.append("桌面接口实际校验已通过；已发送少量测试请求。"
                     if active_configs else "仅保存配置；本次未发送模型请求，尚未证明接口可用。")
        lines.append("CLI 档案已禁用内置多代理和网页搜索，以减少第三方接口不支持的工具类型。")
        if bridge_plans:
            lines.append("Chat Completions 已通过当前用户的本地转换服务接入；只监听 127.0.0.1。")
            lines.append("已设置当前用户登录启动转换服务；密钥仍从用户环境变量读取。")
        if bundled and active_configs:
            lines.append("已注册当前服务商的桌面模型目录：%s。" % document["model_catalog_json"])
            lines.append("本批次当前服务商模型：%s。" % "、".join(cfg["id"] for cfg in active_configs))
        elif bundled:
            lines.append("模型目录已写入 CLI 档案；桌面端仍使用原服务商。需要桌面端使用时，请勾选设为默认模型后写入。")
        else:
            lines.append("本机 CLI 未提供可读取的内置模型目录；仅写入 CLI 档案，尚未注册桌面模型列表。")
        if router_plan:
            lines.append("桌面模型列表保留 GPT 原名称，自定义模型添加分类前缀；选择模型即按模型路由，无需退出登录。")
            lines.append("窗口导入的模型均归入自定义模型；之前通过本工具合并的模型会继续保留。")
        else:
            lines.append("当前未合并原服务商；目录中的模型使用当前服务商地址和密钥。")
            lines.append("自定义服务商目录仅展示已导入的模型；切回常规模型请使用“恢复原 Codex 默认配置”。")
        lines.append("请完全退出后重新启动 Codex，并在新会话模型列表中验证。")
        lines.append("现有终端和父进程可能仍持有旧环境；若提示缺少密钥，请注销并重新登录 Windows。")
        if not notified:
            lines.append("环境变更广播失败；请注销并重新登录 Windows 后使用。")
        return "\n".join(lines)
