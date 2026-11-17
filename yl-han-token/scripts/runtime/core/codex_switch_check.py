"""Exercise native app-server model changes within one conversation, with default tools."""
import json
import os
import pathlib
import queue
import subprocess
import tempfile
import threading
import time


def check_switching(url, secret, models, sequence, *, timeout=90, features=None, web_search=None,
                    provider_id="probe", initial_url=None, resume_after_first=False):
    from agents import codex
    results = []
    with tempfile.TemporaryDirectory(prefix="hantoken-switch-") as root:
        home = pathlib.Path(root)
        (home / "models.json").write_text(json.dumps({"models": models}), encoding="utf-8")
        doc = codex._tomlkit().document()
        doc.update(model=sequence[0], model_provider=provider_id, model_catalog_json=str(home / "models.json"))
        if features is not None:
            doc["features"] = features
        if web_search is not None:
            doc["web_search"] = web_search
        doc["model_providers"] = {provider_id: {
            "name": "Switching probe", "base_url": url, "wire_api": "responses",
            "env_key": "HANTOKEN_SWITCH_TEST_KEY", "requires_openai_auth": False,
            "request_max_retries": 0, "stream_max_retries": 0,
        }}
        messages = queue.Queue()
        def start(endpoint):
            doc["model_providers"][provider_id]["base_url"] = endpoint
            (home / "config.toml").write_text(codex._tomlkit().dumps(doc), encoding="utf-8")
            child = subprocess.Popen(
                [codex._find_cli(), "app-server", "--stdio"], cwd=root,
                env=dict(os.environ, CODEX_HOME=root, HANTOKEN_SWITCH_TEST_KEY=secret),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8",
            )
            def reader():
                for line in child.stdout:
                    try:
                        messages.put(json.loads(line))
                    except ValueError:
                        pass
            worker = threading.Thread(target=reader, daemon=True)
            worker.start()
            return child, worker
        def stop():
            process.terminate()
            process.wait(timeout=5)
            worker.join(timeout=3)
            process.stdin.close()
            process.stdout.close()
        process, worker = start(initial_url or url)
        def send(value):
            process.stdin.write(json.dumps(value) + "\n")
            process.stdin.flush()
        def request(identifier, method, params):
            send(dict(id=identifier, method=method, params=params))
            while True:
                response = messages.get(timeout=20)
                if response.get("id") == identifier:
                    if "error" in response:
                        raise ValueError("Native switching request rejected")
                    return response["result"]
        try:
            request(1, "initialize", {"clientInfo": {"name": "hantoken_switch_check", "version": "1"},
                                    "capabilities": {"experimentalApi": True}})
            tool = {"type": "namespace", "name": "hantoken", "description": "Test tools", "tools": [{
                "type": "function", "name": "echo", "description": "Return supplied text unchanged.",
                "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                                "required": ["text"], "additionalProperties": False},
            }]}
            thread = request(2, "thread/start", {
                "model": sequence[0], "ephemeral": not resume_after_first, "cwd": root,
                "approvalPolicy": "never", "sandbox": "read-only", "dynamicTools": [tool],
            })["thread"]["id"]
            for index, model_id in enumerate(sequence):
                if index == 1 and resume_after_first:
                    stop()
                    while not messages.empty():
                        messages.get_nowait()
                    process, worker = start(url)
                    request(101, "initialize", {"clientInfo": {"name": "hantoken_switch_check", "version": "1"},
                                              "capabilities": {"experimentalApi": True}})
                    resumed = request(102, "thread/resume", {"threadId": thread})
                    if resumed["thread"].get("modelProvider") != provider_id:
                        raise ValueError("Resumed provider does not match original")
                marker = "HANTOKEN_SWITCH_%d_OK" % index
                info = next(m for m in models if m["slug"] == model_id)
                request(10 + index, "turn/start", {
                    "threadId": thread, "model": model_id,
                    "effort": info.get("default_reasoning_level", "none"),
                    "input": [{"type": "text", "text":
                               "For this turn call hantoken.echo exactly once with text " + marker +
                               ", then reply with the returned text. Do not call other tools."}],
                })
                called, answered, status = False, False, "timeout"
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    event = messages.get(timeout=max(0.1, deadline - time.monotonic()))
                    if event.get("method") == "item/tool/call":
                        params = event["params"]
                        valid = (params.get("namespace") == "hantoken" and params.get("tool") == "echo"
                                 and params.get("arguments") == {"text": marker})
                        called |= valid
                        send({"id": event["id"], "result": {"success": valid, "contentItems": [
                            {"type": "inputText", "text": marker if valid else "Invalid test call"}]}})
                    elif event.get("method") == "item/completed":
                        item = event.get("params", {}).get("item", {})
                        answered |= item.get("type") == "agentMessage" and marker in item.get("text", "")
                    elif event.get("method") == "turn/completed":
                        status = event["params"]["turn"].get("status")
                        break
                results.append({"model": model_id, "ok": called and answered and status == "completed",
                                "tool_called": called, "answered": answered, "turn_status": status})
                if not results[-1]["ok"]:
                    break
        except (ValueError, queue.Empty, OSError):
            results.append({"model": sequence[min(len(results), len(sequence) - 1)], "ok": False,
                            "reason": "Native switch test timed out or failed"})
        finally:
            stop()
    return {"ok": len(results) == len(sequence) and all(r["ok"] for r in results),
            "same_thread": True, "resumed_old_provider_thread": resume_after_first,
            "provider_id": provider_id, "turns": results}
