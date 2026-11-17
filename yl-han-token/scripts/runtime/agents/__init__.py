# -*- coding: utf-8 -*-
"""Agent 适配器注册表。

新增 Agent 支持时：在 agents/ 下新建模块实现 AgentAdapter，
并在此处注册即可，GUI 会自动出现对应选项。
"""

from agents.trae_cn import TraeCNAdapter
from agents.workbuddy import WorkBuddyAdapter
from agents.codex import CodexAdapter

REGISTRY = {
    TraeCNAdapter.display_name: TraeCNAdapter,
    WorkBuddyAdapter.display_name: WorkBuddyAdapter,
    CodexAdapter.display_name: CodexAdapter,
    # 后续扩展：
    # "ChatGPT": ChatGPTAdapter,
    # "Claude Code": ClaudeCodeAdapter,
}


def list_agents():
    """返回所有已注册 Agent 的显示名列表。"""
    return list(REGISTRY.keys())


def get_adapter(display_name):
    """按显示名实例化对应适配器。"""
    return REGISTRY[display_name]()
