> 新版支持对话 Key + 精确模型 ID、默认地址补全、批量模型和 OpenCode。总览见仓库根 README；跨客户端步骤见 [兼容说明](references/agent-compatibility.md)。以下为原有 Windows 客户端详细操作；原有 GUI 仍接受完整 JSON。

# yl-han-token 使用说明与调用案例

给 Codex、WorkBuddy、Trae CN 添加兼容 OpenAI 接口的自定义模型。支持单个模型和批量输入，保留其他已有模型及客户端自带模型。

Codex 默认将本次导入的第一项设为默认模型，项目界面的「同时将第一项设为默认模型」默认勾选。若只想添加模型，聊天中明确写「保留当前默认模型」或「只添加不切换」，命令行加 `--keep-default`，界面取消勾选即可。「保留其他已有模型」表示保留列表，不等同于保留默认选择。WorkBuddy 和 Trae 的导入不自动切换当前聊天模型。

## 在聊天中调用技能

在支持技能的客户端中输入 `$yl-han-token`，然后附上需求和模型 JSON。以下密钥均为占位内容，使用时替换为你自己的密钥。

### 只给密钥和模型 ID（URL 用默认）

不想贴 JSON 时，可在对话里直接说明密钥和模型 ID（单个或多个都行），技能会自动补全配置并导入，无需再提供接口地址。默认地址：WorkBuddy 用完整地址 `https://api.hanrouter.ai/v1/chat/completions`，Codex 与 Trae CN 用基础地址 `https://api.hanrouter.ai/v1`。例如：

```text
$yl-han-token 配置 WorkBuddy。key 是 sk-你自己的密钥，模型 ID 是 qwen3.7-flash，保留其他已有模型。
```

多个模型可逐个列出 ID 和密钥。密钥只经标准输入传入，不在回复或报告中明文出现；报告会说明本次使用的默认地址，地址不同时给出真实地址再导入即可（按 ID 更新，不产生重复）。若本身提供了 URL，则以提供的为准。

### 案例一：配置当前客户端

```text
$yl-han-token 帮我添加这个自定义模型，保留其他已有模型。
{
  "id": "qwen3.7-flash",
  "url": "https://api.hanrouter.ai/v1",
  "apiKey": "填写你自己的密钥"
}
```

未指定客户端时，默认配置调用技能的宿主：在 Codex 中调用就配置 Codex，在 Trae CN 中调用就配置 Trae CN。在 WorkBuddy 中使用这个案例时，将 URL 改为完整请求地址 `https://api.hanrouter.ai/v1/chat/completions`。

Trae CN 作为宿主且未开调试端口时，技能会引导你在当前窗口手动添加，无需关闭。若选择自动配置，则需要从外部入口退出并重新启动 Trae，当前聊天无法在窗口关闭后继续执行。

### 案例二：明确配置 WorkBuddy

```text
$yl-han-token 配置 WorkBuddy，保留其他已有模型。
{
  "id": "agnes-2.5-flash",
  "url": "https://apihub.agnes-ai.com/v1/chat/completions",
  "apiKey": "填写你自己的密钥"
}
```

显式指定的客户端优先。因此，在其他客户端中发出这条请求，也只配置 WorkBuddy。

### 案例三：明确配置 Trae CN

```text
$yl-han-token 配置 Trae CN。
{
  "id": "agnes-2.5-flash",
  "url": "https://apihub.agnes-ai.com/v1",
  "apiKey": "填写你自己的密钥"
}
```

这条案例也可以在 Trae 自己的聊天窗口中提出：已有可用调试端口时自动处理，否则按下面的步骤手动添加。需要自动配置时，可在 Codex 或 WorkBuddy 中执行并明确指定目标为 Trae CN。

#### 在 Trae 当前窗口内手动添加（无需关闭）

1. 点击聊天输入框附近的当前模型名称，展开模型下拉菜单，点击底部「添加模型」。也可以直接进入设置中的「模型」页。
2. 在「模型」设置页，点击「+ 添加模型」。
3. 在服务商选择弹窗中，点击顶部「自定义模型」。
4. 填写下表各项，然后点击右下角「添加模型」，等待客户端的连通性检查。

| 表单项 | 上面 Agnes 案例的填写内容 |
| --- | --- |
| API 格式 | 选择「OpenAI Chat Completions 格式」，此选择适用于该案例的接口；其他接口按服务商实际协议选择 |
| 自定义请求地址 | `https://apihub.agnes-ai.com/v1`，关闭「完整 URL」开关；客户端会补 `/chat/completions` |
| 模型 ID | `agnes-2.5-flash`，与服务商提供的 ID 完全一致 |
| 模型显示名称 | 可留空使用模型 ID，或填写自定义名称；无需添加「自定义模型 / 」前缀 |
| API 密钥 | 粘贴你自己的原始 API Key；不要填文档中的占位文字 |

如果提供的是完整请求地址，开启「完整 URL」后填写完整地址，避免重复补路径。以 HanRouter 的 `qwen3.7-flash` 为例，基础地址填 `https://api.hanrouter.ai/v1`、关闭「完整 URL」，模型 ID 填 `qwen3.7-flash`。

添加成功后返回模型列表，确认自定义项已启用，再返回聊天中的模型下拉菜单选择它。批量输入时逐个添加；遇到“已存在”先检查原条目，保留其他模型和内置模型。失败时根据界面提示检查地址、ID、密钥及账号权限。

技能在当前窗口提供引导时，应按你本次输入列出地址、ID 和可选显示名，密钥仅提示使用你已提供的原值；提供步骤不代表已完成配置或通过检查。

#### 需要工具自动配置时

普通启动且未开启调试端口时，自动配置需要先完全关闭 Trae；关闭会中断其中的 Agent，不能依靠原聊天窗口完成这次重启配置。操作顺序：

1. 先在 Trae 之外准备入口：Codex / WorkBuddy 中调用技能并明确“配置 Trae CN”，或打开本工具 GUI、Windows 独立 PowerShell。Trae 内置的集成终端不作为外部入口。
2. 保存工作，完全退出 Trae CN，包括托盘中的后台实例。
3. 在外部入口执行导入或点击“校验并写入”。工具会以调试模式自动启动 Trae，随后完成添加和连通性检查。

**独立配置窗口也支持先点击、再退出 Trae，自动接着配置：**

1. 在独立 PowerShell 启动本工具，或从 Trae 之外打开 GUI，选择 Trae CN 并粘贴配置。
2. 点击一次「校验并写入」。如果 Trae 正在运行但没开调试端口，窗口会显示「等待 Trae CN 退出」，保留本批输入，不立即报写入失败。
3. 保存工作并完全退出 Trae（含托盘），保持本配置窗口开启。
4. 工具检测到退出后，自动以调试模式重新启动 Trae，继续填表、检查和导入。无需重新粘贴、再次点击或手动重开 Trae。

等待退出最多 3 分钟；超时会提示，输入框内容仍保留，退出 Trae 后可重新点击。已有可用调试端口则直接导入。此流程适用于独立配置窗口；Trae 自己的聊天窗口仍使用前述手动引导或外部入口。

若 Trae 已通过调试模式启动且 CDP 可用，则可直接复用，无需再次关闭。工具自动添加会执行原生连通性检查；重复模型提示后跳过，保留旧配置。

### 案例四：批量配置 Codex

```text
$yl-han-token 给 Codex 批量添加以下模型，默认使用第一项。
[
  {
    "id": "glm-5.3-flash",
    "url": "https://api.hanrouter.ai/v1",
    "apiKey": "填写你自己的密钥"
  },
  {
    "id": "qwen3.7-flash",
    "url": "https://api.hanrouter.ai/v1",
    "apiKey": "填写你自己的密钥"
  }
]
```

不同模型 ID 分别导入，共用地址或密钥不会将它们合并。模型名称可通过可选的 `name` 字段指定；WorkBuddy 和 Trae 省略时使用 ID，不额外添加“自定义模型”前缀。

上述 Codex 批量案例成功后，默认模型为第一项 `glm-5.3-flash`。需要保留原默认时，将请求改为「批量添加以下模型，保留当前默认模型」。高级输入可在某项设置 `"codex": {"setDefault": true}` 显式选择该项，显式的 `setDefault` 设置优先于自动选择第一项；一批最多选一个默认模型。命令行 `--keep-default` 会覆盖输入中的默认设置，保留当前选择。

这些案例展示输入方式；模型是否由服务商提供、当前账号是否有权限，以实际检查结果为准。

## 地址与重复项规则

| 客户端 | URL 示例 | 自定义模型重复项处理 |
| --- | --- | --- |
| Codex | `https://api.hanrouter.ai/v1` | 按实际端点、协议、模型及账号识别；不同账号保留 |
| WorkBuddy | `https://api.hanrouter.ai/v1/chat/completions` | 按去掉首尾空格、区分大小写的 ID 更新，同 ID 保留最后一条 |
| Trae CN | `https://api.hanrouter.ai/v1` | 使用客户端原生存在检查，已存在则跳过 |

URL 会清理首尾空格和外层 Markdown 链接，不会自动补 `/v1` 或 `/chat/completions`。上述去重只处理自定义配置，不修改自带模型。

## 直接运行命令

需要 Windows、Python 3.10+ 和已安装的目标客户端。技能自带运行代码与 tomlkit，无需克隆项目。Trae 需要 Playwright；入口优先复用已有环境，缺少依赖时自动准备，不下载浏览器。

以下 PowerShell 示例使用 Codex 的常见技能安装位置。安装到其他目录时，把 `$entry` 改为实际的 `scripts/hantoken.py` 路径；运行命令不要求切换工作目录。

```powershell
$entry = Join-Path $env:USERPROFILE '.codex\skills\yl-han-token\scripts\hantoken.py'
python -X utf8 $entry --help
```

首次安装、迁移或排查文件损坏时自检：

```powershell
python -X utf8 $entry self-check
```

打开界面粘贴配置，客户端任选一个：

```powershell
python -X utf8 $entry gui --agent WorkBuddy
python -X utf8 $entry gui --agent Trae
python -X utf8 $entry gui --agent Codex
```

从你已准备好的本地 JSON 文件导入，文件内容可以是单个对象或数组：

```powershell
python -X utf8 $entry apply --agent WorkBuddy --input 'C:\私有配置\workbuddy-models.json' --timings
python -X utf8 $entry apply --agent Trae --input 'C:\私有配置\models.json' --timings
python -X utf8 $entry apply --agent Codex --input 'C:\私有配置\models.json' --timings
```

Codex 仅添加、保留当前默认选择：

```powershell
python -X utf8 $entry apply --agent Codex --input 'C:\私有配置\models.json' --keep-default
```

WorkBuddy 文件填写完整请求地址，其他两个示例文件填写基础地址。命令行调用始终显式传 `--agent`；宿主自动选择是技能在聊天中的规则，直接运行脚本时不能依赖它识别宿主。

工具支持安全传递标准输入时，可使用 `apply --agent <目标> --stdin`。密钥放在标准输入、GUI 或用户自己的私有配置文件中，不放到命令参数、脚本示例或报告里。配置文件含密钥，不要提交到代码仓库或打包分享。

`--timings` 在标准错误中输出阶段名称和耗时，不输出配置内容。常规导入内部已检测客户端，无需每次先执行 `self-check` 或 `detect`。WorkBuddy 内容未变化时，校验后跳过写入和备份。

找不到客户端时可单独检测，并通过环境变量指定实际程序位置：

```powershell
$env:WORKBUDDY_EXE = 'D:\Apps\WorkBuddy\WorkBuddy.exe'
python -X utf8 $entry detect --agent WorkBuddy
```

Trae CN 和 Codex 对应 `TRAE_CN_EXE`、`CODEX_EXE`。这里只设置当前 PowerShell 进程的环境变量。

## 诊断与验收案例

聊天中可以这样提出明确的排查需求：

```text
$yl-han-token 检查 Codex 里 qwen3.7-flash 的实际调用路由，先诊断并给出结果。
```

命令行查看 Codex 已安装路由的脱敏诊断：

```powershell
python -X utf8 $entry diagnose --agent Codex
```

需要完整切换验收时，在已配置的模型中选择精确 ID。下面的 `实际常规模型ID` 必须替换为当前目录中真实存在的 ID：

```powershell
python -X utf8 $entry switch-check --agent Codex --models '实际常规模型ID' 'glm-5.3-flash' 'qwen3.7-flash' '实际常规模型ID' --report '.\switch-report.json'
```

该操作会实际调用模型，使用隔离会话验证工具调用、最终回答及模型 ID。普通导入不需要额外执行完整切换回归。

结果含义：WorkBuddy 导入成功表示配置已回读校验；Trae 添加成功表示原生连通性检查通过；Codex 桌面启用成功表示必需兼容性检查通过。磁盘配置更新是否已被运行中的客户端加载，需要在客户端确认。

进一步的配置与验证细节见 [配置流程](references/workflows.md)，故障定位见 [诊断说明](references/diagnostics.md)，技能执行规则见 [SKILL.md](SKILL.md)。
