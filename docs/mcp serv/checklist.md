# ZXCode 外部 MCP 服务器接入验收清单

## 配置解析与校验

- [x] 项目根目录存在 `zxcode-servers.toml`，可声明一个 stdio server（含 command、env、超时）与一个 HTTP server（含 url、headers、超时）。
- [x] server 名仅允许 `[A-Za-z0-9_-]`；含空格、点或其它字符的 server 名导致启动报错且退出码非 0。
- [x] 配置值中的 `${VAR_NAME}` 引用会被环境变量展开；引用未定义变量导致启动报错并指明变量名。
- [x] 缺失 server 名、未知传输类型、stdio 缺 command、HTTP 缺 url 时，启动报错并给出对应字段。
- [x] `pyproject.toml` 的 dependencies 精确包含 `openai`、`textual`、`httpx` 三项，未新增其它运行时依赖。

## 协议与 id 匹配

- [x] 发送的每个请求都含 `jsonrpc: "2.0"`、唯一 id、method、params；id 单调递增且进程内不重复。
- [x] 同一连接上两个并发请求乱序返回时，各自结果按 id 落到正确的挂起请求，不串扰。
- [x] 收到未知 id 的响应或 server 主动通知时连接不崩溃、挂起请求不受影响。
- [x] 收到畸形 JSON 或非法消息时返回结构化失败（`invalid_json`），连接保持可用。
- [x] 错误码映射符合约定：`-32700`→`invalid_json`、`-32600`→`invalid_request`、`-32601`→`unknown_method`、`-32602`→`invalid_arguments`、其它 JSON-RPC 错误→`remote_error`。

## stdio 传输

- [x] stdio server 以换行分隔 JSON 消息；子进程 stderr 内容只进日志，不进入工具结果。
- [x] 工具调用超时后连接保持可用并发出取消通知；关闭连接时子进程（含子进程树）在 1 秒内结束，无悬挂进程。
- [x] 关闭连接时先关闭 stdin，等待 server 退出；超过 5 秒未退出则终止进程树（参照 taskkill /T 先例）。

## Streamable HTTP 传输

- [x] 每个 POST 请求携带 `Accept: application/json, text/event-stream`。
- [x] 首次握手响应带会话 id 时，后续请求复用同一会话 id（`Mcp-Session-Id` 头）。
- [x] 握手后所有请求携带 `MCP-Protocol-Version: 2025-06-18` 请求头。
- [x] server 返回 SSE 流与返回 JSON 两种响应形式都能解析出完整结果。
- [x] HTTP 请求超时返回 `connection_error` 或 `timeout`，且不留下悬挂协程。

## 会话与生命周期

- [x] 首次工具调用前自动完成 initialize 握手与 `notifications/initialized`；之后才发 tools/list 与 tools/call。
- [x] 客户端首版支持 protocolVersion `2025-06-18`；server 回复其它版本时断开并返回 `handshake_failed`，不注册任何工具。
- [x] 同一 server 连续两次工具调用复用同一连接：stdio 子进程 pid 不变，HTTP 会话 id 不变。
- [x] 空闲超过 300 秒后连接被关闭；下一次调用自动重连并重新握手、刷新工具列表。
- [x] 连接失败后下一次调用能重新建立连接（stdio 重启子进程 / HTTP 重发握手），返回 `connection_error` 后不阻塞同批其它工具。
- [x] 应用退出时所有连接被关闭：stdio 子进程全部结束，HTTP 客户端会话全部关闭。

## 适配与注册

- [x] 远端工具注册名统一为 `<server>_<tool>`；两个 server 暴露同名工具时注册表同时包含两者且无冲突。
- [x] 注册表工具名集合 = 六个内置工具 + 所有启用的远端工具（前缀后），无缺失、无多余。
- [x] 远端工具的 LLM 可见定义包含 description 与 inputSchema（映射自 tools/list），Agent 无需感知来源即可调用。
- [x] tools/call 的文本内容进入 ToolResult.output；`isError: true` 的结果映射为失败结果。
- [x] 远端工具输出超过 65,536 字节时被截断，`metadata.truncated=true`，不产生非法 UTF-8。

## 安全与确认

- [x] 只读判定符合约定：server 配置 `trusted=true` 且工具标注 readOnlyHint，或配置的显式只读清单命中 → 只读；否则一律视为写工具。
- [x] 写类远端工具首次调用弹出确认，标题同时含 server 名与工具名；拒绝后返回 `permission_denied`。
- [x] plan-only 模式下写类远端工具被拦截，blocked 原因含 plan-only；只读远端工具照常执行。
- [x] 配置的禁用清单（disabled_tools）命中的工具不出现在注册表，调用返回 `unknown_tool`。
- [x] 日志、测试输出与会话历史中 grep 不到配置里使用的测试 token 明文。

## 超时、取消与并发

- [x] 默认握手超时 10 秒、工具调用超时 60 秒；per-server 超时覆盖生效。
- [x] 工具调用超时返回错误码 `timeout`，并发出取消通知（仿造 server 能记录收到 `notifications/cancelled`）。
- [x] 用户取消（Ctrl+C）后，挂起的远端请求清理完毕，无悬挂协程；残缺回复不进入下一轮会话。
- [x] 同批只读远端工具与内置只读工具并发执行（执行时间区间重叠）；写工具按调用顺序串行执行。

## 端到端与回归

- [x] 仿造 stdio server 与仿造 HTTP server 各暴露至少一个工具；用 FakeClient 完成「用户请求 → 远端工具调用 → 结果回传 → 模型引用结果的最终回复」完整循环。
- [x] 执行 `python -m unittest discover -s tests` 返回退出码 0（208 项通过）；原有六个内置工具、纯文本对话、快捷键、plan-only 与安全测试全部通过。
- [ ] 手动冒烟（可选，需真实 server）：用兼容 MCP server 启动 `python -m zxcode`，模型至少成功调用一次远端工具并基于结果回复。

验证说明：除最后一项外均由 `tests/test_mcp_*.py` 与 `tests/test_mcp_e2e.py` 自动化验证；`zxcode-servers.toml` 未提交到仓库，格式示例见 README「外部 MCP 服务器」一节。
