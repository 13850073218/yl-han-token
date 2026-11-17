# 客户端接入兼容说明

按客户端选择本节，不把一种客户端的配置结构直接复制给另一种。以下支持范围描述的是本技能实现，不承诺所有客户端版本的 UI 一致。

## 支持矩阵

| 客户端 | 技能安装/加载 | 接入方式 | 系统与验证边界 |
| --- | --- | --- | --- |
| Codex | 原生 Skill | `hantoken.py apply --agent Codex` | Windows 自动配置，原有本地协议桥与桌面验证 |
| WorkBuddy | 支持 Skill 的版本按其安装规则加载 | `hantoken.py apply --agent WorkBuddy` | Windows，备份、合并、回读；网络验证另计 |
| Trae CN | 按客户端技能目录规则加载 | 原有自动入口或界面添加 | Windows；当前窗口未启用 CDP 时不能自关闭后继续 |
| OpenCode | 原生 Agent Skills 或安装器识别的目录 | `connect.py apply --agent OpenCode` | 跨平台 OpenCode CLI；用户级 JSON 写入、回读 |
| Cursor | 支持 Agent Skills 的版本；否则读取本说明 | OpenAI 兼容设置页 | 引导配置；版本/套餐可能限制自定义地址与模型 |
| Cline / Roo Code | 各扩展支持的 Skill 或明确加载文件 | OpenAI Compatible provider | 引导配置；工具能力须客户端实测 |
| Continue | 按当前版本 Skill/规则支持读取 | 配置 YAML 的 OpenAI provider | 引导配置；不自动覆盖既有 YAML |
| Aider | 使用者/宿主读取本技能 | OpenAI compatible model 与环境变量 | 引导配置；无原生 Skill 安装承诺 |
| Claude Code | 可加载技能，但模型提供商是另一问题 | 需要兼容的 Anthropic Messages 服务 | 当前包不自动修改；OpenAI Chat 地址不能直接代替 |
| 其他 | 能读 SKILL.md 即可理解流程 | 查目标客户端的自定义 provider 能力 | 不支持自定义 provider 的产品不能仅靠本技能突破 |

## Codex / WorkBuddy / Trae CN

参阅 [workflows.md](workflows.md)。stdin JSON 可省略 URL；程序补全汉路特默认地址。原来的 GUI、诊断、恢复默认和同会话切换工具继续保留。

Trae 当前窗口手动配置：模型下拉 → 添加模型 → 模型设置 → 自定义模型；API 格式选 OpenAI 兼容、基础地址 `https://api.hanrouter.ai/v1`、ID 填用户精确 ID，Key 填本轮提供的原值。实际界面名称依版本为准。未操作不得声称已配置。

## OpenCode

要求 `opencode` CLI 已安装。通过 stdin 执行：

```text
python -X utf8 <技能目录>/scripts/connect.py apply --agent OpenCode --stdin
```

JSON：`{"apiKey":"YOUR_API_KEY","models":["EXACT_MODEL_ID"]}`。示例占位符需要替换，不作为真实请求。

用户级配置默认 `~/.config/opencode/opencode.json`；设置 `XDG_CONFIG_HOME` 时遵守该目录。写入 `provider.<稳定ID>.npm=@ai-sdk/openai-compatible`、`options.baseURL`、`options.apiKey`、`models.<精确ID>`，并按需设置默认 `model=<provider>/<id>`。每个 ID 使用独立 provider，允许不同模型使用不同 Key/URL。重复导入更新同一 ID，保留其他 provider、选项和模型元数据。只读 JSON，不覆盖 JSONC；有 `opencode.jsonc` 或解析失败时保持原文件。项目级配置或 `OPENCODE_CONFIG` 可能覆盖用户级配置，实际运行时须核对选择的 provider；本技能不会为绕过它擅改项目。

备份与配置含实际 Key，留在用户私有配置目录，勿上传。POSIX 使用 0600，Windows 文件权限遵守本机账户 ACL，chmod 不等同于 Windows ACL 加固。回滚时从本次备份恢复原文件；首次创建则移除本次 provider，并依需恢复默认选择。不要删除整个配置目录。

## Cursor

在模型设置中启用自定义 OpenAI API Key，并使用其支持的 Override OpenAI Base URL / 自定义地址选项，基础地址填 `https://api.hanrouter.ai/v1`，添加精确 ID，选择对应自定义模型。Key 填用户原值。版本不支持自定义地址、ID 或相关工具时说明限制，不修改内部数据库或系统文件。先验证设置，再新开一次最小客户端请求；API 探测通过不能代替 Cursor 内的工具验证。

## Cline / Roo Code

在扩展 Provider 设置中选择 OpenAI Compatible（中文名称随版本变化），Base URL 填 `https://api.hanrouter.ai/v1`，API Key 使用用户原值，Model ID 精确填写。上下文长度、输出上限、视觉/工具能力只采用已验证资料，不按模型名称猜测。不将本技能的演示预算 8 token 写为 Agent 的永久输出上限。

## Continue

当前 YAML 配置常用条目如下；由智能体按已安装版本和现有结构合并，不覆盖其他模型。密钥存于 Continue 支持的 secret 存储，本例引用其 secrets 插值，需依当前版本建立 `HANROUTER_API_KEY`，不要把该占位符当成已保存凭据。

```yaml
models:
  - name: HanRouter Custom
    provider: openai
    model: EXACT_MODEL_ID
    apiBase: https://api.hanrouter.ai/v1
    apiKey: ${{ secrets.HANROUTER_API_KEY }}
```

旧版 JSON 配置与新版 YAML 不通用；先识别实际配置格式与 secret 功能再合并。未实施只报告引导。

## Aider

在用户私有环境中设置 `OPENAI_API_BASE=https://api.hanrouter.ai/v1` 与 `OPENAI_API_KEY=<用户原值>`，启动 `aider --model openai/EXACT_MODEL_ID`。Key 通过工具安全的进程环境传递或由用户在本机私密输入，不能嵌入 shell 历史或报告。LiteLLM 的 `openai/` 是 provider 前缀，发送上游的 ID 仍为原值。是否支持编辑与工具调用须实测，不伪造元数据。

## Claude Code / 非 OpenAI 协议

Claude Code 使用 Anthropic Messages。`/v1/chat/completions`、Codex Responses 与 `/v1/messages` 是不同协议。用户只给 HanRouter Key/模型 ID 时不能直接写入 `ANTHROPIC_BASE_URL` 并宣称可用。先核实网关是否为该模型公开 Messages 端点及其鉴权规范；未有证据时建议使用已支持的 OpenAI 兼容客户端，或者在用户指定后使用可靠协议桥。本包不自动安装未验证桥接器，不更改 Claude Code 登录和默认配置。

## 验证和错误

- `probe`：仅 GET `/models`，精确大小写匹配。返回不含原始 Key。
- `probe --chat`：仅文字模型，POST `/chat/completions`，`max_tokens=8`，无重试；推理模型可能用完预算无正文，结果会标记未通过输出验证，而不是判定上游彻底故障。
- 401 鉴权失败、403 权限不足、404 路径/模型不匹配、429 额度/限流、5xx 上游错误；不自动换其他型号、增加预算或扫描其他模型。
- 探测拒绝 HTTP 重定向，避免认证头随跳转传给其他域名。显式 URL 禁止用户信息、查询参数和 fragment；HTTPS 或本机回环 HTTP。
- 图片/视频/语音 ID 不可直接作为编程 Agent 主模型。需要使用这些能力时参考 [真实请求案例](https://www.hanrouter.ai/model-cases/)；历史“已下架”条目不可作为当前可用型号推荐。
