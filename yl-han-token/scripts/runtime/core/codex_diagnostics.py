"""Read-only route evidence; never infer backend identity from generated text."""
import datetime
import json
import urllib.request


def routing_report(home):
    from agents.codex import _load
    from core.desktop_router import uses_router

    _, document = _load(home / "config.toml")
    path = home / "hantoken_bridge/hantoken_desktop_router.json"
    lines = ["配置默认模型：%s（当前聊天的选择以实际请求为准）" % document.get("model", "未设置")]
    if not path.exists():
        return "\n".join(lines + ["尚未安装两组模型入口，无法读取调用记录。"])
    settings = json.loads(path.read_text(encoding="utf-8"))
    active = uses_router(document, settings)
    lines.append("两组模型入口：%s" % ("已配置" if active else "未启用；当前配置直连原服务商"))
    if not active:
        lines.append("下方仅为该入口的历史记录，不能代表当前直连请求。")
    url = "http://127.0.0.1:%d/health" % settings["port"]
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            health = json.loads(response.read(128000))
        if health.get("service") != "hantoken-chat-bridge" or health.get("instance") != settings["instance"]:
            return "\n".join(lines + ["入口实例不匹配，无法确认调用记录。"])
    except (OSError, ValueError):
        return "\n".join(lines + ["本地入口未运行或无法连接，无法确认实际调用。"])
    rows = health.get("recent_routes", [])
    last_error = health.get("last_error") or {}
    if last_error.get("http_status") in (401, 403):
        lines.append("入口最近上游错误：HTTP %s（%s）" % (last_error["http_status"],
            "认证被拒绝，请检查原服务商密钥及账号" if last_error["http_status"] == 401
            else "访问被拒绝，请检查服务商权限或访问限制"))
    if not rows:
        lines.append("入口启动后暂无调用记录。")
    for row in rows[-12:]:
        at = datetime.datetime.fromtimestamp(row["at"], datetime.timezone.utc).astimezone().strftime("%m-%d %H:%M:%S")
        lines.append("%s | 选择 %s → 发送 %s → 返回 %s | %s | %s" % (
            at, row["requested_model"], row["upstream_model"], row.get("response_model") or "未确认",
            row["upstream_host"], row["status"] + (" HTTP %s" % row["http_status"] if row.get("http_status") else "")))
    lines.append("这些记录核对 API 字段；模型自称不作为调用凭据，也不能据此鉴定第三方内部模型。")
    return "\n".join(lines)
