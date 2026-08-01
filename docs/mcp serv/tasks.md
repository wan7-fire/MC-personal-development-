# ZXCode 外部 MCP 服务器接入任务

模块统一放在 `zxcode/mcp/`，测试放在 `tests/`。

## 任务 1：JSON-RPC 消息模型与编解码

- **目标**：定义请求/响应/错误/通知四种消息的判别与校验，id 生成与回显，畸形消息的容错解析。
- **影响文件**：`zxcode/mcp/__init__.py`、`zxcode/mcp/protocol.py`、`tests/test_mcp_protocol.py`
- **依赖任务**：无
- **参考资料定位**：MCP 规范 https://modelcontextprotocol.io/specification/2025-06-18 （Base Protocol / JSON-RPC 消息）；JSON-RPC 2.0 https://www.jsonrpc.org/specification ；`zxcode/tools/base.py:31-47` 的 ToolResult 错误结构（code + message 约定）；`zxcode/tools/base.py:24-29` 的 ToolCall id 语义

## 任务 2：请求-响应异步匹配器

- **目标**：实现挂起请求表，按 id 关联响应；并发安全；未知 id 响应与过期响应被忽略；超时后清理并触发取消通知。
- **影响文件**：`zxcode/mcp/matcher.py`、`tests/test_mcp_matcher.py`
- **依赖任务**：任务 1
- **参考资料定位**：`zxcode/tools/executor.py:18-47` 的 asyncio 并发批处理模式；`zxcode/agent.py:378` 起 `_cancellable` 的取消与清理语义；MCP 规范「Lifecycle / Timeouts」：所有请求必须设超时，超时后应停止等待

## 任务 3：stdio 传输

- **目标**：启动本地子进程，按换行分隔读写 JSON-RPC 消息；stderr 只进日志；超时与进程清理（关 stdin → 等待退出 → 终止进程树）。
- **影响文件**：`zxcode/mcp/transports/__init__.py`、`zxcode/mcp/transports/stdio.py`、`tests/test_mcp_stdio.py`
- **依赖任务**：任务 1
- **参考资料定位**：`zxcode/tools/shell.py:103`（create_subprocess_exec）、`zxcode/tools/shell.py:141-142`（taskkill /T）、`zxcode/tools/shell.py:155`（超时清理）；`tests/test_shell_tool.py` 的进程清理用例；MCP 规范「Transports / stdio」：换行分隔 JSON、stderr 仅日志、关闭 stdin 后等待退出

## 任务 4：Streamable HTTP 传输

- **目标**：通过 POST 发送 JSON-RPC 请求，Accept 头兼容 JSON 与 SSE 响应；维护会话 id 与协议版本请求头；请求超时；在 `pyproject.toml` 显式声明 httpx 依赖（openai 已传递依赖，不新增第三方包）。
- **影响文件**：`zxcode/mcp/transports/http.py`、`pyproject.toml`、`tests/test_mcp_http.py`
- **依赖任务**：任务 1
- **参考资料定位**：MCP 规范「Transports / Streamable HTTP」：POST + `Accept: application/json, text/event-stream`、`Mcp-Session-Id`、`MCP-Protocol-Version`；`zxcode/client.py:48-55`（httpx 上层 timeout 设定先例）；`tests/test_client.py:18-56` 的仿造流模式

## 任务 5：会话层（握手与生命周期）

- **目标**：实现 initialize 握手、协议版本协商、initialized 通知、连接状态机；版本不兼容时按规范断开并给出明确失败。
- **影响文件**：`zxcode/mcp/session.py`、`tests/test_mcp_session.py`
- **依赖任务**：任务 2、任务 3、任务 4
- **参考资料定位**：MCP 规范「Lifecycle」：initialize 参数与响应形状、版本协商规则、initialized 通知、shutdown；`zxcode/state.py` 的状态机写法（Agent 循环状态机先例）

## 任务 6：配置解析与校验

- **目标**：解析项目根目录 `zxcode-servers.toml`，支持 stdio/HTTP 两种 server 定义、每 server 超时与工具清单、`${VAR}` 环境变量引用；非法配置给出明确启动错误。
- **影响文件**：`zxcode/mcp/config.py`、`tests/test_mcp_config.py`
- **依赖任务**：任务 5
- **参考资料定位**：`zxcode/security.py:219`（load_policy 的 TOML 读取与路径解析）、`zxcode/security.py:16`（配置文件名约定）；`zxcode/client.py:39-46`（from_env 的 env 读取与缺省报错风格）；`zxcode-security.toml` 现有格式

## 任务 7：连接池与生命周期管理

- **目标**：按 server 名缓存连接，懒连接复用；空闲回收；进程退出时统一关闭全部连接与子进程；失败后重连。
- **影响文件**：`zxcode/mcp/pool.py`、`tests/test_mcp_pool.py`
- **依赖任务**：任务 5、任务 6
- **参考资料定位**：`zxcode/app.py:100-125`（依赖装配处，注入点）；MCP 规范「Lifecycle / Shutdown」：stdio 关 stdin 等退出、HTTP 关连接

## 任务 8：适配层与注册

- **目标**：把 server 的工具列表映射为本地 Tool 接口（描述、input schema、只读性、`<server>_<tool>` 前缀），注册进注册表；tools/call 调用与结果（content/isError/structuredContent）映射为 ToolResult；复用现有输出截断。
- **影响文件**：`zxcode/mcp/adapter.py`、`tests/test_mcp_adapter.py`
- **依赖任务**：任务 5、任务 6
- **参考资料定位**：`zxcode/tools/base.py:49-90`（Tool 抽象与 ToolRegistry.register/definitions）、`zxcode/tools/base.py:53-54`（read_only / timeout_seconds）；`zxcode/tools/executor.py:13` 与 `:86`（输出截断）；MCP 规范「Server Features / Tools」：tools/list、tools/call、annotations/readOnlyHint、isError/structuredContent

## 任务 9：安全集成与错误映射

- **目标**：远端写工具复用现有安全策略（确认/拒绝、plan-only 拦截），支持每 server 禁用清单与显式只读清单；JSON-RPC 错误码映射为统一错误码；敏感值不落日志。
- **影响文件**：`zxcode/mcp/security.py`、`zxcode/mcp/errors.py`、`tests/test_mcp_security.py`
- **依赖任务**：任务 8
- **参考资料定位**：`zxcode/dispatch.py:75-107`（_pre_hook 的 plan-only 与 security 拦截点）、`zxcode/security.py:141`（guard_call）；`zxcode/tools/executor.py:70-83`（timeout / execution_error 错误码先例）；MCP 规范「Security and Trust & Safety」：工具标注不可信、敏感操作需用户确认

## 任务 10：接入主流程

- **目标**：应用启动时加载配置、构建连接池、注册远端工具；退出时统一关闭；LLM 无感调用；更新 README 与依赖说明。
- **影响文件**：`zxcode/app.py`、`zxcode/__main__.py`、`zxcode/mcp/__init__.py`、`README.md`、`tests/test_app.py`
- **依赖任务**：任务 7、任务 8、任务 9
- **参考资料定位**：`zxcode/app.py:111-121`（注册表与 Agent 装配）、`zxcode/app.py:218` 起（generate 流程）；`zxcode/__main__.py:9-14`（main 启动入口）；`tests/test_app.py:17-43`（FakeClient 模式）、`:119` 起（无头 UI 测试模式）

## 任务 11：端到端验证

- **目标**：用仿造 stdio server 与仿造 HTTP server 完成「握手 → 发现 → 调用 → 结果回传 → 模型最终回复」全流程；跑全量测试并勾选 checklist；真实 MCP server 冒烟（可选、手动）。
- **影响文件**：`tests/test_mcp_e2e.py`、`docs/mcp serv/checklist.md`
- **依赖任务**：任务 10
- **参考资料定位**：`tests/test_app.py:67-107`（ToolCallingClient 完整工具循环）、`tests/test_client.py:18-56`（仿造流模式）；`docs/mcp serv/checklist.md` 全部条目；MCP 规范「Lifecycle」全流程
