"""Standalone skill entry point; resolve all code relative to this file."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
sys.path[:0] = [str(RUNTIME), str(ROOT / "vendor")]
sys.dont_write_bytecode = True


def ensure_trae_dependency():
    if importlib.util.find_spec("playwright"):
        return
    import venv
    env_root = ROOT / ".trae-env"
    executable = env_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    ready = False
    if executable.is_file():
        try:
            ready = subprocess.run(
                [str(executable), "-c", "import playwright.sync_api"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
            ).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            pass
    if not ready:
        if not executable.is_file():
            venv.EnvBuilder(with_pip=True).create(env_root)
        subprocess.run([str(executable), "-m", "pip", "install", "playwright>=1.50,<2"], check=True)
    return executable


@contextmanager
def timed_stage(enabled, stage):
    started = time.perf_counter()
    try:
        yield
    finally:
        if enabled:
            print(json.dumps({"stage": stage, "elapsed_seconds": round(time.perf_counter() - started, 3)},
                             ensure_ascii=False), file=sys.stderr)


def parser():
    result = argparse.ArgumentParser(description="yl-han-token 独立模型配置工具（无需原项目）")
    result.add_argument("action", choices=["detect", "gui", "apply", "diagnose", "switch-check", "restore", "self-check"])
    result.add_argument("--agent", choices=["Codex", "WorkBuddy", "Trae"], default="Codex")
    source = result.add_mutually_exclusive_group()
    source.add_argument("--input", type=Path, help="JSON 文件；敏感配置不要放入共享目录")
    source.add_argument("--stdin", action="store_true", help="从标准输入读取 JSON，避免密钥出现在命令行")
    result.add_argument("--protocol", choices=["auto", "responses", "chat"], default="auto")
    defaults = result.add_mutually_exclusive_group()
    defaults.add_argument("--default-model", help="指定已有常规模型的精确默认 ID，覆盖默认选择第一项的行为")
    defaults.add_argument("--keep-default", action="store_true", help="Codex 仅添加模型，保留当前默认选择")
    result.add_argument("--models", nargs="+", help="switch-check 的精确模型序列")
    result.add_argument("--report", type=Path, help="仅保存脱敏验证结果")
    result.add_argument("--timings", action="store_true", help="向标准错误输出脱敏阶段耗时，不含配置内容")
    return result


def adapter_for(name):
    if name == "Codex":
        from agents.codex import CodexAdapter
        return CodexAdapter()
    if name == "WorkBuddy":
        from agents.workbuddy import WorkBuddyAdapter
        return WorkBuddyAdapter()
    from agents.trae_cn import TraeCNAdapter
    return TraeCNAdapter()


def run(args):
    if args.action == "self-check":
        import hashlib
        skill = ROOT.parent
        manifest = json.loads((skill / "manifest.json").read_text(encoding="utf-8"))
        for relative, expected in manifest["files"].items():
            path = (skill / relative).resolve()
            if not path.is_relative_to(skill) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise RuntimeError("技能文件不完整或被改动，请重新安装分发包。")
        from core.schema import parse_model_configs
        import tomlkit
        from core.chat_bridge import translate_request
        sample = [{"id": name, "url": " https://api.hanrouter.ai/v1 ", "apiKey": "self-check-placeholder"}
                  for name in ("glm-5.3-flash", "qwen3.7-flash")]
        configs = parse_model_configs(json.dumps(sample))
        assert len(configs) == 2 and configs[0]["url"] == "https://api.hanrouter.ai/v1"
        request, _ = translate_request({"model": configs[1]["id"], "input": "test"})
        assert request["model"] == configs[1]["id"]
        assert Path(tomlkit.__file__).resolve().is_relative_to(ROOT / "vendor")
        from connection_input import normalize
        batch = normalize(json.dumps({"apiKey": "self-check-placeholder", "models": ["Exact-A", "exact-a"]}), "OpenCode")
        assert len(batch) == 2 and batch[0]["url"] == "https://api.hanrouter.ai/v1"
        print(json.dumps({"ok": True, "standalone": True, "agents": ["Codex", "WorkBuddy", "Trae", "OpenCode"],
                          "python": sys.version.split()[0]}, ensure_ascii=False))
        return
    if os.name != "nt":
        raise RuntimeError("当前客户端适配器支持 Windows；未修改配置。")
    with timed_stage(args.timings, "client_discovery"):
        adapter = adapter_for(args.agent)
    if args.action == "detect":
        ok, message = adapter.detect()
        print(json.dumps({"installed": ok, "message": message}, ensure_ascii=False))
        return
    with timed_stage(args.timings, "installation_check"):
        adapter.require_installed()
    if args.agent == "Trae" and args.action in ("apply", "gui"):
        with timed_stage(args.timings, "trae_dependency"):
            executable = ensure_trae_dependency()
        if executable:
            result = subprocess.run([str(executable), "-X", "utf8", str(Path(__file__).resolve()), *sys.argv[1:]])
            raise SystemExit(result.returncode)
    if args.action == "gui":
        from main import App
        App(initial_agent=adapter.display_name, initial_protocol=args.protocol,
            initial_set_default=not args.keep_default).mainloop()
        return
    if args.action == "apply":
        from core.schema import parse_model_configs
        if not args.input and not args.stdin:
            raise RuntimeError("请通过 --input 或 --stdin 提供模型对象/数组；不要在命令行传密钥。")
        with timed_stage(args.timings, "input_parse"):
            raw = args.input.read_text(encoding="utf-8-sig") if args.input else sys.stdin.read()
            from connection_input import normalize
            try:
                normalized = normalize(raw, args.agent)
            except json.JSONDecodeError:
                # Keep legacy Markdown/JSON paste support when all fields are provided.
                normalized = parse_model_configs(raw)
            configs = parse_model_configs(json.dumps(normalized, ensure_ascii=False))
        if args.agent == "Codex":
            from core.codex_config import prepare_codex_config, select_codex_defaults
            configs = [prepare_codex_config(cfg, args.protocol) for cfg in configs]
            configs = select_codex_defaults(configs, keep_default=args.keep_default, default_model=args.default_model)
            with timed_stage(args.timings, "codex_validate_and_commit"):
                print(adapter.apply_many(configs, desktop_groups=True, default_model=args.default_model))
        else:
            if args.default_model or args.keep_default:
                raise RuntimeError("--default-model 和 --keep-default 仅用于 Codex。")
            with timed_stage(args.timings, "apply_models"):
                print(adapter.apply_many(configs))
        return
    if args.agent != "Codex":
        raise RuntimeError("此操作仅用于 Codex。")
    from agents.codex import config_dir
    home = config_dir()
    if args.action == "diagnose":
        from core.codex_diagnostics import routing_report
        print(routing_report(home))
    elif args.action == "restore":
        print(adapter.restore_defaults())
    elif args.action == "switch-check":
        from agents.codex import _load
        from core.codex_switch_check import check_switching
        from tools.enable_codex_groups import original_credential
        from core.desktop_router import uses_router
        import urllib.request
        settings = json.loads((home / "hantoken_bridge/hantoken_desktop_router.json").read_text(encoding="utf-8"))
        _, doc = _load(home / "config.toml")
        if not uses_router(doc, settings):
            raise RuntimeError("主配置未启用统一入口；先导入配置再验证安装效果。")
        models = json.loads(Path(str(doc["model_catalog_json"])).read_text(encoding="utf-8"))["models"]
        sequence = args.models or [str(doc["model"]), "glm-5.3-flash", "qwen3.7-flash", str(doc["model"])]
        if not set(sequence).issubset({m["slug"] for m in models}):
            raise RuntimeError("验证序列含目录外模型 ID，请通过 --models 指定已配置的精确 ID。")
        original = settings["metadata"]["original_route"]
        url = "http://127.0.0.1:%s/v1" % settings["port"]
        checked = check_switching(url, original_credential(original), models, sequence,
            features=dict(doc.get("features", {})), web_search=doc.get("web_search"),
            provider_id=settings["metadata"]["original_provider"])
        with urllib.request.urlopen(url.removesuffix("/v1") + "/health", timeout=3) as response:
            health = json.loads(response.read(128000))
        report = {"switch_check": checked, "route_evidence": health.get("recent_routes", []),
                  "last_error": health.get("last_error")}
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
        if not checked["ok"]:
            raise SystemExit(2)


if __name__ == "__main__":
    try:
        run(parser().parse_args())
    except Exception as error:
        # Do not stringify arbitrary exceptions: JSON/parser/HTTP errors can contain input secrets.
        if isinstance(error, RuntimeError):
            print(str(error), file=sys.stderr)
        else:
            print("操作未完成（%s），请检查输入、目标客户端和网络；未输出敏感输入。" % type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
