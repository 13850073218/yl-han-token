"""Capture tool shapes from an isolated CLI against a loopback-only fake API."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agents import codex


def capture(overrides=None, tool_mode="function_calling"):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            import gzip
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if self.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            requests.append(json.loads(raw))
            content = b'{"error":{"message":"intentional local capture stop","type":"invalid_request_error"}}'
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with tempfile.TemporaryDirectory(prefix="hantoken-capture-") as root:
            home = pathlib.Path(root)
            catalog = {"models": [codex._custom_model({"id":"capture-model", "name":"Capture"})]}
            catalog["models"][0]["tool_mode"] = tool_mode
            (home/"models.json").write_text(json.dumps(catalog), encoding="utf-8")
            doc = codex._tomlkit().document()
            doc.update(model="capture-model", model_provider="capture", model_catalog_json=str(home/"models.json"))
            doc["model_providers"] = {"capture": {"name":"Capture", "base_url":"http://127.0.0.1:%s/v1" % server.server_port,
                "wire_api":"responses", "env_key":"HANTOKEN_CAPTURE_KEY", "requires_openai_auth":False,
                "request_max_retries":0, "stream_max_retries":0}}
            if overrides:
                doc.update(overrides)
            (home/"config.toml").write_text(codex._tomlkit().dumps(doc), encoding="utf-8")
            result = subprocess.run([codex._find_cli(), "exec", "--skip-git-repo-check", "--ephemeral",
                "--json", "-s", "read-only", "Reply OK without running tools."], cwd=root,
                env=dict(os.environ,CODEX_HOME=root,HANTOKEN_CAPTURE_KEY="local-dummy-key"),
                capture_output=True, text=True, encoding="utf-8", timeout=25)
            return requests, result
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


if __name__ == "__main__":
    for mode in ("function_calling", "direct", "code_mode_only"):
        requests,result = capture(tool_mode=mode)
        print(json.dumps({"mode":mode,"exit_code":result.returncode,
            "requests":[{"tool_types":[t.get("type") for t in r.get("tools",[])],
                         "tools":[t.get("name") for t in r.get("tools",[])]} for r in requests],
            "error":result.stderr[-500:] if not requests else ""}),flush=True)
