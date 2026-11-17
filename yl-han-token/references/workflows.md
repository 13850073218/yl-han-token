# 配置与验证流程

技能自带完整运行代码。无需原项目，也不要求特定工作目录。以下 `<入口>` 指安装后 `scripts/hantoken.py` 的绝对路径，`<运行目录>` 指其旁的 `runtime`。用当前 Python 环境和脚本 `--help` 核对参数，不硬编码用户目录、端口、凭据或某次服务商状态。

## 输入和 Agent 分支

先确定唯一目标：用户本次明确指定的 Agent 优先；未指定时使用调用技能的宿主 Agent。
例如在 WorkBuddy 中仅粘贴模型 JSON，就默认添加到 WorkBuddy 的自定义模型；
在 Trae CN 中调用则默认添加到 Trae CN。宿主明确时无需再次询问。
以可信调用环境识别宿主，不按正在运行的程序或源码目录猜测；
宿主未知时使用本次任务已明确的唯一目标，仍有歧义才询问。
调用入口时始终显式传入 `--agent Codex|WorkBuddy|Trae`，不能让脚本默认值覆盖宿主选择。
仅处理所选目标的自定义模型，保留自带模型及其他客户端配置。

Trae CN 的宿主选择与执行条件需分开说明：普通启动未开启 CDP 时，自动配置需完全退出后从外部入口以调试模式启动。若技能正在 Trae 自己的聊天窗口执行，优先按 [README 手动引导](../README.md#在-trae-当前窗口内手动添加无需关闭) 让用户在当前窗口添加，无需关闭：模型下拉菜单 → 添加模型 →「+ 添加模型」→ 自定义模型 → 填表并添加。依据本次输入给出地址、模型 ID、显示名和协议；基础地址模式关闭「完整 URL」，由客户端补请求路径；完整端点模式开启该开关，避免重复路径。不明文复述密钥，未得到实际成功证据前不声称已配置。

用户选择自动配置时，使用 Codex / WorkBuddy 明确配置 Trae，或使用 Trae 之外的独立 PowerShell / 本工具 GUI，保存工作并退出 Trae（含托盘），再由外部入口导入。关闭 Trae 会中断其当前 Agent，不能要求原窗口继续；集成终端不视为外部入口。不使用强杀或延时脚本关闭宿主来绕过限制。已有可用 CDP 时直接复用。

独立 GUI 的 Trae 批量导入启用 `wait_for_close=True`，通过主线程显示 `on_status` 进度。可先点击「校验并写入」，再完全退出 Trae；窗口保留原批次，等待退出后自动调试启动并继续，无需第二次点击。等待退出上限 180 秒；超时不提交，输入保留。低层适配器及聊天内调用默认不等待，继续提供手动/外部引导，避免误把独立 GUI 和 Trae 宿主混为一谈。

单个模型示意：

```json
{"id":"qwen3.7-flash","url":"https://api.hanrouter.ai/v1","apiKey":"填写用户自己的密钥"}
```

批量导入使用上述对象的 JSON 数组，例如分别填写 `glm-5.3-flash` 和 `qwen3.7-flash`。统一解析器 `core.schema.parse_model_configs` 可处理聊天复制的字段 Markdown、链接、代码围栏和续行符；不得自行进行可能破坏密钥的全局替换。

| 目标 | URL 含义 | 实施入口与注意事项 |
| --- | --- | --- |
| Codex | 基础地址，如 `https://api.hanrouter.ai/v1` | GUI 选 Codex，或使用 `CodexAdapter`；HanRouter 自动选择 Chat，本地转换为 Responses |
| WorkBuddy | 完整 Chat Completions 地址，如 `https://api.hanrouter.ai/v1/chat/completions` | 使用 WorkBuddy 适配器；仅自定义列表按 ID 去重，保留最后一条；名称不加前缀 |
| Trae CN | 基础地址 | 沿用原生自定义分组，名称不加前缀；使用已有客户端 CDP；“该模型已存在”显示约 1.2 秒后关闭添加弹窗、提示跳过并继续批次 |

解析器只清理 URL 外层复制格式，不自动补路径。不要把 WorkBuddy 的完整请求路径原样用于 Codex。显式 `codex.wireApi` 优先于自动选择；其他服务商不能因为 HanRouter 成功就假设兼容。

对话中只提供密钥和模型 ID、未提供 URL 时，技能按目标使用默认地址补全，不再向用户索取：WorkBuddy 用完整 Chat Completions 地址 `https://api.hanrouter.ai/v1/chat/completions`，Codex 与 Trae CN 用基础地址 `https://api.hanrouter.ai/v1`。模型 ID 取用户原值，`name` 省略时用 ID，密钥只经 `--stdin` 传入并显示脱敏信息；报告中说明本次默认地址以便用户纠正（用户提供真实地址后按 ID 重新导入即可，不产生重复）。用户本轮显式给出 URL 时以用户值为准。

WorkBuddy、Trae 的 `name` 使用用户给定名称，省略时直接使用 `id`；不要额外添加 `自定义模型 / ` 或自行拼接 `name:id`。客户端自带模型保持不变，只处理自定义配置。

WorkBuddy 导入时，已有自定义列表和批量输入均以去掉首尾空格、区分大小写的 `id` 为唯一键；同 ID 保留最后一条，重复导入按 ID 更新已有项，不因名称、地址、密钥不同而新增副本。不同 ID 即使使用同一地址或密钥也分别保留。清理既有重复时保留最后一条的完整字段，保留无关条目和顶层元数据，写入前备份。此规则仅适用于 WorkBuddy 自定义列表，不用于合并客户端自带目录或 Codex 的不同账号路由。

GUI：`python -X utf8 "<入口>" gui --agent Codex`。也支持 `--agent WorkBuddy` 或 `--agent Trae`。tomlkit 已随技能打包，无需安装。Trae 优先使用当前 Python 的 Playwright，否则检查并复用技能内已有独立环境；仅首次或依赖缺失时安装，需要联网但无需下载浏览器。未安装目标程序时停止，可通过 `CODEX_EXE`、`WORKBUDDY_EXE`、`TRAE_CN_EXE` 指定已有程序路径。

通用导入：`python -X utf8 "<入口>" apply --agent Codex --input "<用户配置JSON文件>"`，或 `--stdin` 从工具提供的标准输入读取，不创建共享的密钥文件。Codex 默认将本次第一项设为默认；明确要求保留当前默认时加 `--keep-default`。输入中显式 `codex.setDefault` 优先于自动选第一项，`--keep-default` 覆盖输入中的默认设置。`--protocol auto|chat|responses` 指定 Codex 协议。`--default-model <精确ID>` 用于明确选择已有常规模型，与 `--keep-default` 互斥，且不能同时指定自定义项 `setDefault: true`。用户明确授权导入即可以实施，不因技能额外请求确认。

本包只包含通用代码、第三方库许可证及 `manifest.json` 校验清单，不附带作者的任何模型配置、凭据或会话。接收者提供自己的密钥。迁移整份技能目录即可；Trae 创建的 `.trae-env` 属于本机环境，不作为后续分发内容。

### 性能与验证范围

常规导入一次 `apply` 处理整批；内置安装检查不需要额外 `detect`。`self-check` 用于首次安装、迁移或完整性故障，不需要每次执行。加 `--timings` 可查看客户端发现、安装检查、依赖准备、输入解析及适配器阶段耗时；计时不输出配置。适配器阶段包含其必要的网络/界面验证，不将超时上限当成实际耗时。

WorkBuddy 内容相同则回读验证后跳过写入与备份；发生变更时仍备份并原子写入。Trae 整批只连接一次 CDP，以界面状态等待替代固定启动/转场延时；重复提示的短暂展示保留。Codex 仅在当前进程按 CLI 文件指纹复用离线能力与内置目录，不缓存上游认证或连通性；CLI 更新后重新探测。桌面启用的必需能力一项失败即停止，不再继续发送注定无法通过本次验收的请求；独立兼容性诊断仍保留完整检查。已有成功请求证据无需在同次导入中重复，完整同会话切换验收仅用于用户要求或切换实现变更。

## Codex 实施

先用 `agents.codex.config_dir()` 解析 `CODEX_HOME`，否则使用用户 `.codex`。只输出配置中的非敏感字段。核对当前 provider、实际 URL、默认 ID、目录、profile，以及既有 `hantoken_bridge/hantoken_desktop_router.json`。

导入新的配置时：

- 使用公共解析器得到配置并经 `core.codex_config.prepare_codex_config` 准备协议。
- `CodexAdapter().apply_many(configs, desktop_groups=True)` 启用统一目录。GUI 默认勾选第一项，独立入口通过 `select_codex_defaults` 默认选第一项；底层适配器的直接调用仍以 `codex.setDefault` / `default_model` 为准。用户明确要求保留时不改默认，不能为规避错误擅自换型号。
- 已有原服务商的同一 provider ID 对应本地统一入口，各模型路由分别保留各自上游 URL/协议/密钥。不要为常规和自定义设置两个根 provider 并假设桌面模型选择器会自动切换 provider。
- 原服务商支持范围以当前实现为准：Responses、API Key，额外请求头/查询参数及 OAuth 配置未支持时明确停止，不丢弃这些设置继续写入。

从既有保存档案启用两组，参数以实际档案名称为准：

```powershell
python -X utf8 "<运行目录>/tools/enable_codex_groups.py" --profile hantoken_hanrouter_glm_5_3_flash --profile hantoken_hanrouter_qwen3_7_flash --switch-check --report "<脱敏报告路径>"
```

高级脚本直接运行时需要将技能 `scripts/vendor` 加入该进程 PYTHONPATH；优先使用统一入口，其已处理依赖路径。上述档案名仅为已有档案示例，接收者首次导入使用 `apply`，不能假设已经存在。只有用户要求改变默认且已经确认精确 ID 时追加 `--default-model <exact-id>`。不要把历史验证成功的 `gpt-6-sol` 固定为所有账号的默认值。

## 验证工具

- `tools/enable_codex_groups.py --inspect-only`：原生 `model/list`，检查显示名、重复与可见性。此检查会运行 app-server，即使未改配置也可能写入 Codex 状态目录。
- `--preview-only --gpt-model <exact-id> --report <path>`：测试源代码预览入口，保留真实配置，不代表已安装服务通过。
- 启用命令加 `--switch-check`：实际安装后的原生同会话测试；查看报告 `switch_check.ok`、每轮 `tool_called/answered/turn_status` 及 `route_evidence`。
- `core.codex_switch_check.check_switching`：需要不同切换次序时使用；显式指定 `provider_id`，旧会话场景使用 `initial_url` 和 `resume_after_first=True`。
- `core.codex_tool_check.check_tool`：隔离工具任务；传入目标条目真实 `model_info` 和支持的 `reasoning_effort`。默认 `none` 可能不适用于常规模型，不能把该参数错误当网络失败。
- `core.codex_diagnostics.routing_report(config_dir())` 或 GUI“查看实际调用”：读取安装服务的脱敏记录，并识别恢复直连后的历史记录。
- `tools/repair_codex_router_runtime.py`：仅在源代码已修复并通过相关测试、需要更新安装服务时使用；备份脚本和设置，在原端口重启托管网关，不替代配置启用，也不重启桌面。可能中断在途请求，先确认网关空闲。

统一入口 `switch-check` 使用隔离原生会话调用安装网关；旧会话恢复的更深入验收可用上述高级脚本。`diagnose` 只读已安装服务。`restore` 恢复本工具记录的原默认配置，不能用于退出登录。`self-check` 是离线检查，不访问上游、不修改用户配置。不要用自检通过代替实际工具调用和上游验证。

恢复使用 `CodexAdapter().restore_defaults()` 或 GUI“恢复原 Codex 默认配置”。退出登录使用独立 logout 操作，不用删除配置目录代替。配置刷新后打开原会话继续测试，无需因每次换模型新建聊天。
