---
name: yl-han-token
description: 通过对话提供 API Key 和精确模型 ID，为 Codex、WorkBuddy、Trae CN、OpenCode 接入汉路特自定义模型；为 Cursor、Cline、Roo Code、Continue、Aider 等提供客户端对应配置引导。支持默认地址、批量模型、脱敏诊断与最小请求验证。
---

# 汉路特模型对话接入

用户说“接入这个模型”“配置汉路特”，给出 Key 和模型 ID 时，直接整理输入并配置已明确的目标。默认服务为 HanRouter，不要求用户另写 JSON 或再提供 URL。用户明确指定其他服务地址时以其为准。

## 对话输入与目标

- 从本轮对话获取 `apiKey`（也接受 key）、`id`（也接受 modelId / model）及可选 URL；Key 不从日志、旧会话或截图中搜集。多个 ID 共用 Key 时逐项保留，**ID 区分大小写，不猜测或替换型号**。
- 用户指定目标优先，否则采用可信宿主环境中的当前智能体。只有目标不明、缺少 Key 或 ID 时才问缺失项；不因缺少 URL 追问。
- 默认基础地址 `https://api.hanrouter.ai/v1`；WorkBuddy 使用完整 `https://api.hanrouter.ai/v1/chat/completions`。入口会根据目标补全，支持同 Key 批量模型。
- 优先用工具的 stdin 传 JSON，不把 Key 写入命令参数、终端回显、项目文件、版本库或报告。工具无法安全传 stdin 时使用原有 GUI（支持的客户端）或用户本地私有配置文件；不创建 shell 中嵌有真实 Key 的命令。客户端必需的私有凭据配置可以写入，备份也包含敏感信息，留在用户配置目录。
- 只改变指定客户端和本批模型，保留无关配置。依用户指令执行，不另加确认。已授权接入不代表可以发布其凭据、安装无关软件或关闭当前宿主。

## 运行与选择入口

入口相对技能目录定位，不依赖作者电脑路径。Python 3.10+；`scripts/hantoken.py` 保留原有 Windows 客户端适配；`scripts/connect.py` 的 OpenCode、指南和探测支持 Windows/macOS/Linux。仅加载 [references/agent-compatibility.md](references/agent-compatibility.md) 中目标客户端的分支。

| 目标 | 操作 |
| --- | --- |
| Codex / WorkBuddy / Trae CN | `python -X utf8 <技能目录>/scripts/hantoken.py apply --agent Codex --stdin`，替换明确的目标 |
| OpenCode | `python -X utf8 <技能目录>/scripts/connect.py apply --agent OpenCode --stdin` |
| Cursor / Cline / Roo Code / Continue / Aider / 其他 | `connect.py guide --agent <目标> --stdin` 生成脱敏参数，再按对应客户端文档或界面实施；未操作不能宣称写入成功 |
| Claude Code | 先看兼容说明；其原生 Anthropic 协议不能直接使用 OpenAI 地址，不伪造通用自动接入 |

JSON 输入既可为 `{"id":"精确模型ID","apiKey":"用户提供的原值"}`，也可为 `{"apiKey":"用户提供的原值","models":["ID-A","ID-B"]}`。支持原来的 `{id,url,apiKey}` 和对象数组。上述占位文本只用于说明，不发送给接口。自然语言由宿主整理为 JSON，程序不尝试从任意聊天文字中猜 Key。

首次迁移或怀疑损坏时执行 `hantoken.py self-check`。常规导入不必每次自检或全量扫描。原有 Codex / WorkBuddy / Trae 细节参阅 [references/workflows.md](references/workflows.md)，故障诊断参阅 [references/diagnostics.md](references/diagnostics.md)。

## 客户端注意事项

- Codex：原入口使用统一目录与本地 Chat→Responses 转换，不能只写 CLI profile 就声称桌面已接入。默认本批第一项为默认模型；用户要求仅添加时传 `--keep-default`。同会话切换完整验收仅在用户要求或路由实现变更时进行，保留既有诊断工具。
- WorkBuddy：回读配置不等于网络成功；按精确 ID 更新，重复导入不产生副本。
- Trae CN：优先复用可用 CDP。当前宿主为 Trae 且未开 CDP 时，按兼容说明提供原生界面添加指引；不关闭或强杀当前宿主。外部 GUI 自动配置流程保留。
- OpenCode：仅在 CLI 已安装时写入用户级 JSON；保留其他 provider，每个精确 ID 独立凭据，原子更新并备份。存在 JSONC 时停止自动写入，避免破坏注释或覆盖设置。默认选本批第一项，`--keep-default` 仅保留当前选择。
- 非文本模型：图片、语音、视频模型可用于独立 API 调用，但不能当作通用编程 Agent 主模型；不因接口支持生成就声称支持工具调用。按用户用途提供 API 案例或说明客户端限制。

## 最小验证与汇报

`connect.py probe --agent <目标> --stdin` 只请求 `/models`，不消耗生成额度；确认鉴权及精确 ID 可见，不能证明生成成功。

用户要求验证调用或接入需做最小调用时，文字模型可用 `probe ... --chat`（8 个输出 token，一次请求，无自动重试）。预算不足或模型要求别的参数时如实报告，不把空输出判定为生成通过；不未经说明升级为昂贵生成。原适配器已有可靠网络验证时无需重复。

最小 Chat 请求也不证明 Agent 工具调用、图片输入、长上下文或桌面切换可用。仅报告实际完成的层次：配置已写入并回读 / 模型目录可见 / 文本有输出 / 原生工具调用通过。网络失败时保留脱敏 HTTP 状态和失败类别，不回显原始响应中的潜在密钥。

完成后说明目标客户端、精确 ID、实际地址、默认模型是否变化、验证结果及是否需重新加载。所有 Key 显示 `****************`。不得在尚未执行、只生成指南或只完成目录查询时声称全面可用。
