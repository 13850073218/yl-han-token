# -*- coding: utf-8 -*-
"""自定义模型一键配置工具（GUI 入口）。

用法：python main.py
依赖：tkinter（标准库）；其余依赖见 requirements.txt。
粘贴内容支持单个模型对象 {...} 或多个模型组成的数组 [...]，可一次批量写入。
必填仅 id / url / apiKey；name 缺省取 id，vendor 缺省为 Custom。
"""

import threading
import tkinter as tk
from tkinter import messagebox, ttk

import agents
from core.schema import SchemaError, parse_model_configs
from core.codex_probe import check_provider, format_report
from core.codex_config import prepare_codex_config

PROTOCOL_LABELS = {
    "auto": "自动（HanRouter 使用 Chat Completions）",
    "responses": "Responses",
    "chat": "Chat Completions（本地转换）",
}

SAMPLE_JSON = """{
    "id": "qwen3.7-flash",
    "url": "https://api.hanrouter.ai/v1",
    "apiKey": "sk-xxxxxxxx"
}"""


class App(tk.Tk):
    def __init__(self, initial_agent=None, initial_protocol="auto", initial_set_default=True):
        super().__init__()
        self.title("自定义模型一键配置")
        self.geometry("640x560")
        self.minsize(560, 480)

        top = ttk.Frame(self, padding=10)
        top.pack(fill=tk.X)
        selector = ttk.Frame(top)
        selector.pack(fill=tk.X)
        ttk.Label(selector, text="目标 Agent：").pack(side=tk.LEFT)
        self.agent_var = tk.StringVar()
        names = agents.list_agents()
        self.agent_combo = ttk.Combobox(
            selector, textvariable=self.agent_var, values=names, state="readonly", width=24
        )
        self.agent_combo.pack(side=tk.LEFT)
        if names:
            self.agent_combo.current(names.index(initial_agent) if initial_agent in names else 0)
        self.detect_label = ttk.Label(top, text="", wraplength=600)
        self.detect_label.pack(fill=tk.X, pady=(4, 0))
        top.bind("<Configure>", lambda event: self.detect_label.config(wraplength=max(100, event.width - 20)))
        self.agent_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_detect())
        self.default_model_var = tk.BooleanVar(value=initial_set_default)
        self.codex_options = ttk.Frame(self, padding=(10, 0))
        self.codex_options_anchor = top
        protocol_row = ttk.Frame(self.codex_options)
        protocol_row.pack(fill=tk.X,pady=(0,3))
        ttk.Label(protocol_row,text="接口协议：").pack(side=tk.LEFT)
        self.protocol_var = tk.StringVar(value=PROTOCOL_LABELS[initial_protocol])
        self.protocol_combo = ttk.Combobox(protocol_row,textvariable=self.protocol_var,state="readonly",
            values=tuple(PROTOCOL_LABELS.values()),width=39)
        self.protocol_combo.pack(side=tk.LEFT)
        ttk.Checkbutton(
            self.codex_options,
            text="同时将第一项设为默认模型",
            variable=self.default_model_var,
        ).pack(anchor=tk.W)
        codex_actions = ttk.Frame(self.codex_options)
        codex_actions.pack(fill=tk.X, pady=(2, 4))
        self.check_button = ttk.Button(codex_actions, text="检测接口（不写入）", command=self.check_codex)
        self.check_button.pack(side=tk.LEFT)
        self.restore_button = ttk.Button(codex_actions, text="恢复原 Codex 默认配置", command=self.restore_codex)
        self.restore_button.pack(side=tk.LEFT, padx=6)
        self.logout_button = ttk.Button(codex_actions, text="退出 Codex 登录", command=self.logout_codex)
        self.logout_button.pack(side=tk.LEFT)
        self.routes_button = ttk.Button(self.codex_options, text="查看实际调用（不写入）", command=self.inspect_routes)
        self.routes_button.pack(anchor=tk.W, pady=(0, 4))
        ttk.Label(self.codex_options, text="Codex 导入到自定义模型组，保留 GPT 组；检测会发送少量请求。").pack(anchor=tk.W)

        mid = ttk.Frame(self, padding=(10, 0))
        mid.pack(fill=tk.BOTH, expand=True)
        input_label = ttk.Label(
            mid, text="粘贴模型配置 JSON（单个对象或数组；name 默认取 id，vendor 默认 Custom）：",
            wraplength=600,
        )
        input_label.pack(anchor=tk.W, fill=tk.X)
        mid.bind("<Configure>", lambda event: input_label.config(wraplength=max(100, event.width - 20)))
        self.text = tk.Text(mid, height=12, wrap=tk.NONE, font=("Consolas", 10))
        self.text.pack(fill=tk.BOTH, expand=True, pady=(4, 4))
        self.text.insert("1.0", SAMPLE_JSON)

        btns = ttk.Frame(self, padding=(10, 4))
        btns.pack(fill=tk.X)
        ttk.Button(btns, text="一键粘贴", command=self.paste).pack(side=tk.LEFT)
        ttk.Button(btns, text="清空", command=lambda: self.text.delete("1.0", tk.END)).pack(
            side=tk.LEFT, padx=6
        )
        self.apply_button = ttk.Button(btns, text="校验并写入", command=self.apply)
        self.apply_button.pack(side=tk.RIGHT)
        self.default_model_var.trace_add("write", lambda *_: self.refresh_apply_label())

        bottom = ttk.Frame(self, padding=(10, 4, 10, 10))
        bottom.pack(fill=tk.BOTH, expand=True)
        ttk.Label(bottom, text="日志：").pack(anchor=tk.W)
        self.log = tk.Text(bottom, height=8, state=tk.DISABLED, font=("Consolas", 9))
        self.log.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        self.refresh_detect()

    def refresh_detect(self):
        if self.agent_var.get() == "Codex":
            self.codex_options.pack(fill=tk.X, after=self.codex_options_anchor)
        else:
            self.codex_options.pack_forget()
        self.refresh_apply_label()
        adapter = agents.get_adapter(self.agent_var.get())
        ok, msg = adapter.detect()
        self.detect_label.config(text=("✔ " if ok else "✘ ") + msg.split("，")[0])
        self.writelog(msg)

    def refresh_apply_label(self):
        desktop = self.agent_var.get() == "Codex" and self.default_model_var.get()
        self.apply_button.config(text=("检测并设为默认" if desktop else "检测并添加到自定义模型")
                                 if self.agent_var.get() == "Codex" else "校验并写入")

    def paste(self):
        try:
            content = self.clipboard_get()
        except tk.TclError:
            messagebox.showwarning("提示", "剪贴板为空")
            return
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", content)

    def writelog(self, msg):
        self.log.config(state=tk.NORMAL)
        self.log.insert(tk.END, msg.rstrip() + "\n")
        self.log.see(tk.END)
        self.log.config(state=tk.DISABLED)

    def apply(self):
        raw = self.text.get("1.0", tk.END)
        try:
            cfgs = parse_model_configs(raw)
        except SchemaError as e:
            messagebox.showerror("配置错误", str(e))
            return

        adapter = agents.get_adapter(self.agent_var.get())
        installed, message = adapter.detect()
        if not installed:
            self.writelog(message + "；未写入配置。")
            messagebox.showwarning("未安装目标 Agent", message)
            return
        if self.agent_var.get() == "Codex":
            protocol_label = self.protocol_var.get() if hasattr(self, "protocol_var") else PROTOCOL_LABELS["auto"]
            protocol = next(key for key, label in PROTOCOL_LABELS.items() if label == protocol_label)
            cfgs = [prepare_codex_config(cfg, protocol) for cfg in cfgs]
        if self.agent_var.get() == "Codex" and self.default_model_var.get():
            first = cfgs[0]
            target = first["codex"].get("profile") or (first["id"], first["name"], first["url"])
            for cfg in cfgs:
                key = cfg["codex"].get("profile") or (cfg["id"], cfg["name"], cfg["url"])
                if key == target:
                    cfg["codex"]["setDefault"] = True
                elif cfg["codex"].get("setDefault"):
                    messagebox.showerror("配置错误", "已勾选使用第一项为默认模型，请移除其他项的 setDefault: true")
                    return
        self.writelog("开始写入 %d 个模型..." % len(cfgs))
        if self.agent_var.get() == "Codex":
            for cfg in cfgs:
                self.writelog("模型 %s：%s" % (cfg["id"], PROTOCOL_LABELS[cfg["codex"]["wireApi"]]))
            desktop = any(cfg["codex"].get("setDefault") for cfg in cfgs)
            self.writelog("将检测接口并添加到自定义模型组，保留 GPT 组；成功后重启 Codex 并新建聊天。")
        self.apply_button.config(state=tk.DISABLED)
        self.agent_combo.config(state=tk.DISABLED)
        for name in ("check_button", "restore_button", "logout_button"):
            if hasattr(self, name):
                getattr(self, name).config(state=tk.DISABLED)
        if hasattr(self,"protocol_combo"):
            self.protocol_combo.config(state=tk.DISABLED)
        # 自动化过程耗时较长，放到后台线程避免界面卡死
        threading.Thread(target=self._apply_worker, args=(adapter, cfgs), daemon=True).start()

    def _apply_worker(self, adapter, cfgs):
        try:
            from agents.codex import CodexAdapter
            from agents.trae_cn import TraeCNAdapter
            if isinstance(adapter, CodexAdapter):
                result = adapter.apply_many(cfgs, desktop_groups=True)
            elif isinstance(adapter, TraeCNAdapter):
                result = adapter.apply_many(cfgs, wait_for_close=True,
                    on_status=lambda message: self.after(0, self._show_apply_status, message))
            else:
                result = adapter.apply_many(cfgs)
        except Exception as e:  # 面向用户的错误展示
            self.after(0, self._apply_done, False, str(e))
            return
        self.after(0, self._apply_done, True, result)

    def _show_apply_status(self, message):
        self.detect_label.config(text=message)
        self.writelog(message)

    def _apply_done(self, ok, msg):
        self.apply_button.config(state=tk.NORMAL)
        self.refresh_apply_label()
        self.agent_combo.config(state="readonly")
        for name in ("check_button", "restore_button", "logout_button"):
            if hasattr(self, name):
                getattr(self, name).config(state=tk.NORMAL)
        if hasattr(self,"protocol_combo"):
            self.protocol_combo.config(state="readonly")
        self.writelog(msg)
        if ok:
            messagebox.showinfo("完成", msg)
        else:
            messagebox.showerror("写入失败", msg)

    def check_codex(self):
        try:
            cfgs = parse_model_configs(self.text.get("1.0", tk.END))
            from core.schema import validate_codex_url
            protocol = next(key for key, label in PROTOCOL_LABELS.items() if label == self.protocol_var.get())
            cfgs = [prepare_codex_config(cfg, protocol) for cfg in cfgs]
            for cfg in cfgs:
                validate_codex_url(cfg["codex_url"])
        except SchemaError as error:
            messagebox.showerror("配置错误", str(error))
            return
        self._codex_action("开始检测接口，不修改 Codex 配置...", self._check_worker, cfgs)

    def _check_worker(self, cfgs):
        try:
            reports = []
            for cfg in cfgs:
                results = check_provider(cfg)
                reports.append("模型：%s\n%s" % (cfg["id"], format_report(results)))
            message = "\n\n".join(reports) + "\n检测结束；未修改配置。失败项目通过前，请勿启用桌面默认模型。"
        except Exception:
            message = "接口检测未完成；未修改配置。请检查网络或稍后重试。"
        self.after(0, self._codex_action_done, message)

    def restore_codex(self):
        self._codex_action("正在恢复原 Codex 默认配置...", self._restore_worker)

    def inspect_routes(self):
        self._codex_action("正在读取实际请求与返回的模型 ID...", self._routes_worker)

    def _routes_worker(self):
        from agents.codex import config_dir
        from core.codex_diagnostics import routing_report
        try:
            message = routing_report(config_dir())
        except Exception:
            message = "无法读取 Codex 调用记录；请检查配置及本地入口。"
        self.after(0, self._codex_action_done, message)

    def logout_codex(self):
        if messagebox.askyesno(
            "退出 Codex 登录",
            "确认退出 Codex 登录？退出后需重新登录。\n"
            "此操作清理 Codex 登录凭据，保留模型配置及第三方服务商密钥。",
        ):
            self._codex_action("正在退出 Codex 登录...", self._logout_worker)

    def _logout_worker(self):
        try:
            message = agents.get_adapter("Codex").logout()
        except Exception:
            message = "Codex 退出登录未完成，请检查客户端是否已安装并稍后重试。"
        self.after(0, self._codex_action_done, message)

    def _restore_worker(self):
        try:
            message = agents.get_adapter("Codex").restore_defaults()
        except Exception as error:
            message = str(error)
        self.after(0, self._codex_action_done, message)

    def _codex_action(self, message, worker, *args):
        self.writelog(message)
        for widget in (self.apply_button, self.check_button, self.restore_button, self.logout_button, self.routes_button, self.agent_combo):
            widget.config(state=tk.DISABLED)
        self.protocol_combo.config(state=tk.DISABLED)
        threading.Thread(target=worker, args=args, daemon=True).start()

    def _codex_action_done(self, message):
        for widget in (self.apply_button, self.check_button, self.restore_button, self.logout_button, self.routes_button):
            widget.config(state=tk.NORMAL)
        self.agent_combo.config(state="readonly")
        self.protocol_combo.config(state="readonly")
        self.writelog(message)
        messagebox.showinfo("Codex 检测 / 恢复结果", message)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="自定义模型配置工具")
    parser.add_argument("--agent", choices=agents.list_agents())
    parser.add_argument("--protocol", choices=("auto","responses","chat"),default="auto")
    args=parser.parse_args()
    App(initial_agent=args.agent,initial_protocol=args.protocol).mainloop()
