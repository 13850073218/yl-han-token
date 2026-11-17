"""Stage and start one authenticated, per-user Chat bridge per provider."""
import contextlib
import hashlib
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid

from core.chat_bridge import make_server


def bridge_command(script_path, settings_path, port):
    """Keep a frozen service outside the one-file extraction/USB directory."""
    arguments = ["--settings", str(settings_path), "--port", str(port)]
    executable = pathlib.Path(sys.executable)
    if not getattr(sys, "frozen", False):
        pythonw = executable.with_name("pythonw.exe")
        return [str(pythonw if os.name == "nt" and pythonw.exists() else executable),
                str(script_path), *arguments]
    digest = hashlib.sha256()
    with executable.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    runtime = settings_path.parent / "runtime" / digest.hexdigest()
    runtime.mkdir(parents=True, exist_ok=True)
    service = runtime / "hantoken.exe"
    if not service.exists():
        descriptor, temporary = tempfile.mkstemp(dir=runtime, suffix=".tmp")
        os.close(descriptor)
        try:
            shutil.copyfile(executable, temporary)
            os.replace(temporary, service)
        finally:
            pathlib.Path(temporary).unlink(missing_ok=True)
    return [str(service), "--bridge", *arguments]


class BridgePlan:
    def __init__(self, home, provider_id, configs, env_key, staged, *,
                 routes=None, auth_env=None, metadata=None, auth_sources=None):
        self.provider_id = provider_id
        self.settings_path = home / "hantoken_bridge" / (provider_id + ".json")
        self.script_path = home / "hantoken_chat_bridge.py"
        old = self.settings_path.read_bytes() if self.settings_path.exists() else None
        previous = json.loads(old) if old else {}
        source = pathlib.Path(__file__).with_name("chat_bridge.py").read_bytes()
        source_hash = hashlib.sha256(source).hexdigest()
        if routes is None:
            routes = dict(previous.get("routes", {}))
            for cfg in configs:
                routes[cfg["id"]] = {"base_url":cfg["url"],"env_key":env_key}
        if (previous.get("routes") == routes and previous.get("port")
                and previous.get("script_sha256") == source_hash
                and previous.get("auth_env") == auth_env and previous.get("metadata") == metadata
                and previous.get("auth_sources") == auth_sources):
            self.settings = previous
        else:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1",0));port = sock.getsockname()[1]
            self.settings = {"port":port,"routes":routes,"instance":uuid.uuid4().hex,"script_sha256":source_hash}
            if auth_env:
                self.settings["auth_env"] = auth_env
            if metadata:
                self.settings["metadata"] = metadata
            if auth_sources:
                self.settings["auth_sources"] = auth_sources
        self.url = "http://127.0.0.1:%d/v1" % self.settings["port"]
        self.secret = configs[0]["api_key"]
        self.keys = {env_key:self.secret}
        self.process = None
        self.startup_before = None
        self.startup_written = False
        staged[self.settings_path] = (old,json.dumps(self.settings,indent=2).encode())
        staged[self.script_path] = (self.script_path.read_bytes() if self.script_path.exists() else None,source)

    @contextlib.contextmanager
    def preview(self, cfg):
        server = make_server(self.settings["routes"],key_lookup=lambda name:self.keys[name])
        worker = threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        url = "http://127.0.0.1:%d/v1" % server.server_port
        try:
            yield dict(cfg,url=url,codex_url=url)
        finally:
            server.shutdown();server.server_close();worker.join(timeout=3)

    def healthy(self):
        try:
            with urllib.request.urlopen(self.url.removesuffix('/v1')+'/health',timeout=1) as response:
                data = json.loads(response.read(128000))
            return (data.get("service") == "hantoken-chat-bridge" and data.get("instance") == self.settings["instance"]
                    and data.get("build")==self.settings["script_sha256"] and not data.get("inherited_network_disabled"))
        except (OSError,ValueError):
            return False

    def start(self):
        try:
            command = bridge_command(self.script_path, self.settings_path, self.settings["port"])
            if not self.healthy():
                clean_env={key:value for key,value in os.environ.items()
                           if not key.startswith("CODEX_SANDBOX_") and key!="CODEX_WINDOWS_SANDBOX_PACKAGE_FAMILY"}
                if getattr(sys, "frozen", False):
                    clean_env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
                self.process = subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),
                    close_fds=True,env=clean_env)
                deadline = time.monotonic()+8
                while time.monotonic()<deadline and not self.healthy():
                    if self.process.poll() is not None:
                        break
                    time.sleep(0.1)
                if not self.healthy():
                    raise RuntimeError("本地协议转换服务启动失败。")
            if os.name == "nt":
                import winreg
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run",0,winreg.KEY_READ|winreg.KEY_SET_VALUE) as key:
                    name = "HANTOKEN_CODEX_BRIDGE_" + self.provider_id
                    try:self.startup_before = winreg.QueryValueEx(key,name)
                    except FileNotFoundError:pass
                    self.startup_written = True
                    winreg.SetValueEx(key,name,0,winreg.REG_SZ,subprocess.list2cmdline(command))
        except Exception:
            self.rollback()
            raise RuntimeError("本地转换服务或当前用户登录启动项设置失败，已停止启用。") from None

    def rollback(self):
        if self.startup_written and os.name == "nt":
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r"Software\Microsoft\Windows\CurrentVersion\Run",0,winreg.KEY_SET_VALUE) as key:
                name = "HANTOKEN_CODEX_BRIDGE_" + self.provider_id
                if self.startup_before is None:
                    try:winreg.DeleteValue(key,name)
                    except FileNotFoundError:pass
                else:
                    winreg.SetValueEx(key,name,0,self.startup_before[1],self.startup_before[0])
        if self.process and self.process.poll() is None:
            self.process.terminate();self.process.wait(timeout=5)
        self.startup_written = False
        self.process = None


def start_plans(plans):
    started = []
    try:
        for plan in plans:
            started.append(plan);plan.start()
    except Exception:
        for plan in reversed(started):
            plan.rollback()
        raise
