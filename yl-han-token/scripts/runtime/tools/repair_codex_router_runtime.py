"""Update the installed router error handling without changing Codex configuration."""
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agents.codex import config_dir
from tools.enable_codex_groups import original_credential


def main():
    home = config_dir()
    script = home / "hantoken_chat_bridge.py"
    settings = home / "hantoken_bridge/hantoken_desktop_router.json"
    old_script, old_settings = script.read_bytes(), settings.read_bytes()
    config_before = (home / "config.toml").read_bytes()
    auth_before = (home / "auth.json").read_bytes()
    state = json.loads(old_settings)
    source = pathlib.Path(__file__).resolve().parents[1] / "core/chat_bridge.py"
    new_script = source.read_bytes()
    compile(new_script, str(script), "exec")
    new_hash = hashlib.sha256(new_script).hexdigest()
    url = "http://127.0.0.1:%d" % state["port"]
    def health():
        try:
            with urllib.request.urlopen(url + "/health", timeout=1) as response:
                return json.loads(response.read(128000))
        except (OSError, ValueError):
            return None
    current = health()
    if current and current.get("instance") != state["instance"]:
        raise RuntimeError("Installed gateway instance differs; no changes made")
    def launch():
        pythonw = pathlib.Path(sys.executable).with_name("pythonw.exe")
        command = [str(pythonw if pythonw.exists() else sys.executable), str(script),
                   "--settings", str(settings), "--port", str(state["port"])]
        env = {k: v for k, v in os.environ.items() if not k.startswith("CODEX_SANDBOX_")
               and k != "CODEX_WINDOWS_SANDBOX_PACKAGE_FAMILY"}
        return subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    def await_build(build):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            value = health()
            if value and value.get("build") == build and value.get("instance") == state["instance"]:
                return value
            time.sleep(.1)
        raise RuntimeError("Gateway did not return expected build")
    suffix = ".bak-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    script.with_name(script.name + suffix).write_bytes(old_script)
    settings.with_name(settings.name + suffix).write_bytes(old_settings)
    # Stop only this managed gateway; keep the desktop and all chats running.
    if current:
        secret = original_credential(state["metadata"]["original_route"])
        req = urllib.request.Request(url + "/shutdown", data=b"{}",
                                     headers={"Authorization": "Bearer " + secret})
        with urllib.request.urlopen(req, timeout=3) as response:
            response.read()
        deadline = time.monotonic() + 5
        while health() and time.monotonic() < deadline:
            time.sleep(.1)
        if health():
            raise RuntimeError("Gateway is still running; files unchanged")
    process = None
    try:
        if script.read_bytes() != old_script or settings.read_bytes() != old_settings:
            raise RuntimeError("Concurrent runtime change detected")
        state["script_sha256"] = new_hash
        script.write_bytes(new_script)
        settings.write_text(json.dumps(state, indent=2), encoding="utf-8")
        process = launch()
        value = await_build(new_hash)
    except Exception:
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        script.write_bytes(old_script)
        settings.write_bytes(old_settings)
        launch()
        await_build(hashlib.sha256(old_script).hexdigest())
        raise
    report = {"ok": True, "port_preserved": json.loads(settings.read_bytes())["port"] == json.loads(old_settings)["port"],
              "config_unchanged": (home / "config.toml").read_bytes() == config_before,
              "auth_unchanged": (home / "auth.json").read_bytes() == auth_before,
              "build": new_hash, "backup_suffix": suffix}
    print(json.dumps(report))


if __name__ == "__main__":
    main()
