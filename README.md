# ZXCode

一个使用 OpenAI 兼容 API 的 Python 终端多轮对话客户端。

## 安装

```powershell
.\.python\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

## 配置

```powershell
$env:LLM_API_KEY = "你的 API Key"
$env:LLM_BASE_URL = "https://你的服务地址/v1"
$env:LLM_MODEL = "你的模型名"
```

API Key 仅从当前进程环境读取，不写入项目文件。

## 启动

```powershell
.\.venv\Scripts\python.exe -m zxcode
```

## 操作

- `Enter`：在输入区换行。
- `Ctrl+S`：在 Windows 中发送消息；支持增强键盘协议的终端也可使用 `Ctrl+Enter`。
- `Ctrl+C`：生成中取消当前回答；空闲时退出。
- `/help`：显示命令帮助。
- `/clear`：清空当前会话。
- `/model <名称>`：切换模型并保留当前会话。
- `/plan`：切换 plan-only 模式。开启后写类工具一律被拦截，模型只读取和搜索并给出一份行动计划，收尾列出被拦截的写操作。
- `/exit`：退出。

## 内置工具

启动后默认向支持 tool calling 的模型提供 `ReadFile`、`WriteFile`、`EditFile`、`Bash`、`Glob`、`Grep`。新建文件自动执行；覆盖、编辑以及非明确只读的 PowerShell 命令会在终端中逐次请求批准。

## 安全策略

项目根目录的 `zxcode-security.toml` 控制写入和执行类工具的第一版安全策略，当前覆盖 `Bash`、`WriteFile`、`EditFile`。`mode` 支持 `strict`、`default`、`allow` 三档；硬黑名单和路径沙箱始终生效。

确认弹窗支持“本次允许 / 本会话允许 / 永久允许 / 拒绝”。选择“永久允许”会把精确命令签名或精确路径签名写回 `zxcode-security.toml`，例如反复运行同一条 `git commit` 时不会每次都重新询问。

## 提示词分层

ZXCode 将稳定的全局指令放在请求最前面，将工作目录、系统、时间和 Git 摘要等动态环境信息放在后续独立消息中。这样兼容 prompt caching 的服务可以自然复用稳定前缀；API Key 仍只从当前进程环境读取，不会写入提示词或项目文件。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
