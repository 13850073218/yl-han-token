# 汉路特模型对话接入 Skill

安装后，直接在智能体对话中提供 **API Key + 精确模型 ID**，即可按当前客户端接入。默认使用 `https://api.hanrouter.ai/v1`，不需要另找地址或手写 JSON；也可指定其他兼容服务的 URL。

```text
使用 yl-han-token 接入汉路特。
模型 ID：填写你要接入的精确 ID
Key：填写你自己的 API Key
只添加模型，保留当前默认选择。
```

同 Key 批量接入可以说“帮我接入这些模型：ID-A、ID-B，Key 是……”。ID 大小写保留。请只在你信任的智能体私密对话中提供凭据；技能不会在完成报告中复述 Key，但对话服务本身仍遵守该客户端的数据处理规则。

## 安装或更新

在支持技能的智能体里发送：

```text
请从 https://github.com/13850073218/yl-han-token 安装或更新 yl-han-token 技能到当前智能体可发现的目录，检查其完整性，报告实际安装位置。不要修改我的模型配置，等我提供 Key 和模型 ID 后再接入。
```

支持 `skills` 安装器的环境可运行（需要 Node.js/npx）：

```shell
npx --yes skills add https://github.com/13850073218/yl-han-token --skill yl-han-token --global --yes
```

安装器识别哪些客户端以其当前版本输出为准；不支持原生 Skill 的产品可由具备文件/终端工具的宿主读取 `yl-han-token/SKILL.md`，或按客户端指南配置。下载 ZIP 时请保留完整的 `yl-han-token/` 目录，不能只复制 SKILL.md。

## 客户端兼容范围

| 客户端 | 本包实现 |
| --- | --- |
| Codex | Windows 自动配置，原有本地协议桥、统一模型菜单及切换诊断保留 |
| WorkBuddy | Windows 自动配置，按精确 ID 合并去重并回读 |
| Trae CN | Windows 原有自动接入；当前宿主未启用 CDP 时提供界面引导 |
| OpenCode | Windows/macOS/Linux，检测 CLI 后自动合并用户级 JSON、备份及回读 |
| Cursor、Cline、Roo Code、Continue、Aider | 对应客户端的配置引导和脱敏参数；不伪装成一键自动写入 |
| Claude Code | 说明 Anthropic 协议条件；不将 OpenAI Chat 地址直接写入冒充兼容 |
| 其他智能体 | 支持 OpenAI 兼容 provider 才能采用相应参数，受客户端实际能力限制 |

完整步骤见 [客户端兼容说明](yl-han-token/references/agent-compatibility.md)。Python 3.10+；Codex/WorkBuddy 所需 tomlkit 已内置；Trae 首次自动配置可能需要联网安装 Playwright，但不下载浏览器。不自动退出当前客户端。

## CLI 与输入

自然语言由智能体整理成 JSON；程序读取 stdin，不在命令参数中接收 Key。所有路径相对下载的技能目录：

```shell
python -X utf8 yl-han-token/scripts/hantoken.py apply --agent Codex --stdin
python -X utf8 yl-han-token/scripts/hantoken.py apply --agent WorkBuddy --stdin
python -X utf8 yl-han-token/scripts/hantoken.py apply --agent Trae --stdin
python -X utf8 yl-han-token/scripts/connect.py apply --agent OpenCode --stdin
python -X utf8 yl-han-token/scripts/connect.py guide --agent Cline --stdin
```

安全输入来源为智能体工具提供的标准输入，或你本机的私有配置文件 `--input <文件>`；不要把实际 Key 写入 shell 命令、项目文件或 Git。配置本身及备份保存在客户端私有目录，并包含运行需要的凭据。

stdin 示例（所有占位值需替换）：

```json
{"apiKey":"YOUR_API_KEY","models":["EXACT_MODEL_ID_A","EXACT_MODEL_ID_B"]}
```

也接受 `{"id":"EXACT_MODEL_ID","key":"YOUR_API_KEY"}`、`modelId`/`model` 别名、可选 `url`/`baseUrl`，以及原有完整对象数组。共用 Key 的模型分别保留；同精确 ID 重复时最后一项生效。默认地址按客户端补全，显式 URL 优先。WorkBuddy 自动使用完整 Chat Completions 地址。

Codex 与 OpenCode 默认将本批第一项设为默认；仅添加时加 `--keep-default`。WorkBuddy/Trae 不据此切换当前聊天选项。图片、语音、视频模型不能直接作为通用编程 Agent 主模型。

## 验证与脱敏

```shell
python -X utf8 yl-han-token/scripts/connect.py probe --agent OpenCode --stdin
python -X utf8 yl-han-token/scripts/connect.py probe --agent OpenCode --stdin --chat
python -X utf8 yl-han-token/scripts/hantoken.py self-check
```

默认 probe 只请求模型列表，不产生生成费用；只能证明鉴权和模型可见。`--chat` 是一次最小文字请求（8 个输出 token，无自动重试），可能产生少量费用，不能代替完整工具调用验收。空输出会明确标记，推理模型可能需要更多预算，技能不会静默加量。错误只保留 HTTP 状态和类别，不输出原始敏感响应；拒绝认证请求重定向。

原有客户端的深入诊断、恢复默认、同会话切换流程见 [工作流](yl-han-token/references/workflows.md) 和 [诊断](yl-han-token/references/diagnostics.md)。历史上游验证案例见 [网站案例库](https://www.hanrouter.ai/model-cases/)，标记已下架的模型不可作为可用目录推荐。

## 开发验证

```shell
python -m unittest discover -s yl-han-token/tests
```

离线测试覆盖默认地址、批量和大小写、配置合并/幂等/备份、无效配置保护、最小响应与错误脱敏；不修改开发者实际客户端配置，不使用真实 Key。
