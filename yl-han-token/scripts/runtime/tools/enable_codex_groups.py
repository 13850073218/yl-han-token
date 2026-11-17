"""Enable desktop groups from saved profiles without placing API keys on the command line."""
import argparse
import json
import pathlib
import os
import queue
import subprocess
import sys
import threading
import copy
import urllib.request
from contextlib import contextmanager

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from agents.codex import CodexAdapter, _find_cli, _load, config_dir
from core.chat_bridge import read_key, make_server
from core.codex_tool_check import check_tool
from core.codex_switch_check import check_switching
from core.desktop_router import ENV_KEY
from core.schema import parse_model_config


def inspect_models(home):
    process = subprocess.Popen(
        [_find_cli(), "app-server", "--stdio"], cwd=str(home),
        env=dict(os.environ, CODEX_HOME=str(home)),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8",
    )
    messages = queue.Queue()
    def reader():
        for line in process.stdout:
            messages.put(line)
    worker = threading.Thread(target=reader, daemon=True)
    worker.start()
    def request(identifier, method, params):
        process.stdin.write(json.dumps(dict(id=identifier, method=method, params=params)) + "\n")
        process.stdin.flush()
        while True:
            response = json.loads(messages.get(timeout=20))
            if response.get("id") == identifier:
                if "error" in response:
                    raise RuntimeError("Native Codex inspection failed")
                return response["result"]
    try:
        request(1, "initialize", {"clientInfo": {"name": "hantoken_groups_verify", "version": "1"}})
        rows = request(2, "model/list", {"includeHidden": False})["data"]
        return [{"id": row["model"], "label": row["displayName"]} for row in rows]
    finally:
        process.terminate()
        process.wait(timeout=5)
        worker.join(timeout=3)
        process.stdin.close()
        process.stdout.close()


def load_profile(home, name):
    if not name.startswith("hantoken_") or pathlib.Path(name).name != name or any(c in name for c in "/\\:"):
        raise RuntimeError("Invalid saved profile name")
    _, profile = _load(home / (name + ".config.toml"))
    provider_id = str(profile["model_provider"])
    provider = profile["model_providers"][provider_id]
    model_id = str(profile["model"])
    env_key = str(provider["env_key"])
    url, protocol = str(provider["base_url"]), "responses"
    settings = home / "hantoken_bridge" / (provider_id + ".json")
    if settings.exists():
        route = json.loads(settings.read_text(encoding="utf-8"))["routes"].get(model_id)
        if route and url.startswith("http://127.0.0.1:"):
            url, protocol = route["base_url"], route.get("wire_api", "chat")
            env_key = route["env_key"]
    cfg = parse_model_config(json.dumps({
        "id": model_id, "url": url, "apiKey": read_key(env_key),
        "codex": {"providerId": provider_id, "profile": name, "envKey": env_key, "wireApi": protocol},
    }))
    return cfg


def original_credential(route):
    if route.get("auth_file"):
        value = json.loads(pathlib.Path(route["auth_file"]).read_text(encoding="utf-8"))["OPENAI_API_KEY"]
        if not isinstance(value, str) or not value:
            raise ValueError("Original provider credential missing")
        return value
    return read_key(route["env_key"])


@contextmanager
def preview_gateway(home):
    state = json.loads((home / "hantoken_bridge/hantoken_desktop_router.json").read_text(encoding="utf-8"))
    routes = copy.deepcopy(state["routes"])
    for route in routes.values():
        route["verify_model"] = True
    server = make_server(routes, auth_env=ENV_KEY, auth_sources=[state["metadata"]["original_route"]])
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield "http://127.0.0.1:%d/v1" % server.server_port, state
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def verify_old_thread(home, url, state, models, sequence):
    _, document = _load(home / "config.toml")
    original = state["metadata"]["original_route"]
    result = check_switching(
        url, original_credential(original), models, sequence,
        features=dict(document.get("features", {})), web_search=document.get("web_search"),
        provider_id=state["metadata"]["original_provider"], initial_url=original["base_url"],
        resume_after_first=True,
    )
    with urllib.request.urlopen(url.removesuffix("/v1") + "/health", timeout=3) as response:
        health = json.loads(response.read())
    return {"switch_check": result, "route_evidence": health.get("recent_routes", []),
            "last_error": health.get("last_error")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", action="append")
    parser.add_argument("--inspect-only", action="store_true", help="Inspect native model/list without reapplying")
    parser.add_argument("--preview-only", action="store_true", help="Test current source with real upstreams without changing installed config")
    parser.add_argument("--gpt-model", help="Exact GPT model ID to verify in preview; never changes installed default")
    parser.add_argument("--default-model", help="Set an exact original GPT catalog model as the installed default")
    parser.add_argument("--verify", action="store_true", help="Run real tool tasks for the default and imported models")
    parser.add_argument("--switch-check", action="store_true", help="Test default/custom/default in one conversation with default tools")
    parser.add_argument("--report", type=pathlib.Path)
    args = parser.parse_args()
    home = config_dir()
    if args.preview_only:
        config_before = (home / "config.toml").read_bytes()
        auth_before = (home / "auth.json").read_bytes() if (home / "auth.json").exists() else None
        _, document = _load(home / "config.toml")
        catalog = json.loads((home / "hantoken.desktop.models.json").read_text(encoding="utf-8"))
        gpt_model = args.gpt_model or str(document["model"])
        sequence = [gpt_model, "glm-5.3-flash", "qwen3.7-flash", gpt_model]
        with preview_gateway(home) as (url, state):
            report = verify_old_thread(home, url, state, catalog["models"], sequence)
        report["installed_config_unchanged"] = (home / "config.toml").read_bytes() == config_before
        report["auth_unchanged"] = ((home / "auth.json").read_bytes() if (home / "auth.json").exists() else None) == auth_before
        print(json.dumps(report, ensure_ascii=True), flush=True)
        if args.report:
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if not report["switch_check"]["ok"]:
            raise SystemExit(2)
        return
    if args.inspect_only:
        report = json.loads(args.report.read_text(encoding="utf-8")) if args.report and args.report.exists() else {}
        report["native_visible_models"] = inspect_models(home)
        if args.report:
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"native_visible_models": report["native_visible_models"]}, ensure_ascii=True))
        return
    if not args.profile:
        parser.error("--profile is required unless --inspect-only is used")
    configs = [load_profile(home, name) for name in args.profile]
    print(CodexAdapter().apply_many(configs, desktop_groups=True, default_model=args.default_model), flush=True)
    _, document = _load(home / "config.toml")
    provider = document["model_providers"][document["model_provider"]]
    catalog = json.loads(pathlib.Path(str(document["model_catalog_json"])).read_text(encoding="utf-8"))
    report = {
        "provider": str(document["model_provider"]), "default_model": str(document["model"]),
        "visible_models": [{"id": m["slug"], "label": m.get("display_name", m["slug"])}
                           for m in catalog["models"] if m.get("visibility") == "list"],
    }
    if args.verify:
        results = []
        for model_id in dict.fromkeys([str(document["model"])] + [cfg["id"] for cfg in configs]):
            info = next(m for m in catalog["models"] if m["slug"] == model_id)
            result = check_tool({"id": model_id, "name": model_id, "url": str(provider["base_url"]),
                                 "api_key": read_key(ENV_KEY), "model_info": info,
                                 "reasoning_effort": info.get("default_reasoning_level", "none")}, timeout=90)
            result["model"] = model_id
            results.append(result)
            print(json.dumps(result, ensure_ascii=True), flush=True)
        report["tool_checks"] = results
    if args.switch_check:
        sequence = list(dict.fromkeys([str(document["model"])] + [cfg["id"] for cfg in configs]))
        sequence.append(str(document["model"]))
        state = json.loads((home / "hantoken_bridge/hantoken_desktop_router.json").read_text(encoding="utf-8"))
        verified = verify_old_thread(home, str(provider["base_url"]), state, catalog["models"], sequence)
        report.update(verified)
        print(json.dumps(verified, ensure_ascii=True), flush=True)
    report["native_visible_models"] = inspect_models(home)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.verify and not all(result["ok"] for result in report["tool_checks"]):
        raise SystemExit(2)
    if args.switch_check and not report["switch_check"]["ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
