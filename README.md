# 汉路特自定义模型接入 Skill

`yl-han-token` 用于在 Codex、WorkBuddy、Trae CN 中添加单个或批量自定义模型，自带运行代码及 Codex / WorkBuddy 所需的 tomlkit 依赖。

## 安装

需要 Node.js / npx。将下面的提示词发给支持 Skill 的 Agent：

```text
运行 npx --yes skills add "https://github.com/13850073218/yl-han-token" --skill "yl-han-token" --global --yes，确认 yl-han-token Skill 已为当前 Agent 完成安装，并报告实际安装路径及使其生效所需的重新加载或重启操作。安装后检查运行环境，等待我提供模型配置。
```

也可以直接在终端运行：

```shell
npx --yes skills add "https://github.com/13850073218/yl-han-token" --skill "yl-han-token" --global --yes
```

如安装器未识别目标客户端，可从仓库下载 `yl-han-token/` 完整目录，按该客户端的技能目录规范安装，并重新加载技能。安装位置以安装器实际输出为准。

## 运行要求

- Windows，Python 3.10 或以上，已安装目标客户端。
- Codex / WorkBuddy 不需要另外 pip 安装依赖。
- Trae CN 自动配置首次需要安装 Playwright；其运行时会在技能目录建立独立环境，不下载浏览器。
- 未开启调试端口的 Trae CN 可以在当前聊天中按引导手动添加；自动配置需从外部入口执行，保存工作后退出 Trae 再继续。

## 使用

在 Agent 聊天中输入以下内容，替换模型 ID 和自己的密钥：

```text
$yl-han-token 为当前客户端添加此模型，保留当前默认模型。
{
  "id": "YOUR_MODEL_ID",
  "url": "https://api.hanrouter.ai/v1",
  "apiKey": "YOUR_API_KEY"
}
```

WorkBuddy 的 `url` 使用完整地址 `https://api.hanrouter.ai/v1/chat/completions`。明确指定“配置 Codex”“配置 WorkBuddy”或“配置 Trae CN”可选择目标；未指定时按调用技能的宿主处理。批量配置可提供 JSON 对象数组。

Codex 若未要求保留当前默认，技能默认使用本次第一项作为默认模型。配置前工具会备份；可用功能取决于所选模型的实际协议和工具调用能力。连接验证可能产生接口用量。

## 自检

安装后将下面的路径替换为实际技能目录：

```shell
python -X utf8 "<技能目录>/scripts/hantoken.py" self-check
```

自检验证文件完整性、输入解析、请求转换和随附依赖，不改动客户端配置，也不调用付费模型。`self-check` 通过不代表第三方接口连通或全部模型可用。

完整的单个 / 批量输入、GUI、诊断及恢复案例见 [技能使用说明](yl-han-token/README.md)。[技能入口](yl-han-token/SKILL.md) 与运行代码均位于 `yl-han-token/`。

请在本地填写真实密钥，不要提交到 GitHub、截图或公开分享。

## 随附依赖

随附 tomlkit，其许可证保留于 `yl-han-token/scripts/vendor/tomlkit-LICENSE`。
