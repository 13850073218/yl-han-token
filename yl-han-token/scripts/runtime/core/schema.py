# -*- coding: utf-8 -*-
"""模型配置 JSON 的解析与校验。

支持两种粘贴格式：
1) 单个模型对象（只需 3 个字段）
{
    "id": "qwen3.7-flash",
    "url": "https://api.hanrouter.ai/v1",
    "apiKey": "sk-..."
}
2) 模型数组（批量写入，不同模型如 Hy4 / Hy3 各自保留一条）
[ { ... }, { ... } ]

可省略字段：
    name    缺省取 id 的值
    vendor  缺省为 "Custom"
兼容复制文本中的 Markdown 字段标记、代码围栏和行尾续行符。
url 支持 Markdown 链接，提取链接目标后去掉首尾空格。其余地址内容以传入值为准，
工具不会自动拼接 `/v1` 或 `/chat/completions`，也不改写路径、尾斜杠、
查询参数或片段。不同 Agent 需要的地址不同，由粘贴的配置自行决定。

可选字段（仅 WorkBuddy 适配器使用，Trae CN 会忽略）：
    maxInputTokens / maxOutputTokens / temperature /
    supportsToolCall / supportsImages / supportsReasoning /
    onlyReasoning / useCustomProtocol / reasoning

Codex 专用可选对象 codex（其他 Agent 忽略）：
    providerId / profile / envKey / wireApi / setDefault / profileFormat
"""

import json
import re
from urllib.parse import urlsplit


class SchemaError(ValueError):
    """配置内容不合法时抛出。"""


REQUIRED_FIELDS = ("id", "url", "apiKey")

# 可选字段白名单：类型不符时直接报错，避免静默丢弃用户配置
OPTIONAL_NUMBER_FIELDS = ("maxInputTokens", "maxOutputTokens", "temperature")
OPTIONAL_BOOL_FIELDS = (
    "supportsToolCall",
    "supportsImages",
    "supportsReasoning",
    "onlyReasoning",
    "useCustomProtocol",
)
OPTIONAL_OBJECT_FIELDS = ("reasoning",)
OPTIONAL_FIELDS = OPTIONAL_NUMBER_FIELDS + OPTIONAL_BOOL_FIELDS + OPTIONAL_OBJECT_FIELDS

# vendor 别名 -> 归一化的 provider 标识
VENDOR_MAP = {
    "custom": "custom_openai_compatible",
    "openai": "custom_openai_compatible",
    "openai compatible": "custom_openai_compatible",
    "openai_compatible": "custom_openai_compatible",
    "anthropic": "custom_anthropic_compatible",
}


def _codex_options(data):
    if "codex" not in data:
        return {}
    options = data["codex"]
    if not isinstance(options, dict):
        raise SchemaError("字段 'codex' 必须是对象 {...}")
    allowed = {"providerId", "profile", "envKey", "wireApi", "setDefault", "profileFormat"}
    if set(options) - allowed:
        raise SchemaError("codex 包含未知字段；支持 providerId / profile / envKey / wireApi / setDefault / profileFormat")
    result = {}
    for field in ("providerId", "profile", "envKey", "wireApi", "profileFormat"):
        if field not in options:
            continue
        value = options[field]
        if not isinstance(value, str) or not value.strip():
            raise SchemaError("codex.%s 必须是非空字符串" % field)
        result[field] = value.strip()
    for field in ("providerId", "profile"):
        if field in result and not re.fullmatch(r"hantoken_[a-z0-9_]{1,80}", result[field]):
            raise SchemaError("codex.%s 必须以 hantoken_ 开头，仅含小写字母、数字或下划线，最长 88 字符" % field)
    if "envKey" in result and not re.fullmatch(r"HANTOKEN_CODEX_[A-Z0-9_]{1,100}", result["envKey"]):
        raise SchemaError("codex.envKey 必须以 HANTOKEN_CODEX_ 开头，仅含大写字母、数字或下划线")
    if result.get("wireApi", "auto") not in ("auto", "responses", "chat"):
        raise SchemaError("codex.wireApi 必须为 auto / responses / chat；chat 通过本地转换接入 Codex")
    if result.get("profileFormat", "auto") not in ("auto", "files", "legacy"):
        raise SchemaError("codex.profileFormat 必须是 auto / files / legacy")
    if "setDefault" in options:
        if not isinstance(options["setDefault"], bool):
            raise SchemaError("codex.setDefault 必须是 true / false")
        result["setDefault"] = options["setDefault"]
    return result


def validate_codex_url(value):
    """Validate URLs only for Codex; keep existing agents compatible."""
    try:
        url = urlsplit(value)
        if url.scheme not in ("http", "https") or not url.hostname:
            raise ValueError
        if url.username is not None or url.password is not None or url.query or url.fragment:
            raise ValueError
        url.port
    except ValueError:
        raise SchemaError("Codex url 必须是有效的 HTTP(S) 基础地址，且不能包含账号密码、查询参数或片段") from None
    if url.path.rstrip("/").endswith(("/responses", "/chat/completions")):
        raise SchemaError("Codex url 应为基础地址（例如 https://example.com/v1），不要包含 /responses 或 /chat/completions")


def _normalize_url(raw_url):
    """去掉空格及 Markdown 链接包装，保留实际地址。

    工具不自动拼接 /v1 或 /chat/completions，也不改写路径、尾斜杠、
    query/fragment。不同 Agent 对地址的要求不同（WorkBuddy 需要完整的
    /chat/completions 端点，Codex 需要不含请求路径的 base url），
    一律由粘贴的配置自行决定，避免"一个加上能用、另一个加上不能用"。

    例：
        " https://apihub.agnes-ai.com/v1 "
            -> "https://apihub.agnes-ai.com/v1"
        "https://apihub.agnes-ai.com/v1/chat/completions"
            -> "https://apihub.agnes-ai.com/v1/chat/completions"
        "https://a.com/v1/?foo=1#bar" -> "https://a.com/v1/?foo=1#bar"
    """
    value = raw_url.strip()
    link = re.fullmatch(r"\[[^\]\r\n]*\]\((https?://[^\s]+)\)", value)
    return link.group(1) if link else value


_PASTE_TOKENS = re.compile(
    r'"(?:\\.|[^"\\])*"'
    r'|(?P<marks>\*{1,2})(?P<key>"(?:\\.|[^"\\])*")(?P=marks)(?=\s*:)'
    r'|(?P<continuation>\\[ \t]*(?:\r?\n|$))'
    r'|(?P<space>[^\S\r\n]+)'
)


def _clean_pasted_json(text):
    fence = re.fullmatch(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1)

    # Match complete strings first so formatting cleanup cannot alter credentials.
    def replace(match):
        if match.group("key") is not None:
            return match.group("key")
        if match.group("continuation") is not None:
            return "\n"
        if match.group("space") is not None:
            return " "
        return match.group()

    return _PASTE_TOKENS.sub(replace, text)


def _decode(text):
    """把粘贴文本解析为 JSON 值。"""
    text = (text or "").strip()
    if not text:
        raise SchemaError("内容为空，请先粘贴 JSON 配置")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return json.loads(_clean_pasted_json(text))
        except json.JSONDecodeError as error:
            raise SchemaError("JSON 格式错误：%s" % error) from None


def _normalize(data):
    """归一化单个模型对象，校验字段并透传白名单可选字段。

    返回字段：id / name / provider / url / api_key / vendor / extras / codex
    """
    if not isinstance(data, dict):
        raise SchemaError("模型配置必须是对象 {...}")

    for field in REQUIRED_FIELDS:
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            raise SchemaError("字段 %r 缺失或为空" % field)

    model_id = data["id"].strip()

    # name 缺省（或空串）时取 id；显式给出则保留，用于区分同 id 的不同模型
    raw_name = data.get("name")
    if raw_name is None or (isinstance(raw_name, str) and not raw_name.strip()):
        name = model_id
    elif isinstance(raw_name, str):
        name = raw_name.strip()
    else:
        raise SchemaError("字段 'name' 必须是字符串")

    vendor_raw = str(data.get("vendor", "Custom")).strip() or "Custom"
    provider = VENDOR_MAP.get(vendor_raw.lower(), "custom_openai_compatible")

    # 可选字段按白名单透传，类型不符直接报错（Trae CN 不读取 extras）
    extras = {}
    for field in OPTIONAL_NUMBER_FIELDS:
        if field in data:
            value = data[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SchemaError("字段 %r 必须是数字" % field)
            extras[field] = value
    for field in OPTIONAL_BOOL_FIELDS:
        if field in data:
            value = data[field]
            if not isinstance(value, bool):
                raise SchemaError("字段 %r 必须是 true / false" % field)
            extras[field] = value
    for field in OPTIONAL_OBJECT_FIELDS:
        if field in data:
            value = data[field]
            if not isinstance(value, dict):
                raise SchemaError("字段 %r 必须是对象 {...}" % field)
            extras[field] = value

    # 三种 Agent 共用清理后的实际地址，不自动补充请求路径。
    url = _normalize_url(data["url"])

    return {
        "id": model_id,
        "name": name,
        "provider": provider,
        "url": url,
        # 兼容 Codex 适配器的读取；url 不再改写，两者始终一致
        "codex_url": url,
        "api_key": data["apiKey"].strip(),
        # WorkBuddy 写入 models.json 需要保留原始 vendor 与可选字段
        "vendor": vendor_raw,
        "extras": extras,
        "codex": _codex_options(data),
    }


def parse_model_config(text):
    """解析单个模型对象，返回归一化后的 dict（保留单条调用接口）。"""
    return _normalize(_decode(text))


def parse_model_configs(text):
    """解析单个模型对象或模型数组，返回归一化后的 dict 列表。

    GUI 统一走这里；批量时逐条校验并给出带序号的错误提示。
    重复模型不在此处去重，交由适配器按 (id, name) 合并为 1 条。
    """
    data = _decode(text)
    if isinstance(data, dict):
        items = [data]
    elif isinstance(data, list):
        if not data:
            raise SchemaError("模型数组为空")
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                raise SchemaError("第 %d 项必须是模型对象 {...}" % (i + 1))
        items = data
    else:
        raise SchemaError("JSON 顶层必须是模型对象 {...} 或模型数组 [...]")

    results = []
    for i, item in enumerate(items):
        try:
            results.append(_normalize(item))
        except SchemaError as e:
            if len(items) > 1:
                raise SchemaError("第 %d 个模型：%s" % (i + 1, e))
            raise
    return results
