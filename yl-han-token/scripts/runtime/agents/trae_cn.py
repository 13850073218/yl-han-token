# -*- coding: utf-8 -*-
"""Trae CN 适配器：通过 CDP 驱动 Trae 自己的设置界面添加自定义模型。

为什么不用直接写数据库：
    Trae CN 的自定义模型保存在云端账号，启动时会用云端数据重建本地
    state.vscdb 的 model_list_map，本地直接插入的记录会被覆盖。
    且 ak 字段加密逻辑在远程动态加载的 bundle 中，本地不可得。

CDP 方案：
    以 --remote-debugging-port 启动 Trae CN，用 Playwright 连接，
    依次操作：模型选择器 -> 添加模型 -> 自定义模型 -> 填表 -> 提交。
    加密与云端同步均由 Trae 客户端自己完成。

依赖：pip install playwright（仅需连接 CDP，不需要下载浏览器）。
"""

import json
import os
import re
import subprocess
import time
import urllib.request

from agents.base import AgentAdapter

PROCESS_NAME = "Trae CN.exe"
DEBUG_PORT = 9333  # 避开常见的 9222（常被 Chrome 占用）
CDP_BASE = "http://127.0.0.1:%d" % DEBUG_PORT

# Trae CN 默认安装位置；同时允许环境变量覆盖（移植性）
_TRAE_EXE_CANDIDATES = [
    os.environ.get("TRAE_CN_EXE", ""),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Trae CN", "Trae CN.exe"),
    r"C:\Program Files\Trae CN\Trae CN.exe",
]

# 等待 CDP 就绪的超时时间（秒）
_CDP_WAIT_TIMEOUT = 60
_CLOSE_WAIT_TIMEOUT = 180
# 连通性测试（真实请求）等待时间
_SUBMIT_WAIT_TIMEOUT = 45
_DUPLICATE_NOTICE_MS = 1200


def _find_trae_exe():
    for path in _TRAE_EXE_CANDIDATES:
        if path and os.path.isfile(path):
            return path
    return None


def _cdp_alive():
    try:
        with urllib.request.urlopen(CDP_BASE + "/json/version", timeout=3) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        return "Trae" in data.get("User-Agent", "") or "TraeCN" in data.get("Browser", "")
    except Exception:
        return False


class TraeCNAdapter(AgentAdapter):
    display_name = "Trae CN"

    def __init__(self, exe_path=None):
        self.exe_path = exe_path or _find_trae_exe()

    def detect(self):
        if self.exe_path and os.path.isfile(self.exe_path):
            return True, "已找到 Trae CN：%s" % self.exe_path
        return False, "未找到 Trae CN 安装路径，请设置环境变量 TRAE_CN_EXE 指向 Trae CN.exe"

    def running_processes(self):
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", "IMAGENAME eq %s" % PROCESS_NAME, "/NH"],
                stderr=subprocess.DEVNULL,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).decode("gbk", "replace")
        except Exception:
            return []
        return [l for l in out.splitlines() if l.lower().startswith(PROCESS_NAME.lower())]

    def _ensure_debug_session(self, *, wait_for_close=False, on_status=None):
        """确保有一个带调试端口的 Trae CN 实例，返回是否为新启动。"""
        if _cdp_alive():
            return False
        notify = on_status or (lambda message: None)
        if self.running_processes():
            if wait_for_close:
                notify(
                    "等待 Trae CN 退出：请保存工作并完全关闭 Trae（含托盘），保持本配置窗口开启。"
                    "配置已保留；退出后自动启动并继续，无需重新粘贴或点击（最多等待 3 分钟）。"
                )
                deadline = time.monotonic() + _CLOSE_WAIT_TIMEOUT
                while self.running_processes():
                    if _cdp_alive():
                        notify("Trae CN 调试端口已就绪，正在继续导入。")
                        return False
                    if time.monotonic() >= deadline:
                        raise RuntimeError(
                            "等待 Trae CN 退出超时，尚未提交模型。输入框中的配置仍保留；"
                            "请完全退出 Trae（含托盘）后再次点击「校验并写入」。"
                        )
                    time.sleep(0.5)
            else:
                raise RuntimeError(self._external_or_manual_guidance())
        if not self.exe_path:
            raise RuntimeError("未找到 Trae CN 安装路径")
        notify("正在以调试模式启动 Trae CN，窗口就绪后自动继续导入。")
        subprocess.Popen(
            [self.exe_path, "--remote-debugging-port=%d" % DEBUG_PORT],
            close_fds=True, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=(getattr(subprocess, "DETACHED_PROCESS", 0)
                           | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)),
        )
        deadline = time.monotonic() + _CDP_WAIT_TIMEOUT
        while time.monotonic() < deadline:
            if _cdp_alive():
                notify("Trae CN 调试端口已就绪，正在继续导入。")
                return True
            time.sleep(0.25)
        raise RuntimeError("Trae CN 已启动但调试端口未就绪，请重试")

    @staticmethod
    def _external_or_manual_guidance():
        return (
            "检测到 Trae CN 正在运行但未开启调试端口。\n"
            "可留在当前窗口手动添加，无需关闭：模型下拉菜单 → 添加模型 → "
            "模型设置页「+ 添加模型」→ 自定义模型。\n"
            "选择接口实际使用的 API 格式，填写请求地址、模型 ID、可选显示名和 API 密钥；"
            "基础地址模式关闭「完整 URL」，完整请求地址则开启，最后点击「添加模型」等待检查。\n"
            "如需自动配置，请在 Trae CN 之外打开本工具 GUI，粘贴后点击一次「校验并写入」，"
            "保持工具窗口开启，再保存工作并完全关闭 Trae CN（含托盘）；窗口会自动续配。\n"
            "也可完全关闭 Trae 后，从独立 PowerShell 或其他客户端执行导入。\n"
            "如果正在 Trae 自己的聊天窗口中调用技能，关闭 Trae 会中断该次执行，"
            "不能依赖原窗口在关闭后继续配置。"
        )

    def apply(self, model_config):
        return self.apply_many([model_config])

    def apply_many(self, model_configs, *, wait_for_close=False, on_status=None):
        """Reuse one CDP connection for the batch; each submission stays sequential."""
        self.require_installed()
        configs = list(model_configs or [])
        if not configs:
            raise RuntimeError("没有可写入的模型配置")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError("缺少依赖 playwright，请先执行：pip install playwright")

        self._ensure_debug_session(wait_for_close=wait_for_close, on_status=on_status)

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CDP_BASE)
            page = self._pick_workbench_page(browser)

            try:
                page.locator(
                    ":is(.core-model-select-trigger, .solo-lite-settings-overlay, "
                    ".icd-modal-overlay.add-model-dialog):visible"
                ).first.wait_for(state="visible", timeout=_CDP_WAIT_TIMEOUT * 1000)
                results = [self._format_outcome(cfg, self._add_model_via_ui(page, cfg))
                           for cfg in configs]
            except Exception as e:
                from playwright.sync_api import Error as PWError

                if isinstance(e, PWError):
                    raise RuntimeError(
                        "界面自动化失败：未能在 Trae 窗口中找到目标控件。\n"
                        "请确认 Trae CN 处于对话主界面（左侧为会话列表，底部有输入框），"
                        "且没有被其他弹窗遮挡，然后重试。\n"
                        "技术细节：%s" % str(e).splitlines()[0]
                    )
                raise

        return "\n".join(results)

    @staticmethod
    def _format_outcome(model_config, outcome):
        if outcome == "skipped":
            return "模型「%s」已存在，已跳过；原配置保持不变。" % model_config["name"]
        return (
            "已通过 Trae CN 设置界面添加模型「%s」（连通性测试通过）。\n"
            "模型已保存到你的账号，重启客户端不会丢失。\n"
            "请在 设置 -> 模型 或对话页模型选择器中确认。"
            % model_config["name"]
        )

    # ------------------------------------------------------------------ UI

    @staticmethod
    def _visible(locator):
        """安全判断元素是否可见（不存在/查询失败都视为 False）。"""
        try:
            return locator.count() > 0 and locator.first.is_visible()
        except Exception:
            return False

    def _pick_workbench_page(self, browser):
        """从所有 CDP 页面中挑出 Trae 工作台页面。"""
        pages = [pg for ctx in browser.contexts for pg in ctx.pages]
        if not pages and browser.contexts:
            # Pump Playwright events while the new Electron window is created.
            browser.contexts[0].wait_for_event("page", timeout=_CDP_WAIT_TIMEOUT * 1000)
            pages = [pg for ctx in browser.contexts for pg in ctx.pages]
        if not pages:
            raise RuntimeError("未找到可用的 Trae 页面")
        # 优先：URL 含 solo 的页面；其次：存在模型选择器或设置浮层的页面
        for pg in pages:
            if "solo" in pg.url:
                return pg
        for pg in pages:
            try:
                if (
                    self._visible(pg.locator(".core-model-select-trigger"))
                    or self._visible(pg.locator(".solo-lite-settings-overlay"))
                ):
                    return pg
            except Exception:
                continue
        return pages[0]

    def _open_settings(self, page):
        """打开设置浮层（多种入口，逐级兜底）。"""
        overlay = page.locator(".solo-lite-settings-overlay")
        if self._visible(overlay):
            return
        # 入口 1：底部模型选择器 -> 添加模型（直达模型页）
        trigger = page.locator(".core-model-select-trigger")
        if self._visible(trigger):
            try:
                trigger.first.click()
                entry = page.locator("text=添加模型").last
                entry.wait_for(state="visible", timeout=5000)
                entry.click()
                overlay.first.wait_for(state="visible", timeout=8000)
                return
            except Exception:
                pass  # 落入下一级兜底
        # 入口 2：快捷键 Ctrl+,（VS Code 系通用）
        try:
            page.keyboard.press("Control+,")
            overlay.first.wait_for(state="visible", timeout=5000)
            return
        except Exception:
            pass
        raise RuntimeError(
            "无法打开设置页面。请手动打开 Trae 的 设置 -> 模型 页面后重试。"
        )

    def _open_custom_model_form(self, page):
        """状态循环：无论当前界面处于什么状态，最终打开自定义模型表单。

        覆盖的状态：
          - 表单已打开（含上次残留）：直接复用，fill 会覆盖旧值
          - 服务商选择弹窗开着：点「自定义模型」
          - 设置浮层开着但在其他页签：点左侧「模型」
          - 设置浮层未打开：先打开设置
        """
        url_input = page.locator("input[placeholder*='api.openai.com']")
        add_btn = page.locator("button.setting-button", has_text="添加模型")
        picker = page.locator(".icd-modal-overlay.add-model-dialog")
        overlay = page.locator(".solo-lite-settings-overlay")

        for _attempt in range(4):
            if self._visible(url_input):
                return url_input.first

            if self._visible(picker):
                # 服务商选择弹窗开着
                picker.locator("text=自定义模型").first.click()
                url_input.first.wait_for(state="visible", timeout=8000)
            elif self._visible(overlay):
                # 设置浮层开着：确保在模型页，再点 + 添加模型
                if self._visible(add_btn):
                    add_btn.first.click()
                    picker.first.wait_for(state="visible", timeout=8000)
                else:
                    overlay.locator("text=模型").first.click()
                    add_btn.first.wait_for(state="visible", timeout=8000)
            else:
                self._open_settings(page)

        # 循环后仍未打开，做最后一次显式等待，失败则抛出
        url_input.first.wait_for(state="visible", timeout=8000)
        return url_input.first

    def _fill_and_submit(self, page, cfg, url_input):
        url_input.fill(cfg["url"])
        page.locator("input[placeholder='输入模型 ID']").fill(cfg["id"])
        page.locator("input[placeholder='请输入模型展示名称']").fill(cfg["name"])
        page.locator("input[placeholder='请输入 API Key']").fill(cfg["api_key"])
        # Wait for form validation instead of always sleeping, including when a
        # duplicate keeps the submit button disabled. Never send input to JS.
        page.wait_for_function("""() => {
            const button = document.querySelector('button.add-model-connect-button');
            const duplicate = [...document.querySelectorAll(
                '.add-model-dialog .ai-setting-field-item-error-message'
            )].some(node => /已存在|重复/.test(node.textContent));
            return duplicate || (button && !button.disabled);
        }""", timeout=8000)

        dialog = page.locator(".icd-modal-overlay.add-model-dialog")
        if self._skip_existing(page, dialog):
            return "skipped"
        page.locator("button.add-model-connect-button").click()

        deadline = time.monotonic() + _SUBMIT_WAIT_TIMEOUT
        while time.monotonic() < deadline:
            if not self._visible(dialog):
                return "added"
            if self._skip_existing(page, dialog):
                return "skipped"
            page.wait_for_timeout(250)
        raise RuntimeError("添加失败或连通性测试未通过（请检查 url 和 apiKey 是否正确）。")

    def _skip_existing(self, page, dialog):
        error = dialog.locator(".ai-setting-field-item-error-message").filter(has_text=re.compile("已存在|重复"))
        if not self._visible(error):
            return False
        page.wait_for_timeout(_DUPLICATE_NOTICE_MS)
        dialog.locator(".icd-modal-close").click()
        dialog.first.wait_for(state="hidden", timeout=3000)
        return True

    def _add_model_via_ui(self, page, cfg):
        url_input = self._open_custom_model_form(page)
        return self._fill_and_submit(page, cfg, url_input)
