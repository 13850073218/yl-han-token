# -*- coding: utf-8 -*-
"""Agent 适配器抽象基类。"""

import abc


class AgentAdapter(abc.ABC):
    """每个目标 Agent（Trae CN / WorkBuddy / ...）实现一个适配器。"""

    #: 显示名称，出现在 GUI 下拉框中（子类必须覆盖）
    display_name = ""

    def require_installed(self):
        installed, message = self.detect()
        if not installed:
            raise RuntimeError(message + "\n未写入配置，请先安装对应客户端或指定有效的程序路径。")

    @abc.abstractmethod
    def detect(self):
        """检测本机是否安装了该 Agent，返回 (是否可用, 说明信息)。"""
        raise NotImplementedError

    @abc.abstractmethod
    def running_processes(self):
        """返回正在运行的相关进程名列表（写入前需要关闭）。"""
        raise NotImplementedError

    @abc.abstractmethod
    def apply(self, model_config):
        """把归一化后的模型配置写入该 Agent。

        model_config: core.schema.parse_model_config 的返回值。
        返回面向用户的结果说明字符串；失败抛出异常。
        """
        raise NotImplementedError

    def apply_many(self, model_configs):
        """批量写入多个模型，默认逐条调用 apply。

        子类可覆盖为一次性写入（例如 WorkBuddy 合并成单次原子写、单次备份）。
        """
        results = []
        for cfg in model_configs:
            results.append(self.apply(cfg))
        return "\n".join(results)
