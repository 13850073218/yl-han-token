"""Live compatibility probes; never modify config or return response bodies/secrets."""
import json
import urllib.error
import urllib.request
from core.codex_config import prepare_codex_config


def probe(base_url, model, secret, kind):
    function = {"type": "function", "name": "hantoken_echo",
                "description": "Return the supplied text unchanged.",
                "parameters": {"type": "object", "properties": {"text": {"type": "string"}},
                               "required": ["text"], "additionalProperties": False}}
    # Codex uses SSE; some valid Codex endpoints reject non-streaming requests.
    body = {"model": model, "input": "Reply with OK.", "max_output_tokens": 100,
            "instructions": "You are a helpful assistant. Follow the user's test instruction.",
            "stream": True, "store": False}
    if kind in ("function", "namespace"):
        body["input"] = "Call hantoken_echo exactly once with text OK."
        body["tools"] = [function] if kind == "function" else [
            {"type": "namespace", "name": "hantoken", "description": "Test tools", "tools": [function]}]
        body["tool_choice"] = "required"
    request = urllib.request.Request(base_url.rstrip("/") + "/responses",
        data=json.dumps(body).encode(), headers={"Authorization": "Bearer " + secret,
        "Content-Type": "application/json", "User-Agent": "hantoken-provider-check/1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
            raw = response.read(2_000_000)
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read(2_000_000)
    except (OSError, urllib.error.URLError):
        return {"probe": kind, "ok": False, "reason": "连接失败或超时"}
    if status in (401, 403):
        return {"probe": kind, "ok":False, "http_status":status,
                "reason":"认证失败或服务端拒绝访问"}
    data = None
    looks_like_sse = raw.lstrip().startswith((b"data:", b"event:", b":"))
    if status == 200 and (kind == "stream" or looks_like_sse):
        completed, text, last_error = False, False, None
        try:
            for line in raw.decode("utf-8").splitlines():
                if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                    continue
                event = json.loads(line[5:])
                if event.get("error"):
                    last_error = event["error"]
                if event.get("type") in ("error", "response.failed"):
                    last_error = event.get("error", event.get("response", {}).get("error", {}))
                text |= event.get("type") == "response.output_text.delta" and bool(event.get("delta"))
                completed |= event.get("type") == "response.completed"
                if event.get("type") == "response.completed":
                    data = event.get("response", {})
            if last_error:
                data = {"error":last_error}
            elif kind == "stream":
                return {"probe": kind, "ok": completed and text, "http_status": status,
                        "reason": "通过" if completed and text else "没有收到完整的 Responses 文本事件"}
            elif not completed:
                return {"probe":kind, "ok":False, "http_status":status, "reason":"Responses 流没有完整结束"}
        except (ValueError, UnicodeError):
            return {"probe": kind, "ok": False, "http_status": status, "reason": "流式响应格式不兼容"}
    try:
        if data is None:
            data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError()
    except ValueError:
        return {"probe": kind, "ok": False, "http_status": status, "reason": "响应不是 JSON 对象"}
    output = data.get("output", [])
    if not isinstance(output, list):
        output = []
    calls = [item for item in output if isinstance(item, dict) and item.get("type") == "function_call"
             and item.get("name") == "hantoken_echo"]
    valid_call = False
    for call in calls:
        try:
            valid_call |= json.loads(call.get("arguments", "{}")) == {"text": "OK"} and bool(call.get("call_id"))
        except (ValueError, TypeError):
            pass
    texts = [part.get("text", "") for item in output if isinstance(item, dict)
             for part in item.get("content", []) if isinstance(part, dict) and part.get("type") == "output_text"]
    error = str(data.get("error", "")).lower()
    ok = status == 200 and data.get("status") == "completed" and bool(valid_call if kind != "text" else texts)
    reason = "通过" if ok else "接口未返回有效的完整响应或工具调用"
    if "queue is full" in error:
        reason = "服务端请求队列已满"
    elif "namespace" in error and ("unknown variant" in error or "unsupported" in error):
        reason = "服务端不支持 namespace 工具协议"
    elif status in (401, 403):
        reason = "密钥无效或没有访问权限"
    elif status == 429:
        reason = "服务端限流或额度不足"
    elif "model" in error and ("not found" in error or "not available" in error):
        reason = "服务端不提供该模型或当前密钥无权使用"
    return {"probe": kind, "ok": ok, "http_status": status, "reason": reason}


def check_provider(cfg, *, fail_fast=False):
    cfg = prepare_codex_config(cfg)
    if cfg.get("codex", {}).get("wireApi") == "chat":
        from core.chat_bridge import make_server
        from core.codex_tool_check import check_tool
        import threading
        server = make_server({cfg["id"]:{"base_url":cfg.get("codex_url",cfg["url"]),
            "env_key":"preview"}},key_lookup=lambda name:cfg["api_key"])
        worker = threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            local = "http://127.0.0.1:%d/v1" % server.server_port
            result = check_tool(dict(cfg,url=local,codex_url=local,desktop_tools=True))
            result["probe"] = "chat_bridge"
            result["reason"] = "通过：实际 Codex 命名空间工具调用和后续回复完成" if result["ok"] else (
                "Chat Completions 转换后的实际 Codex 工具任务未通过："+result.get("reason",str(result.get("turn_status","未完成"))))
            return [result]
        finally:
            server.shutdown();server.server_close();worker.join(timeout=3)
    results = []
    for kind in ("text", "function", "stream", "namespace"):
        result = probe(cfg.get("codex_url", cfg["url"]), cfg["id"], cfg["api_key"], kind)
        results.append(result)
        # Activation requires every capability. Once one fails, further paid
        # requests cannot make this activation succeed. Diagnostics keep their
        # full report by default.
        if fail_fast and not result["ok"]:
            break
        if result.get("http_status") in (401, 403) or "http_status" not in result:
            break
    return results


def format_report(results):
    labels = {"text": "普通对话", "function": "函数调用", "stream": "流式响应", "namespace": "namespace 工具",
              "chat_bridge":"Chat Completions 经本地转换"}
    return "\n".join("%s：%s%s" % (labels[r["probe"]], r["reason"],
        "（HTTP %s）" % r["http_status"] if "http_status" in r else "") for r in results)


def require_desktop_compatibility(cfg):
    cfg = prepare_codex_config(cfg)
    results = check_provider(cfg, fail_fast=True)
    expected = 1 if cfg.get("codex", {}).get("wireApi") == "chat" else 4
    if len(results) != expected or not all(r["ok"] for r in results):
        raise RuntimeError("未启用桌面自定义模型，原配置未改动。接口兼容性校验失败：\n"
                           + format_report(results)
                           + "\n可以不勾选桌面启用，仅保存隔离的 CLI 配置方案。")
    return results
