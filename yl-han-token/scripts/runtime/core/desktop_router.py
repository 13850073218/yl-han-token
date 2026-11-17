"""Merge desktop catalogs while keeping each model's upstream credentials isolated."""
import copy
import hashlib
import json
import os
import secrets as secure

from core.bridge_runtime import BridgePlan

PROVIDER = "hantoken_desktop_router"
ENV_KEY = "HANTOKEN_CODEX_DESKTOP_ROUTER_KEY"
PROVIDER_KEYS = ("name", "base_url", "wire_api", "env_key", "requires_openai_auth",
                 "request_max_retries", "stream_max_retries")


def uses_router(document, settings):
    current = str(document.get("model_provider", "openai"))
    if current == PROVIDER:
        return True
    provider = document.get("model_providers", {}).get(current, {})
    return (current == settings.get("metadata", {}).get("original_provider")
            and str(provider.get("base_url", "")) ==
            "http://127.0.0.1:%s/v1" % settings.get("port"))


def _route_account(route, secrets, store):
    name = route["env_key"]
    if name in secrets:
        return secrets[name]
    saved = store.read(name)
    if saved:
        return saved[0]
    return os.environ.get(name)


def _same_route(left, right, secrets, store):
    fields = ("wire_api", "model")
    if any(left.get(key) != right.get(key) for key in fields):
        return False
    if left["base_url"].rstrip("/") != right["base_url"].rstrip("/"):
        return False
    if left["env_key"] == right["env_key"]:
        return True
    one, two = _route_account(left, secrets, store), _route_account(right, secrets, store)
    return bool(one and two and one == two)


def stage_router(home, document, previous_document, bundled, configs, entries,
                 staged, secrets, store, *, default_model=None):
    from agents.codex import _catalog_base, _custom_model, _table

    settings_path = home / "hantoken_bridge" / (PROVIDER + ".json")
    previous = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    metadata = copy.deepcopy(previous.get("metadata", {}))
    current = str(previous_document.get("model_provider", "openai"))
    was_managed = uses_router(previous_document, previous)
    if not was_managed:
        source = previous_document
        if current.startswith("hantoken_"):
            snapshot = home / "hantoken.previous-default.json"
            if snapshot.exists():
                before = json.loads(snapshot.read_text(encoding="utf-8"))["before"]
                source = copy.deepcopy(previous_document)
                for key in ("model", "model_provider", "model_catalog_json", "model_reasoning_effort",
                            "profile", "web_search"):
                    source.pop(key, None)
                source.update(before)
                current = str(source.get("model_provider", "openai"))
        provider = previous_document.get("model_providers", {}).get(current, {})
        if not provider.get("base_url") or current.startswith("hantoken_"):
            return None
        if provider.get("wire_api", "responses") != "responses":
            raise RuntimeError("原服务商不是 Responses 协议，无法启用两组模型。")
        if any(provider.get(key) for key in ("http_headers", "env_http_headers", "query_params")):
            raise RuntimeError("原服务商含额外请求头或查询参数，尚不支持合并；原配置未修改。")
        route = {"base_url": str(provider["base_url"]), "wire_api": "responses"}
        if provider.get("env_key"):
            route["env_key"] = str(provider["env_key"])
        else:
            auth_file = home / "auth.json"
            try:
                auth = json.loads(auth_file.read_text(encoding="utf-8"))
                if not isinstance(auth.get("OPENAI_API_KEY"), str) or not auth["OPENAI_API_KEY"]:
                    raise ValueError()
            except (OSError, ValueError):
                raise RuntimeError("两组切换需要原服务商的 API Key 登录；未找到有效凭据，原配置未修改。") from None
            route["auth_file"] = str(auth_file)
        # Preserve full stock capabilities, visibility and any external catalog.
        base = _catalog_base(source, home, bundled)
        managed = base.get("hantoken_provider_models", {})
        models = {m["slug"]: copy.deepcopy(m) for m in bundled["models"]}
        models.update({m["slug"]: copy.deepcopy(m) for m in base["models"] if m["slug"] not in managed})
        metadata.update(original_provider=current, original_route=route, original_models=list(models.values()),
                        original_reasoning=source.get("model_reasoning_effort"),
                        original_web_search=source.get("web_search"),
                        original_provider_config={key: provider[key] for key in PROVIDER_KEYS if key in provider})
    if not metadata.get("original_route"):
        raise RuntimeError("两组模型的原服务商记录缺失，已停止写入。")
    if default_model is not None:
        original_ids = {item["slug"] for item in metadata["original_models"]}
        if default_model not in original_ids:
            raise RuntimeError("指定的默认模型不在原 GPT 模型目录中，已停止写入。")
        document["model"] = default_model

    custom = metadata.setdefault("custom_models", {})
    for cfg, (_, provider_id, env_name) in zip(configs, entries):
        if provider_id == PROVIDER or env_name == ENV_KEY:
            raise RuntimeError("桌面路由服务商和密钥变量为工具保留名称，请使用其他名称。")
        if env_name == metadata["original_route"].get("env_key"):
            raise RuntimeError("自定义模型不能覆盖原 GPT 服务商的密钥变量，请使用独立的 envKey。")
        if any(e["route"]["env_key"] == env_name and e["provider"] != provider_id
               and (e["route"]["base_url"].rstrip("/") != cfg["url"].rstrip("/")
                    or e["route"]["wire_api"] != cfg["codex"]["wireApi"])
               for e in custom.values()):
            raise RuntimeError("不同自定义服务商不能覆盖同一密钥变量，请使用独立的 envKey。")
        identity = hashlib.sha256((provider_id + "\0" + cfg["id"]).encode()).hexdigest()[:16]
        custom[identity] = {
            "info": _custom_model(cfg), "provider": provider_id,
            "route": {"base_url": cfg["url"], "env_key": env_name,
                      "wire_api": cfg["codex"]["wireApi"], "model": cfg["id"]},
        }
    # Provider IDs and env names can change between saved-profile and GUI imports.
    # Compare the actual endpoint/model/account without persisting credential hashes.
    merged = {}
    for identity, entry in custom.items():
        match = next((key for key, saved in merged.items()
                      if _same_route(saved["route"], entry["route"], secrets, store)), None)
        if match is None:
            merged[identity] = entry
        else:
            merged[match]["info"] = entry["info"]
    custom = metadata["custom_models"] = merged
    for entry in custom.values():
        if entry["route"]["wire_api"] == "chat":
            entry["route"]["verify_model"] = True
    routes, models = {}, []
    for original in metadata["original_models"]:
        item = copy.deepcopy(original)
        slug = item["slug"]
        effort = metadata.get("original_reasoning")
        if effort and any(level.get("effort") == effort for level in item.get("supported_reasoning_levels", [])):
            item["default_reasoning_level"] = effort
        routes[slug] = dict(metadata["original_route"], model=slug, verify_model=True)
        item["display_name"] = item.get("display_name", slug)
        models.append(item)
    for identity, entry in custom.items():
        item = copy.deepcopy(entry["info"])
        upstream_model = entry["route"]["model"]
        slug = upstream_model
        if slug in routes:
            slug = "custom-" + identity + "-" + upstream_model
        item.update(slug=slug, display_name="自定义模型 / " + item["display_name"])
        peers = [e for e in custom.values() if e["route"]["model"] == upstream_model]
        if len(peers) > 1:
            from urllib.parse import urlsplit
            item["display_name"] += " (%s; %s)" % (
                urlsplit(entry["route"]["base_url"]).netloc, entry["provider"].removeprefix("hantoken_"))
        routes[slug] = entry["route"]
        models.append(item)
    aliases = dict(metadata.get("model_aliases", {}))
    for old_slug, old_route in previous.get("routes", {}).items():
        if old_slug in routes or not old_route.get("env_key"):
            continue
        canonical = next((slug for slug, route in routes.items() if route.get("env_key")
                          and _same_route(old_route, route, secrets, store)), None)
        if canonical:
            aliases[old_slug] = canonical
    for alias, canonical in aliases.items():
        if alias not in routes and canonical in routes:
            routes[alias] = routes[canonical]
    metadata["model_aliases"] = aliases
    if document.get("model") in aliases:
        document["model"] = aliases[document["model"]]
    catalog = home / "hantoken.desktop.models.json"
    staged[catalog] = (
        catalog.read_bytes() if catalog.exists() else None,
        (json.dumps({"models": models}, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    stored = store.read(ENV_KEY)
    local_secret = stored[0] if stored else os.environ.get(ENV_KEY) or secure.token_urlsafe(32)
    secrets[ENV_KEY] = local_secret
    plan = BridgePlan(
        home, PROVIDER, [{"api_key": local_secret}], ENV_KEY, staged,
        routes=routes, auth_env=ENV_KEY, metadata=metadata,
        auth_sources=[metadata["original_route"]],
    )
    original_id = metadata["original_provider"]
    provider = _table(_table(document, "model_providers", "model_providers"), original_id, original_id)
    original_values = metadata.get("original_provider_config")
    if original_values is None:
        # Migrate older gateway installs using the untouched original provider table.
        original_values = {key: provider[key] for key in PROVIDER_KEYS if key in provider}
        metadata["original_provider_config"] = original_values
        # BridgePlan serialized the metadata before this migration field was added.
        plan.settings["metadata"] = metadata
        staged[plan.settings_path] = (staged[plan.settings_path][0], json.dumps(plan.settings, indent=2).encode())
    provider.update(original_values)
    provider["base_url"] = plan.url
    document["model_provider"] = original_id
    document["model_catalog_json"] = str(catalog)
    document.pop("profile", None)
    # Each catalog entry supplies its own reasoning default and capabilities.
    document.pop("model_reasoning_effort", None)
    plan.was_managed = was_managed
    plan.provider_restore = {
        "id": original_id, "before": dict(original_values),
        "after": {key: provider[key] for key in PROVIDER_KEYS if key in provider},
    }
    if metadata.get("original_web_search") is not None:
        document["web_search"] = metadata["original_web_search"]
    else:
        document.pop("web_search", None)
    for cfg in configs:
        if cfg["codex"].get("setDefault"):
            wanted = {"base_url": cfg["url"], "wire_api": cfg["codex"]["wireApi"], "model": cfg["id"],
                      "env_key": next(e[2] for c, e in zip(configs, entries) if c is cfg)}
            document["model"] = next(slug for slug, route in routes.items() if route.get("env_key")
                                     and _same_route(route, wanted, secrets, store))
            break
    return plan
