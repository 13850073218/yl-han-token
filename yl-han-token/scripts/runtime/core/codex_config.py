"""Resolve Codex protocol presets without network requests or changing input."""
from urllib.parse import urlsplit


def select_codex_defaults(configs, *, keep_default=False, default_model=None):
    """Entry-point policy: use the first model unless the user chose otherwise.

    The low-level adapter remains opt-in so existing programmatic callers keep
    their behavior. Work on copies; repeated first-model records must carry the
    choice through last-record-wins deduplication.
    """
    result = [dict(cfg, codex=dict(cfg.get("codex", {}))) for cfg in configs]
    if keep_default:
        for cfg in result:
            cfg["codex"]["setDefault"] = False
        return result
    if not result or default_model is not None or any("setDefault" in cfg["codex"] for cfg in result):
        return result

    def identity(cfg):
        options = cfg["codex"]
        return options.get("profile") or (
            cfg["id"], cfg.get("name", cfg["id"]), cfg["url"],
            cfg["api_key"], options.get("wireApi"),
            options.get("providerId"), options.get("envKey"),
        )

    first = identity(result[0])
    for cfg in result:
        if identity(cfg) == first:
            cfg["codex"]["setDefault"] = True
    return result


def prepare_codex_config(cfg, protocol="auto"):
    options = dict(cfg.get("codex", {}))
    selected = options.get("wireApi", "auto")
    if selected == "auto":
        selected = protocol
    if selected == "auto":
        hostname = urlsplit(cfg.get("codex_url", cfg.get("url", ""))).hostname
        selected = "chat" if hostname == "api.hanrouter.ai" else "responses"
    if selected not in ("responses", "chat"):
        raise ValueError("Unsupported Codex protocol: %s" % selected)
    options["wireApi"] = selected
    return dict(cfg, codex=options)
