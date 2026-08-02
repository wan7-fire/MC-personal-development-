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
- `/compact`：手动触发上下文压缩（一层落盘 + 二层摘要）。
- `/exit`：退出。

## 内置工具

启动后默认向支持 tool calling 的模型提供 `ReadFile`、`WriteFile`、`EditFile`、`Bash`、`Glob`、`Grep`。新建文件自动执行；覆盖、编辑以及非明确只读的 PowerShell 命令会在终端中逐次请求批准。

## 安全策略

项目根目录的 `zxcode-security.toml` 控制写入和执行类工具的第一版安全策略，当前覆盖 `Bash`、`WriteFile`、`EditFile`。`mode` 支持 `strict`、`default`、`allow` 三档；硬黑名单和路径沙箱始终生效。

确认弹窗支持“本次允许 / 本会话允许 / 永久允许 / 拒绝”。选择“永久允许”会把精确命令签名或精确路径签名写回 `zxcode-security.toml`，例如反复运行同一条 `git commit` 时不会每次都重新询问。

## 上下文压缩

工具结果是上下文消耗的大头。ZXCode 用两层机制控制历史体积：

- 单条工具结果超过阈值（默认 8192 字符）时，完整内容按内容哈希写入 `.zxcode/spool/`，对话中只保留预览与读取路径；同一轮工具结果合计超限（默认 32768 字符）时从最大开始依次落盘。
- 历史逼近窗口上限（默认按 128k token 估算，80% 触发）时，自动调用模型生成固定栏目（主要请求、关键概念、文件代码、错误修复、解决过程、用户原话、待办、当前工作、下一步）的结构化摘要，替换最早的轮次，并紧跟一条边界消息提示模型按需重读文件。用户原始消息在「用户原话」栏目中逐字保留。
- 摘要调用不带工具定义，且连续失败会自动熔断（默认 2 次），停止自动触发；`/compact` 可随时手动触发，不受熔断限制。

## 外部 MCP 服务器

ZXCode 可以作为 MCP 客户端连接外部工具服务器：配置放在项目根目录的 `zxcode-servers.toml`，支持本地子进程（stdio）与远程 Streamable HTTP 两种传输。启动后自动完成初始化握手、拉取工具列表，并把远端工具注册为 `<server>_<工具名>`，Agent 可以像调用内置工具一样无感调用。

```toml
[[servers]]
name = "local"
transport = "stdio"
command = ["python", "-m", "my_mcp_server"]
env = { TOKEN = "${MY_TOKEN}" }

[[servers]]
name = "remote"
transport = "http"
url = "https://example.test/mcp"
headers = { Authorization = "Bearer ${REMOTE_TOKEN}" }
call_timeout_seconds = 30
```

敏感值一律通过 `${环境变量}` 引用，不写入配置文件明文。写类远端工具会先经过现有安全策略与确认弹窗；`trusted = true` 且服务器声明只读标注（或配置 `read_only_tools` 显式列出）的工具按只读并发放行。

## 提示词分层

ZXCode 将稳定的全局指令放在请求最前面，将工作目录、系统、时间和 Git 摘要等动态环境信息放在后续独立消息中。这样兼容 prompt caching 的服务可以自然复用稳定前缀；API Key 仍只从当前进程环境读取，不会写入提示词或项目文件。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
