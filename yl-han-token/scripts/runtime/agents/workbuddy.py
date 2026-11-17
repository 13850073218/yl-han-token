# -*- coding: utf-8 -*-
"""WorkBuddy 适配器：直接维护本地 models.json。

与 Trae CN 的区别：WorkBuddy 桌面版以本地文件为权威数据源，GUI 添加模型时
会写入 %USERPROFILE%\\.workbuddy\\models.json；客户端对该文件有 watch + 1 秒
防抖热重载，因此不需要 CDP / Playwright，也不要求关闭客户端。

客户端读取逻辑（app.asar 实测）：
    extractLocalModels(x)   数组直接使用；对象则取 x["models"]
    saveLocalCustomModel    按 id 命中则替换，否则追加
    路径优先级              WORKBUDDY_CONFIG_DIR > CODEBUDDY_CONFIG_DIR > ~/.workbuddy

写入规则：
    同一模型只保留 1 条 —— 以去掉首尾空格的 id 识别，重复时保留最后一条；
    内容完全相同的多余副本也会清除。不同模型（例如 Hy4 与 Hy3，即使 url / apiKey
    等字段完全一样）各自保留 1 条，绝不合并。
    其余模型条目、原有可选字段、顶层结构一律保留。
    支持一次写入多个模型（apply_many）：批量内的重复模型同样只留 1 条，
    并且整批只做一次备份 + 一次原子写，不会反复重写文件。
    写入前备份，原子替换（UTF-8 无 BOM / LF / 2 空格缩进），写后回读校验。
    日志与提示均不输出 API Key。
"""

import datetime
import copy
import json
import os
import pathlib
import shutil
import subprocess
import tempfile

from agents.base import AgentAdapter
from core.schema import (
    OPTIONAL_BOOL_FIELDS,
    OPTIONAL_NUMBER_FIELDS,
    OPTIONAL_OBJECT_FIELDS,
)

PROCESS_NAME = "WorkBuddy.exe"

# 与 WorkBuddy GUI「添加模型」表单默认值保持一致
DEFAULT_MODEL_FIELDS = {
    "supportsToolCall": True,
    "supportsImages": False,
    "supportsReasoning": False,
    "useCustomProtocol": False,
}

_BASE_FIELDS = ("id", "name", "vendor", "url", "apiKey")
_OPTIONAL_ORDER = OPTIONAL_NUMBER_FIELDS + OPTIONAL_BOOL_FIELDS + OPTIONAL_OBJECT_FIELDS


def config_dir():
    """返回 WorkBuddy 配置目录（环境变量优先，与客户端 getUserConfigPath 一致）。"""
    for name in ("WORKBUDDY_CONFIG_DIR", "CODEBUDDY_CONFIG_DIR"):
        value = os.environ.get(name, "").strip()
        if value:
            return pathlib.Path(value)
    return pathlib.Path.home() / ".workbuddy"


def config_path():
    return config_dir() / "models.json"


def _find_client_exe():
    candidates = [
        os.environ.get("WORKBUDDY_EXE", ""),
        os.path.join(
            os.environ.get("LOCALAPPDATA", ""), "Programs", "WorkBuddy", "WorkBuddy.exe"
        ),
        r"C:\Program Files\Tencent\WorkBuddy\WorkBuddy.exe",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def _load(path):
    """读取现有配置，返回 (顶层类型, 容器对象)。文件损坏时抛错，绝不覆盖。"""
    if not path.exists():
        return "list", []
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as e:
        raise RuntimeError("无法读取现有 models.json：%s" % e)
    if not raw.strip():
        return "list", []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            "现有 models.json 不是合法 JSON（%s），已停止写入以免破坏配置；请先手动修复。" % e
        )
    if isinstance(data, list):
        return "list", data
    if isinstance(data, dict):
        if "models" not in data:
            data["models"] = []
        if not isinstance(data["models"], list):
            raise RuntimeError("现有 models.json 的 models 字段不是数组，已停止写入。")
        return "dict", data
    raise RuntimeError("现有 models.json 顶层必须是数组或对象，已停止写入。")


def _signature(item):
    return json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)


def _model_key(item):
    """以区分大小写的模型 ID 为唯一键；缺失或空 ID 不参与同模型合并。"""
    model_id = item.get("id")
    return model_id.strip() if isinstance(model_id, str) else ""


def _dedupe(models):
    """按 ID 唯一化并清除内容完全相同的条目。

    返回 (结果, 同模型清理数, 完全相同清理数)。同一模型出现多次时保留最后一条
    （后写入的通常更新）；非对象条目原样保留。
    """
    out = []
    index_by_key = {}
    seen_sig = set()
    removed_same_model = 0
    removed_exact = 0
    for item in models:
        if not isinstance(item, dict):
            out.append(item)
            continue
        sig = _signature(item)
        key = _model_key(item)
        if key and key in index_by_key:
            previous = out[index_by_key[key]]
            out[index_by_key[key]] = item
            if _signature(previous) == sig:
                removed_exact += 1
            else:
                removed_same_model += 1
            continue
        if not key and sig in seen_sig:
            removed_exact += 1
            continue
        seen_sig.add(sig)
        pos = len(out)
        out.append(item)
        if key:
            index_by_key[key] = pos
    return out, removed_same_model, removed_exact


def _build_entry(cfg, old):
    """构造最终条目：以粘贴配置为准，已有条目的额外字段与默认值不丢失。"""
    merged = dict(old) if isinstance(old, dict) else {}
    merged["id"] = cfg["id"]
    merged["name"] = cfg["name"]
    merged["vendor"] = cfg.get("vendor") or "Custom"
    merged["url"] = cfg["url"]
    merged["apiKey"] = cfg["api_key"]
    merged.update(cfg.get("extras") or {})
    for key, value in DEFAULT_MODEL_FIELDS.items():
        merged.setdefault(key, value)

    # 按 GUI 写出的字段顺序排列，便于人工核对
    ordered = {key: merged[key] for key in _BASE_FIELDS}
    for key in _OPTIONAL_ORDER:
        if key in merged:
            ordered[key] = merged[key]
    for key, value in merged.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def _backup(path):
    if not path.exists():
        return None
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name("models.json.bak-" + stamp)
    seq = 1
    while candidate.exists():
        candidate = path.with_name("models.json.bak-%s-%d" % (stamp, seq))
        seq += 1
    shutil.copy2(str(path), str(candidate))
    return candidate


def _atomic_write(path, data):
    fd, tmp = tempfile.mkstemp(prefix="models-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _verify(path, targets):
    """回读确认每个目标模型 ID 恰好出现 1 次。"""
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    models = data if isinstance(data, list) else data.get("models")
    if not isinstance(models, list):
        raise RuntimeError("写入后校验失败：models 不是数组，请用备份文件恢复。")
    for model_id in targets:
        target = model_id.strip()
        hits = [m for m in models if isinstance(m, dict) and _model_key(m) == target]
        if len(hits) != 1:
            raise RuntimeError(
                "写入后校验失败：模型 %s 出现 %d 次，请用备份文件恢复。"
                % (model_id, len(hits))
            )
    return len(models)


class WorkBuddyAdapter(AgentAdapter):
    display_name = "WorkBuddy"

    def detect(self):
        executable = _find_client_exe()
        if executable:
            return True, "已找到 WorkBuddy：%s，配置：%s" % (executable, config_path())
        return False, "未找到 WorkBuddy 客户端，请先安装或设置 WORKBUDDY_EXE 指向 WorkBuddy.exe"

    def running_processes(self):
        """仅用于日志提示；WorkBuddy 支持文件热重载，不需要关闭客户端。"""
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", "IMAGENAME eq %s" % PROCESS_NAME, "/NH"],
                stderr=subprocess.DEVNULL,
                timeout=10,
            ).decode("gbk", "replace")
        except Exception:
            return []
        return [line for line in out.splitlines() if line.lower().startswith(PROCESS_NAME.lower())]

    def apply(self, model_config):
        """写入单个模型（保留单条调用接口）。"""
        return self.apply_many([model_config])

    def apply_many(self, model_configs):
        """一次写入多个模型：单次读取、单次备份、单次原子写、单次校验。"""
        self.require_installed()
        raw_cfgs = list(model_configs or [])
        if not raw_cfgs:
            raise RuntimeError("没有可写入的模型配置")

        # 本次粘贴里同一模型只处理一次（保留最后一条），日志与写入结果保持一致
        by_key = {}
        for cfg in raw_cfgs:
            by_key[_model_key(cfg)] = cfg
        cfgs = list(by_key.values())
        repeated = len(raw_cfgs) - len(cfgs)

        path = config_path()
        if not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)

        kind, container = _load(path)
        original = copy.deepcopy(container)
        models = container if kind == "list" else container["models"]
        models, dup_same_model, dup_exact = _dedupe(models)

        actions = []
        for cfg in cfgs:
            target_key = _model_key(cfg)
            pos = next(
                (
                    i
                    for i, item in enumerate(models)
                    if isinstance(item, dict) and _model_key(item) == target_key
                ),
                None,
            )
            old = models[pos] if pos is not None else None
            entry = _build_entry(cfg, old)
            if pos is None:
                models.append(entry)
                actions.append(("新增", cfg))
            else:
                models[pos] = entry
                actions.append(("更新", cfg))

        # 兜底再跑一次：批量内重复的同一模型也只保留 1 条
        models, more_model, more_exact = _dedupe(models)
        dup_same_model += more_model
        dup_exact += more_exact

        appended_ids = []
        if kind == "list":
            output = models
        else:
            output = container
            output["models"] = models
            # availableModels 非空时是白名单，不追加新 id 会导致模型不显示
            available = output.get("availableModels")
            if isinstance(available, list) and available:
                for cfg in cfgs:
                    if cfg["id"] not in available:
                        available.append(cfg["id"])
                        appended_ids.append(cfg["id"])

        if path.exists() and output == original:
            total = _verify(path, [cfg["id"] for cfg in cfgs])
            return (
                "WorkBuddy 的 %d 个自定义模型配置未变化，已校验；跳过写入和备份（当前共 %d 个模型）。\n配置文件：%s"
                % (len(cfgs), total, path)
            )

        backup = _backup(path)
        try:
            _atomic_write(path, output)
            total = _verify(path, [cfg["id"] for cfg in cfgs])
        except Exception as e:
            raise RuntimeError(
                "写入 %s 失败：%s%s"
                % (path, e, ("；原文件备份在 %s" % backup.name) if backup else "")
            )

        if len(cfgs) == 1:
            action, cfg = actions[0]
            lines = [
                "已%s模型「%s」到 WorkBuddy（按 ID 识别，同一模型只保留 1 条；当前共 %d 个模型）。"
                % (action, cfg["name"], total)
            ]
        else:
            added = sum(1 for act, _ in actions if act == "新增")
            lines = [
                "已批量写入 %d 个模型到 WorkBuddy（新增 %d / 更新 %d；同一模型只保留 1 条；当前共 %d 个模型）。"
                % (len(cfgs), added, len(actions) - added, total)
            ]
            for action, cfg in actions:
                lines.append("  · %s %s" % (action, cfg["name"]))
        lines.append("配置文件：%s" % path)

        if repeated:
            lines.append("粘贴内容含 %d 个重复模型，已各合并为 1 条。" % repeated)
        if dup_same_model or dup_exact:
            lines.append(
                "已清理重复记录 %d 条（同模型 %d / 内容完全相同 %d）。"
                % (dup_same_model + dup_exact, dup_same_model, dup_exact)
            )
        lines.append(
            "原文件已备份为 %s。" % backup.name if backup else "原文件不存在，已新建 models.json。"
        )
        if appended_ids:
            lines.append(
                "已把 %d 个模型 ID 追加到 availableModels，确保下拉框可见。" % len(appended_ids)
            )
        if self.running_processes():
            lines.append("WorkBuddy 正在运行，配置约 1 秒后自动热加载；若列表未刷新请重启客户端。")
        else:
            lines.append("启动 WorkBuddy 后，在 设置 -> 模型 或对话页模型选择器中确认。")
        return "\n".join(lines)
