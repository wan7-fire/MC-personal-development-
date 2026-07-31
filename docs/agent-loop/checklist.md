# ZXCode Agent 循环验收清单

> 每一项都必须可勾选、可观测。测试命令统一为：
> `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`

---

## 一、事件类型与通道

- [ ] `grep -o "\"[a-z_]*\"" zxcode/events.py` 能找到全部 11 个事件类型字面量：`user_message`、`thinking`、`text`、`tool_call_start`、`tool_call_end`、`tool_result`、`turn_end`、`final_reply`、`error`、`cancelled`、`loop_end`。
- [ ] `Event` 是冻结数据类：`from dataclasses import fields; Event(type="text").timestamp` 返回一个大于 `1.7e12` 的整数；对实例赋值 `e.turn = 1` 抛 `FrozenInstanceError`。
- [ ] `Event(type="text", turn=2, data={"content":"你好"}).to_dict()` 经 `json.dumps(..., ensure_ascii=False)` 后包含 `"content": "你好"`（非 `你`）。
- [ ] `EventChannel()` 默认 `maxsize` 为 `1000`：`EventChannel()._queue.maxsize == 1000`。
- [ ] 通道顺序保真：连续 `emit` 3 个事件后 `close()`，`async for` 迭代恰好得到 3 个事件且顺序与发射一致。
- [ ] 通道关闭后重复迭代立即结束，不阻塞（`asyncio.wait_for(..., timeout=1)` 不超时）。
- [ ] 通道关闭后再 `emit` 不抛异常，且不会让已结束的迭代重新产出事件。

## 二、取消令牌

- [ ] `CancelToken()` 新建时 `is_cancelled()` 返回 `False`；`cancel()` 后返回 `True`；`reset()` 后返回 `False`。
- [ ] `cancel()` 后 `await asyncio.wait_for(token.wait(), timeout=1)` 正常返回，不超时。
- [ ] `reset()` 后 `await asyncio.wait_for(token.wait(), timeout=0.2)` 抛 `TimeoutError`。
- [ ] 10 个线程并发调用 `cancel()` 与 `is_cancelled()` 各 100 次，无异常抛出。

## 三、状态机

- [ ] `LoopState` 恰好 6 个成员：`IDLE`、`RUNNING`、`TOOL_EXECUTING`、`PLAN_ONLY`、`CANCELLED`、`TERMINATED`。
- [ ] 下列 9 条转换全部成功：`IDLE→start→RUNNING`、`RUNNING→tool_call→TOOL_EXECUTING`、`TOOL_EXECUTING→tool_done→RUNNING`、`RUNNING→terminate→TERMINATED`、`RUNNING→cancel→CANCELLED`、`TOOL_EXECUTING→cancel→CANCELLED`、`CANCELLED→cleanup_done→TERMINATED`、`RUNNING→enter_plan_only→PLAN_ONLY`、`PLAN_ONLY→exit_plan_only→RUNNING`。
- [ ] `IDLE` 状态下 `transition("tool_call")` 抛 `IllegalStateTransition`，异常消息同时包含字符串 `IDLE` 和 `tool_call`。
- [ ] `TERMINATED` 状态下 `transition("start")` 抛 `IllegalStateTransition`。
- [ ] `IllegalStateTransition` 是 `RuntimeError` 的子类。

## 四、循环配置

- [ ] `AgentConfig()` 默认值：`max_turns == 20`、`plan_only is False`、`llm_timeout_seconds == 120.0`。
- [ ] `LoopTerminatorConfig()` 默认值保持不变：`repeated_observation_limit == 3`、`repeated_error_limit == 2`、`no_progress_limit == 4`。
- [ ] `AgentConfig().with_plan_only(True)` 返回新实例且原实例 `plan_only` 仍为 `False`。
- [ ] `grep -o "repeated_observation\|repeated_error\|no_progress" zxcode/config.py` 返回 ≥3 条（额外终止原因已纳入枚举）。

## 五、工具批次调度与拦截位

- [ ] 读类并发：构造 3 个 `read_only=True` 的伪工具各 `await asyncio.sleep(0.3)`，一次 `dispatch` 总耗时 < `0.6s`（串行需 ≥0.9s）。
- [ ] 写类串行：构造 3 个 `read_only=False` 的伪工具，各自记录进入时间，断言三段执行区间互不重叠。
- [ ] 读先写后：输入 `[write_A, read_B, write_C]`，断言 `read_B` 的开始时间早于 `write_A` 的开始时间。
- [ ] 结果保序：输入 `[write_A, read_B, write_C]`，返回结果列表顺序仍为 A、B、C，且 `metadata["call_id"]` 与输入一一对应。
- [ ] 事件成对：一次含 2 个调用的 `dispatch` 恰好产出 2 个 `tool_call_start` 和 2 个 `tool_call_end`，且每个 `tool_call_id` 的 start 早于 end。
- [ ] `tool_call_start.data` 同时含 `tool_call_id`、`tool_name`、`arguments`、`tool_type` 四个键，`tool_type` 取值只能是 `"read"` 或 `"write"`。
- [ ] `tool_call_end.data["duration_ms"]` 为非负整数；对一个 `sleep(0.2)` 的伪工具该值 ≥ `180`。
- [ ] `status` 三态：成功工具得 `"success"`；抛异常的工具得 `"error"`；`timeout_seconds=0.05` 且 `sleep(1)` 的工具得 `"timeout"`。
- [ ] 单个调用失败不影响同批：一批 3 个调用其中 1 个抛异常，仍返回 3 个结果。
- [ ] `grep -n "_pre_hook\|_post_hook" zxcode/dispatch.py` 返回 ≥4 条（定义 + 调用点各 2）。
- [ ] `grep -n "权限\|permission" zxcode/dispatch.py` 返回 ≥1 条（权限检查空位有明确注释标记）。

## 六、plan-only 模式

- [ ] `plan_only=True` 时，一个会真实写文件的伪写类工具的 `execute` 调用次数为 `0`（用计数器断言），且目标路径 `Path.exists()` 为 `False`。
- [ ] `plan_only=True` 时，读类工具的 `execute` 调用次数正常 > `0`。
- [ ] 拦截结果 `success is False` 且 `error["code"] == "plan_only_blocked"`。
- [ ] 拦截结果的 `error["message"]` 精确等于：`当前为 plan-only 模式，写类工具已被拦截。请使用 /plan 关闭该模式后再执行写操作。`
- [ ] `AgentComplete.blocked_calls` 中每一项含 `tool_name`、`arguments`、`reason` 三个键。
- [ ] 跨 2 轮各拦截 1 次写调用后，`AgentComplete.blocked_calls` 长度为 `2`。
- [ ] `plan_only=False` 时 `AgentComplete.blocked_calls == []`。
- [ ] `plan_only=True` 时，送给模型的 system 消息内容比 `plan_only=False` 时更长（追加的计划指令生效）。
- [ ] `final_reply` 事件的 `data` 同时含 `content` 与 `blocked_calls` 两个键。

## 七、循环终止路径

- [ ] `end_turn`：伪模型返回不含 `tool_calls` 的文本，`loop_end.data["termination_reason"] == "end_turn"`，`turn_end.data["reason"] == "end_turn"`。
- [ ] `max_turns`：伪模型每轮都返回工具调用，`max_turns=3` 时恰好调用模型 3 次，`loop_end.data["termination_reason"] == "max_turns"`。
- [ ] `cancelled`：工具执行期间 `cancel()`，`loop_end.data["termination_reason"] == "cancelled"`，且事件流中出现 `cancelled` 事件（`data["reason"] == "user_cancelled"`）。
- [ ] `error`：伪模型抛 `AuthenticationError`，事件流出现 `error` 事件且 `data["recoverable"] is False`，`loop_end.data["termination_reason"] == "error"`。
- [ ] `repeated_error`：连续 2 轮返回完全相同的工具错误，`termination_reason == "repeated_error"`。
- [ ] `repeated_observation`：连续 3 轮返回完全相同的工具名+参数+输出，`termination_reason == "repeated_observation"`。
- [ ] `no_progress`：连续 4 轮无新增成功观察，`termination_reason == "no_progress"`。
- [ ] 每次终止都恰好产出 1 个 `loop_end` 事件，且其后通道立即结束迭代。
- [ ] `loop_end.data["total_turns"]` 等于实际调用模型的次数。

## 八、事件时序完整性

- [ ] 一次"工具调用 → 工具结果 → 最终回复"的完整运行，事件类型序列按顺序包含：`user_message`、`text`、`tool_call_start`、`tool_call_end`、`tool_result`、`turn_end`、`text`、`final_reply`、`turn_end`、`loop_end`。
- [ ] 事件流的第一个事件恒为 `user_message`，且 `data["content"]` 等于传入 `messages` 中最后一条 user 消息的内容，全流程只出现 1 次。
- [ ] 伪客户端产出 `ReasoningDelta` 时，事件流出现对应数量的 `thinking` 事件；不产出时 `thinking` 事件数为 `0` 且运行不报错。
- [ ] 模型流式输出 5 个 delta 时，产出恰好 5 个 `text` 事件（增量粒度），各 `data["content"]` 拼接后等于完整文本。
- [ ] 所有事件的 `turn` 字段单调不减，且第一轮事件的 `turn == 0`。
- [ ] 事件流中 `tool_result` 的数量等于该轮 `tool_calls` 的数量（含被拦截和失败的）。
- [ ] `grep -rn "textual\|from .app" zxcode/agent.py zxcode/events.py zxcode/dispatch.py zxcode/state.py` 返回 `0` 条（循环层不依赖界面）。

## 九、取消与消息配对

- [ ] 取消后 `AgentComplete.messages` 中，每个 assistant 消息里的 `tool_calls[*].id` 都能在后续 tool 消息的 `tool_call_id` 中找到，无悬空。
- [ ] 被补齐的取消结果 `error["code"] == "cancelled"`。
- [ ] 取消后把 `AgentComplete.messages` 交给 `ChatSession.commit_messages`，再发起下一轮请求，`request_messages` 产出的消息列表能被伪造客户端正常接受（无 400 类结构错误断言）。
- [ ] 工具执行中取消时，正在跑的工具 `execute` 仍跑完（用完成标志位断言为 `True`），未被强制打断。
- [ ] 模型流中途取消时，剩余 delta 不再产出 `text` 事件。
- [ ] 取消后 `state_machine.state is LoopState.TERMINATED`。
- [ ] 连续取消两次不抛异常，`cancelled` 事件仍只出现 1 个。

## 十、主流程接入

- [ ] `grep -n "/plan" zxcode/app.py` 返回 ≥2 条（命令分发 + 帮助文案）。
- [ ] `/help` 输出精确等于：`/help  /clear  /exit  /model <名称>  /plan`。
- [ ] `/plan` 执行一次后 `app.config.plan_only is True`，再执行一次后为 `False`。
- [ ] `plan_only` 为真时，状态栏文本包含子串 `plan-only`；为假时不包含。
- [ ] `grep -n "worker.cancel()" zxcode/app.py` 返回 `0` 条；`grep -n "cancel_token.cancel()" zxcode/app.py` 返回 ≥1 条。
- [ ] `grep -n "TextDelta" zxcode/app.py` 返回 `0` 条（界面已改为消费 `Event`）。
- [ ] `README.md` 的「操作」小节包含 `/plan` 一行说明。

## 十一、端到端验收

- [ ] 无头 Textual：伪模型先请求一次 `ReadFile` 再返回文本，界面依次出现工具状态行与最终回答；`session.messages` 长度为 `4`（user、assistant、tool、assistant）。
- [ ] 无头 Textual：生成中触发 `action_interrupt`，状态栏文本包含 `已取消`；`session.messages` 中无悬空 `tool_call_id`；随后再发一条消息能正常完成并使 `session.turns` 增加。
- [ ] 无头 Textual：`/plan` 打开后伪模型请求 `WriteFile`，断言目标文件 `Path.exists()` 为 `False`，界面出现拦截提示，最终回复区展示了非空的拦截清单。
- [ ] 真实兼容 API 手动跑通一次：终端里完成一次 `ReadFile` 调用 + 自然语言回答，界面可见工具开始/结束状态行与耗时。
- [ ] 真实兼容 API 手动跑通一次：长任务中途按 `Ctrl+C`，界面显示已取消且可继续下一轮对话。
- [ ] `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` 全部通过，`FAILED` 与 `ERROR` 计数均为 `0`。
- [ ] 回归：`/clear`、`/model <名称>`、`Ctrl+S` 发送、授权弹窗批准/拒绝行为与本章开始前一致。
