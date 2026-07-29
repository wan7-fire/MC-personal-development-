# MewCode Agent 循环任务分解

> 共 12 个任务，按依赖顺序排列。每个任务应能在一次专注会话内完成。
> 参考规格：`docs/agent-loop/spec.md`；验收项：`docs/agent-loop/checklist.md`。

---

## T1 — 定义事件类型与事件数据结构

**影响文件**：`mewcode/events.py`（新建）、`tests/test_events.py`（新建）

**依赖**：无

**内容**

- 定义 `EventType` 字符串枚举，成员覆盖 spec 能力清单中的 11 类事件：`user_message`、`thinking`、`text`、`tool_call_start`、`tool_call_end`、`tool_result`、`turn_end`、`final_reply`、`error`、`cancelled`、`loop_end`。
- 定义冻结数据类 `Event`，字段为 `type`、`timestamp`（毫秒整数，默认取 `time.time() * 1000`）、`turn`（0-based，默认 0）、`data`（`dict[str, Any]`，默认空字典）。
- 提供 `Event.to_dict()` 用于日志序列化，保证 `json.dumps(..., ensure_ascii=False)` 可用。

**参考资料定位**

- 事件类型清单与各事件 `data` 字段：`agnent loop part/事件流与工具执行规范.md` 第 1.3、1.4 节。
- 现有冻结数据类风格：`mewcode/client.py:17-25`（`TextDelta`、`AssistantMessage`）。

---

## T2 — 实现异步事件通道

**影响文件**：`mewcode/events.py`、`tests/test_events.py`

**依赖**：T1

**内容**

- 实现 `EventChannel`：内部持有 `asyncio.Queue[Event | None]`，构造参数 `maxsize` 默认 `1000`。
- `async emit(event)` 入队；`close()` 入队 `None` 作为结束哨兵。
- `__aiter__` / `__anext__` 实现异步迭代，取到 `None` 时停止迭代，之后重复迭代立即结束。
- `emit` 在通道已关闭后调用时静默丢弃，不抛异常（循环收尾路径可能重入）。

**参考资料定位**

- `AsyncChannel` 队列语义与 `maxsize=1000`：`agnent loop part/事件流与工具执行规范.md` 第 5 节。

---

## T3 — 实现取消令牌

**影响文件**：`mewcode/cancel.py`（新建）、`tests/test_cancel.py`（新建）

**依赖**：无

**内容**

- 实现 `CancelToken`：`cancel()`、`is_cancelled()`、`reset()`，内部用 `threading.Lock` 保证线程安全。
- 额外提供 `asyncio.Event` 形式的 `wait()`，供需要可等待语义的位置使用。
- `reset()` 同时清除内部 asyncio 事件，使令牌可跨轮复用。

**参考资料定位**

- `CancelToken` 基础实现：`agnent loop part/Agent循环架构设计.md` 第 5.3 节。

---

## T4 — 实现循环状态机

**影响文件**：`mewcode/state.py`（新建）、`tests/test_state.py`（新建）

**依赖**：无

**内容**

- 定义 `LoopState` 枚举：`IDLE`、`RUNNING`、`TOOL_EXECUTING`、`PLAN_ONLY`、`CANCELLED`、`TERMINATED`。
- 定义 `IllegalStateTransition(RuntimeError)`。
- 实现 `LoopStateMachine`：`state` 属性、`transition(action)` 方法、`termination_reason` 属性。
- 合法转换表按 `agnent loop part/Agent循环架构设计.md` 第 3.3 节实现，非法转换抛 `IllegalStateTransition`，异常消息包含当前状态与动作名。
- `TERMINATED` 为终态，任何后续 `transition` 均非法。

**参考资料定位**

- 状态定义与合法转换表：`agnent loop part/Agent循环架构设计.md` 第 3.1–3.3 节。

---

## T5 — 定义循环配置与终止原因

**影响文件**：`mewcode/config.py`（新建）、`tests/test_state.py`

**依赖**：T3

**内容**

- 定义 `AgentConfig` 数据类：`max_turns=20`、`plan_only=False`、`llm_timeout_seconds=120.0`、`cancel_token: CancelToken`、`terminator_config: LoopTerminatorConfig`。
- 定义 `TerminationReason` 常量集合：`end_turn`、`no_tool_calls`、`max_turns`、`cancelled`、`error`，外加复用现有守卫的 `repeated_observation`、`repeated_error`、`no_progress`。
- `AgentConfig` 提供 `with_plan_only(bool)` 返回新实例，供界面切换开关时使用。

**参考资料定位**

- 现有守卫配置字段与默认值：`mewcode/terminator.py:11-18`（`LoopTerminatorConfig`）。
- 守卫产出的原因字符串：`mewcode/terminator.py:48,57,72,84`。

---

## T6 — 实现带事件与拦截位的工具批次调度层

**影响文件**：`mewcode/dispatch.py`（新建）、`tests/test_dispatch.py`（新建）

**依赖**：T1、T2、T5

**内容**

- 实现 `ToolDispatcher`，构造参数为 `registry`、`executor`、`channel`。
- `async dispatch(calls, context, config, turn)`：按 `Tool.read_only` 分组，读类用 `asyncio.gather` 并发，写类在读类全部结束后按原顺序串行；返回结果顺序与输入 `calls` 顺序一致。
- 每个调用：先过 `_pre_hook`（plan-only 检查 + 权限检查空位），发 `tool_call_start`（含 `tool_call_id`、`tool_name`、`arguments`、`tool_type`），计时执行，发 `tool_call_end`（含 `duration_ms`、`status` 取 `success`/`error`/`timeout`），再过 `_post_hook`（审计空位）。
- plan-only 拦截：`config.plan_only` 且工具 `read_only` 为 `False` 时不调用执行器，直接返回 `ToolResult(success=False, error={"code": "plan_only_blocked", ...})`，并把 `{tool_name, arguments, reason}` 追加到本次 dispatch 的拦截清单，随返回值一并交出。
- `status` 判定：`result.success` 为真取 `success`；`result.error["code"] == "timeout"` 取 `timeout`；其余取 `error`。

**参考资料定位**

- 现有读写分组与顺序保序逻辑：`mewcode/tools/executor.py:18-47`（`execute_batch`）。
- 现有超时与异常归一化、`code` 取值：`mewcode/tools/executor.py:69-87`。
- `read_only` 属性定义：`mewcode/tools/base.py:52`。
- 拦截位布局与伪代码：`agnent loop part/事件流与工具执行规范.md` 第 2.4、2.5 节。

---

## T7 — 重构 AgentLoop 为事件驱动的 ReAct 循环

**影响文件**：`mewcode/agent.py`、`mewcode/client.py`、`tests/test_agent.py`

**依赖**：T1–T6

**内容**

- `AgentLoop.__init__` 改为接收 `client`、`registry`、`executor`、`config: AgentConfig`、`context: ToolContext`，内部构造 `LoopStateMachine` 与 `ToolDispatcher`。
- 新增 `async run(messages, model, channel) -> AgentComplete`：不再是异步生成器，全部过程通过 `channel` 发事件，返回值只承载最终结果。
- `run` 开始时先发一个 `user_message` 事件，内容取 `messages` 中最后一条 `role == "user"` 的消息。
- 一轮流程：检查取消 → 状态转 `RUNNING` → 调模型并把 `TextDelta` 逐条发为 `text` 事件 → 追加 assistant 消息 → 无 `tool_calls` 则发 `final_reply` + `turn_end(end_turn)` 后结束 → 有调用则转 `TOOL_EXECUTING`、交给 `ToolDispatcher`、逐条发 `tool_result`、回填 tool 消息 → 转回 `RUNNING` → 询问终止守卫 → 发 `turn_end`。
- `thinking` 事件：`ChatClient.stream_events` 目前只产出 `TextDelta` 与 `AssistantMessage`，本任务在 `mewcode/client.py` 增加一个 `ReasoningDelta` 冻结数据类，并在流事件类型为推理增量时产出；`AgentLoop` 收到后发 `thinking` 事件。服务商不返回推理内容时该事件自然缺席，不视为失败。
- 循环结束统一发 `loop_end`（含 `total_turns`、`termination_reason`）并 `channel.close()`。
- 保留 `_prepare` 的参数解析与 `invalid_arguments` 错误语义不变。
- 保留现有守卫调用：成功结果走 observation 分支、失败结果走 error 分支、轮末走 progress 分支。

**参考资料定位**

- 现有循环骨架、守卫调用顺序、progress 集合构造：`mewcode/agent.py:37-116`。
- 现有 `_prepare` 与 `invalid_arguments` 错误结构：`mewcode/agent.py:136-153`。
- 模型流事件来源与 `AssistantMessage` 产出：`mewcode/client.py:69-93`（`stream_events`）。

---

## T8 — 实现取消收尾与消息配对保证

**影响文件**：`mewcode/agent.py`、`tests/test_agent.py`

**依赖**：T7

**内容**

- 在每轮开始前、模型流消费的每个 delta 之后、工具批次返回之后检查 `config.cancel_token`。
- 取消时走统一收尾：对本轮 assistant 消息中每一个尚无配对 tool 消息的 `tool_call.id`，补一条 `{"role": "tool", "tool_call_id": ..., "content": ToolResult(False, error={"code":"cancelled", ...}).to_content()}`。
- 状态转 `CANCELLED` → 清理 → `TERMINATED`；发 `cancelled(reason="user_cancelled")` 与 `loop_end(termination_reason="cancelled")`。
- 返回的 `AgentComplete.messages` 只包含已配对完整的消息，可直接交给 `ChatSession.commit_messages`。
- 模型流中途取消时通过关闭流中断，不等待剩余 chunk；工具执行中取消时不打断工具，等待其完成或自身超时。

**参考资料定位**

- 现有 assistant/tool 消息成对追加逻辑：`mewcode/agent.py:56-80`。
- 会话提交入口与消息形状要求：`mewcode/session.py:35-41`（`commit_messages`）。
- 流关闭方式：`mewcode/client.py:82-86`（`async with stream`）。

---

## T9 — 实现 plan-only 收尾与拦截清单

**影响文件**：`mewcode/agent.py`、`mewcode/dispatch.py`、`tests/test_agent.py`

**依赖**：T6、T7

**内容**

- `AgentComplete` 增加 `blocked_calls: list[dict[str, Any]]` 字段，默认空列表；跨轮累积 `ToolDispatcher` 返回的拦截清单。
- plan-only 下循环正常结束时，`final_reply` 事件的 `data` 同时携带 `content`（模型自然语言计划）与 `blocked_calls`。
- 拦截返回给模型的 `error.message` 固定为：`当前为 plan-only 模式，写类工具已被拦截。请使用 /plan 关闭该模式后再执行写操作。`
- 最小系统提示词在 `plan_only` 为真时追加一句，要求模型在无法执行写操作时给出分步计划而非反复重试。

**参考资料定位**

- 现有 `AgentComplete` 定义：`mewcode/agent.py:15-20`。
- 现有系统提示词：`mewcode/session.py:8-11`（`SYSTEM_PROMPT`）。
- plan-only 行为规则表：`agnent loop part/事件流与工具执行规范.md` 第 3.3 节。

---

## T10 — 补齐单元测试

**影响文件**：`tests/test_events.py`、`tests/test_cancel.py`、`tests/test_state.py`、`tests/test_dispatch.py`、`tests/test_agent.py`

**依赖**：T1–T9

**内容**

- 事件通道：发射/迭代/关闭/关闭后再发射。
- 状态机：全部合法转换通过、代表性非法转换抛 `IllegalStateTransition`。
- 调度层：读类并发（用带 `asyncio.sleep` 的伪工具验证总耗时小于串行之和）、写类串行且保序、事件顺序正确、`status` 三态判定。
- 循环：五类终止路径各一个用例；伪造模型流覆盖"工具调用—工具结果—最终回复"完整一轮；取消后消息配对完整；plan-only 下写类工具零执行。
- 复用现有伪造客户端与临时目录写法，不触真实 API。

**参考资料定位**

- 现有伪造 LLM 流与断言风格：`tests/test_agent.py`。
- 现有工具运行时测试写法：`tests/test_tool_runtime.py`。

---

## T11 — 接入主流程

**影响文件**：`mewcode/app.py`、`mewcode/__main__.py`、`README.md`、`tests/test_app.py`

**依赖**：T7–T9

**内容**

- `MewCodeApp.__init__` 构造 `AgentConfig` 与 `CancelToken`，`AgentLoop` 按新签名创建。
- `generate` worker 改为：创建 `EventChannel` → 起一个任务跑 `agent.run(...)` → `async for event in channel` 按事件类型渲染（`text` 追加、`thinking` 暗色渲染、`tool_call_start`/`tool_call_end` 渲染工具状态行、`error`/`cancelled` 更新状态栏、`final_reply` 记录待提交内容）→ 等待 run 任务返回 `AgentComplete` 并 `commit_messages`。
- `action_interrupt` 改为触发 `cancel_token.cancel()` 并等待循环收尾，不再直接 `worker.cancel()`；空闲时行为不变（退出）。
- 新增 `/plan` 斜杠命令切换 `config.plan_only`，`/help` 文案同步更新为 `/help  /clear  /exit  /model <名称>  /plan`。
- 状态栏在 plan-only 打开时追加 `| plan-only` 标记。
- `README.md` 的「操作」小节补充 `/plan` 说明。

**参考资料定位**

- 现有 worker 与事件消费位置：`mewcode/app.py:193-226`（`generate`）。
- 现有取消实现：`mewcode/app.py:187-191`（`action_interrupt`）。
- 现有斜杠命令分发与帮助文案：`mewcode/app.py:162-180`（`handle_command`）。
- 现有状态栏渲染：`mewcode/app.py:137-141`（`set_status`）。
- `AgentLoop` 构造位置：`mewcode/app.py:105-113`。

---

## T12 — 端到端验证

**影响文件**：`tests/test_app.py`、`docs/agent-loop/checklist.md`

**依赖**：T11

**内容**

- 无头 Textual 测试：伪造模型先返回一次 `ReadFile` 调用、再返回纯文本，断言界面依次出现工具状态行与最终回答，且 `session.messages` 中 assistant/tool 消息成对。
- 无头 Textual 测试：生成过程中触发 `action_interrupt`，断言状态栏显示已取消、`session.messages` 中无悬空 `tool_call_id`、随后可再发一条消息正常完成。
- 无头 Textual 测试：`/plan` 打开后伪造模型请求 `WriteFile`，断言目标文件未被创建、界面出现拦截提示、最终回复带出拦截清单。
- 真实兼容 API 手动验证一次：在真实终端里完成一次读取工具调用 + 自然语言回答，并按 Ctrl+C 中断一次长任务确认收尾正常。
- 全量回归：`.\.venv\Scripts\python.exe -m unittest discover -s tests -v` 全绿。
- 逐条勾选 `docs/agent-loop/checklist.md`。

**参考资料定位**

- 现有无头 Textual 测试写法：`tests/test_app.py`。
- 测试命令：`README.md` 第 42-46 行。
