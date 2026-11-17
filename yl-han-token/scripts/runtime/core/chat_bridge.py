"""Local Responses-to-Chat Completions adapter, with no third-party dependencies.

Only text and function tools are translated. Unsupported capabilities fail
explicitly. Upstream credentials stay in environment/HKCU, never in settings.
"""
import copy
import gzip
import hashlib
import hmac
import json
import os
import pathlib
import threading
import time
import urllib.error
import urllib.request
import uuid
import ssl
import socket
import re
import zlib
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

_BUILD = hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()


class ModelVerificationError(ValueError):
    def __init__(self, reported=None):
        super().__init__("Upstream model identity was not verified")
        self.reported = reported


def verified_response_prefix(remote, expected):
    """Buffer the first model-bearing event; never deliver a mismatched stream."""
    pending = bytearray()
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if remote.headers.get("Content-Encoding") == "gzip" else None
    event_buffer = b""
    stream = remote.headers.get_content_type() == "text/event-stream"
    while len(pending) <= 2_000_000:
        chunk = remote.read1(65536)
        if not chunk:
            break
        pending.extend(chunk)
        event_buffer += decoder.decompress(chunk) if decoder else chunk
        if stream:
            while b"\n" in event_buffer:
                line, event_buffer = event_buffer.split(b"\n", 1)
                if not line.startswith(b"data:"):
                    continue
                try:
                    event = json.loads(line[5:])
                    reported = event.get("response", {}).get("model")
                except (ValueError, AttributeError):
                    continue
                if reported:
                    if reported != expected:
                        raise ModelVerificationError(reported)
                    return bytes(pending), reported
        else:
            try:
                value = json.loads(event_buffer)
            except ValueError:
                continue
            reported = value.get("model") if isinstance(value, dict) else None
            if reported != expected:
                raise ModelVerificationError(reported)
            return bytes(pending), reported
    raise ModelVerificationError()


def network_error_details(error):
    cause = getattr(error,"reason",error)
    code = getattr(cause,"winerror",None) or getattr(cause,"errno",None)
    kind = "connection_failed"
    if isinstance(cause,(TimeoutError,socket.timeout)):
        kind = "timeout"
    elif isinstance(cause,ssl.SSLCertVerificationError):
        kind = "certificate_verification_failed"
    elif isinstance(cause,socket.gaierror):
        kind = "dns_failed"
    elif isinstance(cause,PermissionError) or code in (13,10013):
        kind = "network_access_denied"
    elif isinstance(cause,ConnectionRefusedError):
        kind = "connection_refused"
    return {"kind":kind,"cause":type(cause).__name__,"code":code}


def read_key(name):
    value = None
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value = winreg.QueryValueEx(key, name)[0]
        except FileNotFoundError:
            pass
    if not value:
        value = os.environ.get(name)
    if not value:
        raise ValueError("Upstream credential is missing")
    return value


def flat_name(namespace, name):
    if not namespace:
        return name
    value = namespace + "__" + name
    return value if len(value) <= 64 else value[:47] + "_" + hashlib.sha256(value.encode()).hexdigest()[:16]


def translate_request(body, *, optional_web_search=False, upstream_model=None):
    tools, mapping = [], {}
    def add_tool(tool, namespace=None):
        kind = tool.get("type")
        if kind == "namespace":
            for child in tool.get("tools", []):
                add_tool(child, tool["name"])
            return
        if kind in ("web_search", "web_search_preview") and optional_web_search:
            choice = body.get("tool_choice")
            if isinstance(choice, dict) and choice.get("type") in ("web_search", "web_search_preview"):
                raise ValueError("Explicit web search is unavailable on this Chat model")
            return
        if kind not in ("function", "custom"):
            raise ValueError("Unsupported tool type: " + str(kind))
        name = flat_name(namespace, tool["name"])
        target = (namespace, tool["name"], kind)
        if name in mapping and mapping[name] != target:
            raise ValueError("Flattened tool name collision")
        mapping[name] = target
        parameters = tool.get("parameters", {"type":"object","properties":{}})
        if kind == "custom":
            parameters = {"type":"object", "properties":{"input":{"type":"string"}},
                          "required":["input"],"additionalProperties":False}
        function = {"name":name,"description":tool.get("description", ""),"parameters":parameters}
        tools.append({"type":"function", "function":function})
    inputs = body.get("input", [])
    current_tools = list(body.get("tools") or [])
    if not current_tools and isinstance(inputs, list):
        for item in inputs:
            if item.get("type") == "additional_tools":
                current_tools.extend(item.get("tools", []))
    for tool in current_tools:
        add_tool(tool)
    messages = []
    if body.get("instructions"):
        messages.append({"role":"system", "content":body["instructions"]})
    if optional_web_search and any(t.get("type") in ("web_search", "web_search_preview")
                                   for t in current_tools):
        messages.append({"role":"system", "content":
                         "Built-in web search is unavailable for this model. Do not claim to have searched the web."})
    if isinstance(inputs, str):
        inputs = [{"role":"user", "content":inputs}]
    for item in inputs:
        kind = item.get("type", "message")
        if kind in ("reasoning", "compaction", "configuration_update", "additional_tools"):
            continue
        if kind in ("function_call", "custom_tool_call"):
            name = flat_name(item.get("namespace"), item["name"])
            arguments = item.get("arguments", "{}")
            if kind == "custom_tool_call":
                arguments = json.dumps({"input":item.get("input", "")})
            if not messages or messages[-1].get("role") != "assistant" or "tool_calls" not in messages[-1]:
                messages.append({"role":"assistant","content":None,"tool_calls":[]})
            messages[-1]["tool_calls"].append({"id":item["call_id"], "type":"function",
                "function":{"name":name,"arguments":arguments}})
        elif kind in ("function_call_output", "custom_tool_call_output"):
            output = item.get("output", "")
            messages.append({"role":"tool","tool_call_id":item["call_id"],
                             "content":output if isinstance(output,str) else json.dumps(output,ensure_ascii=False)})
        elif kind == "message":
            content = item.get("content", "")
            if isinstance(content,list):
                texts = []
                for part in content:
                    if part.get("type") not in ("input_text", "output_text", "text"):
                        raise ValueError("Only text inputs are supported by this bridge")
                    texts.append(part.get("text", ""))
                content = "\n".join(texts)
            role = item.get("role", "user")
            messages.append({"role":"system" if role == "developer" else role,"content":content})
        elif kind == "web_search_call" and optional_web_search:
            messages.append({"role":"assistant", "content":
                             "Earlier web search activity: " + json.dumps(item.get("action", {}), ensure_ascii=False)})
        else:
            raise ValueError("Unsupported input item type: " + kind)
    if body.get("previous_response_id"):
        raise ValueError("Send full input history; previous_response_id is not supported")
    request = {"model":body["model"], "messages":messages,"stream":False}
    if tools:
        request["tools"] = tools
    choice = body.get("tool_choice")
    if isinstance(choice, str) and choice in ("auto", "none", "required"):
        request["tool_choice"] = choice
    elif isinstance(choice, dict) and choice.get("type") == "function":
        request["tool_choice"] = {"type":"function","function":{
            "name":flat_name(choice.get("namespace"),choice["name"])}}
    for key in ("temperature", "top_p"):
        if key in body:
            request[key] = body[key]
    if upstream_model:
        request["model"] = upstream_model
    # Provider token limits differ. Do not invent a context/output limit.
    return request, mapping


def translate_response(data, model, mapping):
    if data.get("error") or not data.get("choices"):
        raise ValueError("Upstream did not return a Chat Completions result")
    choice = data["choices"][0]
    if choice.get("finish_reason") not in ("stop", "tool_calls", "function_call"):
        raise ValueError("Upstream response did not finish successfully")
    message, output = choice.get("message", {}), []
    if message.get("content"):
        output.append({"type":"message","id":"msg_"+uuid.uuid4().hex,"status":"completed",
            "role":"assistant","content":[{"type":"output_text","text":message["content"],"annotations":[]}]})
    for call in message.get("tool_calls", []):
        function = call["function"]
        if function["name"] not in mapping:
            raise ValueError("Upstream called an unknown tool")
        namespace, name, kind = mapping[function["name"]]
        item = {"type":"function_call","id":"fc_"+uuid.uuid4().hex,"status":"completed",
                "call_id":call["id"],"name":name,"arguments":function.get("arguments", "{}")}
        if namespace:
            item["namespace"] = namespace
        if kind == "custom":
            item["type"] = "custom_tool_call"
            item["input"] = json.loads(item.pop("arguments"))["input"]
        output.append(item)
    if not output:
        raise ValueError("Upstream response contains no text or tool calls")
    usage = data.get("usage", {})
    return {"id":"resp_"+uuid.uuid4().hex,"object":"response","created_at":int(time.time()),
            "status":"completed","model":model,"output":output,"error":None,"incomplete_details":None,
            "usage":{"input_tokens":usage.get("prompt_tokens",0),"output_tokens":usage.get("completion_tokens",0),
                     "total_tokens":usage.get("total_tokens",0)}}


def response_events(response):
    running = copy.deepcopy(response)
    running.update(status="in_progress",output=[],usage=None)
    yield {"type":"response.created","response":running}
    yield {"type":"response.in_progress","response":running}
    for index,item in enumerate(response["output"]):
        initial = copy.deepcopy(item)
        initial["status"] = "in_progress"
        if item["type"] == "message":
            initial["content"] = []
        elif item["type"] == "function_call":
            initial["arguments"] = ""
        elif item["type"] == "custom_tool_call":
            initial["input"] = ""
        yield {"type":"response.output_item.added","output_index":index,"item":initial}
        common = {"item_id":item["id"],"output_index":index}
        if item["type"] == "message":
            for content_index,part in enumerate(item["content"]):
                details = dict(common,content_index=content_index)
                yield dict(details,type="response.content_part.added",part={"type":"output_text","text":"","annotations":[]})
                yield dict(details,type="response.output_text.delta",delta=part["text"])
                yield dict(details,type="response.output_text.done",text=part["text"])
                yield dict(details,type="response.content_part.done",part=part)
        elif item["type"] == "function_call":
            yield dict(common,type="response.function_call_arguments.delta",delta=item["arguments"])
            yield dict(common,type="response.function_call_arguments.done",arguments=item["arguments"])
        elif item["type"] == "custom_tool_call":
            yield dict(common,type="response.custom_tool_call_input.delta",delta=item["input"])
            yield dict(common,type="response.custom_tool_call_input.done",input=item["input"])
        yield {"type":"response.output_item.done","output_index":index,"item":item}
    yield {"type":"response.completed","response":response}


def make_server(routes, port=0, instance="", key_lookup=read_key, auth_env=None, auth_sources=None):
    history = deque(maxlen=24)
    history_lock = threading.Lock()
    def credential(route):
        if route.get("auth_file"):
            value = json.loads(pathlib.Path(route["auth_file"]).read_text(encoding="utf-8"))
            secret = value.get("OPENAI_API_KEY")
            if not isinstance(secret, str) or not secret:
                raise ValueError("Original provider API credential is missing")
            return secret
        return key_lookup(route["env_key"])

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(NoRedirect)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *args):
            pass
        def send_json(self, status, data):
            content = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        def authorized(self, fallback=None):
            candidates = list(auth_sources or [])
            if auth_env:
                candidates.append({"env_key": auth_env})
            if not candidates:
                candidates = [fallback] if fallback else list(routes.values())
            for candidate in candidates:
                try:
                    if hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + credential(candidate)):
                        return True
                except (OSError, ValueError, KeyError):
                    continue
            return False
        def do_GET(self):
            if self.path == "/health":
                with history_lock:
                    recent = list(history)
                self.send_json(200,{"service":"hantoken-chat-bridge","version":1,"instance":instance,
                    "build":_BUILD,"pid":os.getpid(),
                    "inherited_network_disabled":os.environ.get("CODEX_SANDBOX_NETWORK_DISABLED")=="1",
                    "last_success_at":getattr(self.server,"last_success_at",None),
                    "last_error":getattr(self.server,"last_error",None), "recent_routes":recent})
            else:
                self.send_json(404,{"error":{"message":"Not found"}})
        def do_POST(self):
            phase = "request"
            response_started = False
            route_record = None
            try:
                if self.path == "/shutdown":
                    if not self.authorized():
                        self.send_json(401,{"error":{"message":"Unauthorized"}})
                        return
                    self.send_json(200,{"status":"stopping"})
                    threading.Thread(target=self.server.shutdown,daemon=True).start()
                    self.close_connection=True
                    return
                if self.path != "/v1/responses":
                    self.send_json(404,{"error":{"message":"Only /v1/responses is supported"}})
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16_000_000:
                    raise ValueError("Request size is invalid")
                raw = self.rfile.read(length)
                if self.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                    if len(raw) > 16_000_000:
                        raise ValueError("Request size is invalid")
                body = json.loads(raw)
                route = routes.get(body.get("model"))
                if route is None:
                    raise ValueError("Model is not registered on this bridge")
                if not self.authorized(route):
                    self.send_json(401,{"error":{"message":"Unauthorized"}})
                    return
                secret = credential(route)
                route_record = {
                    "requested_model": body["model"], "upstream_model": route.get("model", body["model"]),
                    "upstream_host": urlsplit(route["base_url"]).netloc,
                    "wire_api": route.get("wire_api", "chat"), "at": int(time.time()),
                    "status": "in_progress", "response_model": None,
                }
                if route.get("wire_api") == "responses":
                    phase = "responses_forwarding"
                    body["model"] = route.get("model", body["model"])
                    headers = {"Authorization":"Bearer "+secret,"Content-Type":"application/json",
                               "Accept":"text/event-stream" if body.get("stream") else "application/json"}
                    for name in ("User-Agent", "OpenAI-Beta", "X-Codex-Turn-Metadata",
                                 "X-Codex-Session-Id", "X-Codex-Turn-State"):
                        if self.headers.get(name):
                            headers[name] = self.headers[name]
                    upstream = urllib.request.Request(
                        route["base_url"].rstrip("/") + "/responses",
                        data=json.dumps(body).encode(), headers=headers)
                    with opener.open(upstream, timeout=180) as remote:
                        prefix = b""
                        if route.get("verify_model"):
                            prefix, reported = verified_response_prefix(remote, body["model"])
                            route_record["response_model"] = reported
                        self.send_response(remote.status)
                        response_started = True
                        for name in ("Content-Type", "Content-Encoding", "Cache-Control",
                                     "X-Request-Id", "X-Codex-Turn-State"):
                            if remote.headers.get(name):
                                self.send_header(name, remote.headers[name])
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.close_connection = True
                        if prefix:
                            self.wfile.write(prefix)
                            self.wfile.flush()
                        event_buffer = b""
                        inspect_stream = remote.headers.get_content_type() == "text/event-stream" and not route.get("verify_model")
                        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if inspect_stream and remote.headers.get("Content-Encoding") == "gzip" else None
                        while True:
                            chunk = remote.read1(65536)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            self.wfile.flush()
                            if inspect_stream:
                                event_buffer += decoder.decompress(chunk) if decoder else chunk
                                while b"\n" in event_buffer:
                                    line, event_buffer = event_buffer.split(b"\n", 1)
                                    if line.startswith(b"data:") and len(line) < 1_000_000:
                                        try:
                                            event = json.loads(line[5:])
                                            reported = event.get("response", {}).get("model")
                                            if (isinstance(reported, str)
                                                    and re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", reported)
                                                    and not reported.startswith("sk-")):
                                                route_record["response_model"] = reported
                                        except (ValueError, AttributeError):
                                            pass
                                if len(event_buffer) > 1_000_000:
                                    event_buffer = b""
                    self.server.last_success_at = int(time.time())
                    self.server.last_error = None
                    route_record["status"] = "completed"
                    return
                phase = "request_conversion"
                request,mapping = translate_request(body, optional_web_search=True,
                                                   upstream_model=route.get("model", body["model"]))
                request["model"] = route.get("model", body["model"])
                endpoint = route["base_url"].rstrip('/')+'/chat/completions'
                upstream = urllib.request.Request(endpoint,data=json.dumps(request).encode(),headers={
                    "Authorization":"Bearer "+secret,"Content-Type":"application/json"})
                phase = "upstream_request"
                for attempt in range(2):
                    try:
                        with opener.open(upstream,timeout=90) as remote:
                            data = json.loads(remote.read(16_000_000))
                        error_text = str(data.get("error", "")).lower()
                        if attempt == 0 and "queue is full" in error_text:
                            time.sleep(0.3)
                            continue
                        break
                    except urllib.error.HTTPError as error:
                        if attempt == 0 and error.code in (429,502,503,504):
                            time.sleep(0.3)
                            continue
                        raise
                phase = "response_conversion"
                reported = data.get("model")
                if isinstance(reported, str) and re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", reported) and not reported.startswith("sk-"):
                    route_record["response_model"] = reported
                if route.get("verify_model") and reported != request["model"]:
                    route_record["status"] = "model_unverified" if not reported else "model_mismatch"
                    raise ValueError("Upstream model identity does not match the requested model")
                result = translate_response(data,body["model"],mapping)
                self.server.last_success_at = int(time.time())
                self.server.last_error = None
                if body.get("stream"):
                    self.send_response(200)
                    self.send_header("Content-Type","text/event-stream")
                    self.send_header("Cache-Control","no-cache")
                    self.send_header("Connection","close")
                    self.end_headers()
                    for sequence,event in enumerate(response_events(result)):
                        event["sequence_number"] = sequence
                        self.wfile.write(("event: "+event["type"]+"\ndata: "+json.dumps(event)+"\n\n").encode())
                    self.wfile.flush()
                    self.close_connection = True
                else:
                    self.send_json(200,result)
                route_record["status"] = "completed"
            except urllib.error.HTTPError as error:
                self.server.last_error={"phase":phase,"exception":"HTTPError","http_status":error.code}
                if route_record is not None:
                    route_record["http_status"] = error.code
                    route_record["status"] = {401: "authentication_failed", 403: "access_denied"}.get(error.code, "upstream_http_error")
                # Never relay upstream bodies: they may include credentials/prompts.
                detail = {401: "Upstream rejected its credential; check this provider's API key and account.",
                          403: "Upstream denied access; check provider permissions or gateway access rules."}.get(error.code, "")
                self.send_json(error.code,{"error":{"message":"Model upstream returned HTTP %s. %s" % (error.code, detail),
                                             "type":"upstream_error"}})
                error.close()
            except (OSError,ValueError,KeyError,TypeError) as error:
                self.server.last_error={"phase":phase,"exception":type(error).__name__}
                if isinstance(error, ModelVerificationError) and route_record:
                    reported = error.reported
                    if isinstance(reported, str) and re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", reported) and not reported.startswith("sk-"):
                        route_record["response_model"] = reported
                    route_record["status"] = "model_mismatch" if reported else "model_unverified"
                model_issue = route_record and route_record["status"] in ("model_mismatch", "model_unverified")
                if model_issue:
                    self.server.last_error["kind"] = route_record["status"]
                if phase == "request_conversion":
                    # Structural diagnostics only; never save prompts, schemas or credentials.
                    inputs = body.get("input", [])
                    self.server.last_error["shape"] = {
                        "tool_types": sorted({str(t.get("type")) for t in body.get("tools", [])}),
                        "input_types": sorted({str(i.get("type", "message")) for i in inputs
                                               if isinstance(i, dict)}) if isinstance(inputs, list) else ["string"],
                        "content_types": sorted({str(p.get("type")) for i in inputs if isinstance(i, dict)
                                                 and isinstance(i.get("content"), list)
                                                 for p in i["content"] if isinstance(p, dict)})
                                         if isinstance(inputs, list) else [],
                        "previous_response": bool(body.get("previous_response_id")),
                    }
                if isinstance(error,OSError):
                    self.server.last_error.update(network_error_details(error))
                if not response_started:
                    if model_issue:
                        # A deterministic identity mismatch will not recover by
                        # reconnecting. Keep it non-retryable and explain the IDs.
                        reported = route_record.get("response_model") or "(missing)"
                        message = ("Selected model %s, but upstream reported %s; response rejected. "
                                   "Choose a model actually supported by this provider."
                                   % (route_record["requested_model"], reported))
                        self.send_json(400, {"error": {"message": message,
                                                      "type": "invalid_request_error",
                                                      "code": route_record["status"]}})
                    else:
                        message = "Model request failed during %s (%s)" % (phase,type(error).__name__)
                        self.send_json(502,{"error":{"message":message,
                                                     "type":"bridge_error"}})
            finally:
                if route_record is not None:
                    if route_record["status"] == "in_progress":
                        route_record["status"] = "failed"
                    with history_lock:
                        history.append(route_record)
    return ThreadingHTTPServer(("127.0.0.1",port),Handler)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings",type=pathlib.Path,required=True)
    parser.add_argument("--port",type=int,required=True)
    args = parser.parse_args()
    config = json.loads(args.settings.read_text(encoding="utf-8"))
    make_server(config["routes"],args.port,config.get("instance", ""),
                auth_env=config.get("auth_env"), auth_sources=config.get("auth_sources")).serve_forever()
