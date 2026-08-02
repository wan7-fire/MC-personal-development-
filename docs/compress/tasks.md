# ZXCode 上下文压缩任务分解

> 共 13 个任务，按依赖顺序排列。参考规格：`docs/compress/spec.md`；验收清单：`docs/compress/checklist.md`。

---

## T1 - 大小估算与压缩配置

**影响文件**：`zxcode/compress.py`（新建）、`tests/test_compress.py`（新建）

**依赖任务**：无

**内容**

- 定义 `estimate_chars()` 与 `estimate_tokens()`：token 估算 = 字符数 // 4。
- 定义 `CompressionConfig`：`context_window`（默认 131072 token）、`trigger_ratio`（0.8）、`target_ratio`（0.4）、`single_result_limit`（8192 字符）、`batch_total_limit`（32768 字符）、`summary_model`（可空，空则复用当前模型）、`breaker_limit`（2 次）、`spool_dir`（默认 `.zxcode/spool`）。
- 定义 `CompressionFailure` 类型化异常，区分「模型错误 / 违反约束 / 解析失败 / 压缩后仍超限」。

**参考资料定位**：`zxcode/session.py` `request_messages`；`zxcode/config.py` `AgentConfig`

---

## T2 - 一层落盘核心（单条与合计）

**影响文件**：`zxcode/compress.py`、`tests/test_compress.py`

**依赖任务**：T1

**内容**

- `spool_tool_message()`：content 长度 > `single_result_limit` 时，按内容 sha256 命名写入 `spool_dir/<sha256>.txt`，content 替换为预览（含「已溢出」标记、原长度、相对路径、开头 ≤200 字符）。
- `spool_batch()`：同一轮进入历史的工具消息合计 > `batch_total_limit` 时，按长度降序依次落盘，直到合计达标。
- 幂等：已落盘消息重跑结果逐字节一致；写入失败时返回原消息、不抛异常。

**参考资料定位**：`zxcode/tools/base.py` `ToolResult.to_content`

---

## T3 - 一层接入 agent 循环

**影响文件**：`zxcode/agent.py`、`tests/test_agent.py`

**依赖任务**：T2

**内容**

- 在 `history.append(tool_message)` 之前对 tool 消息执行 `spool_batch`；`tool_message` 同时被 `history` 与 `turn_messages` 引用，落盘后两侧一致。
- `AgentComplete` 增加 `final_history: list[dict] | None` 字段（默认 None），`_complete()` 填入最终 `history`。

**参考资料定位**：`zxcode/agent.py` `class AgentComplete`（30 行）、`history.append(tool_message)`（148 行）、`_tool_message`（431 行）

---

## T4 - 一层接入会话（旧数据兜底）

**影响文件**：`zxcode/session.py`、`zxcode/app.py`、`tests/test_session.py`

**依赖任务**：T2

**内容**

- `ChatSession.prepare_request()`：对 `self.messages` 做幂等一层重检（覆盖功能上线前已存在的大结果与任何漏网消息），再组装请求。
- `ZXCodeApp.generate` 改用 `prepare_request` 构建请求。
- 一层重检只在存在未落盘大消息时写盘；重复调用结果不变。

**参考资料定位**：`zxcode/session.py` `request_messages`、`commit_messages`；`zxcode/app.py` `generate`

---

## T5 - 二层摘要 prompt 与解析

**影响文件**：`zxcode/compress.py`、`tests/test_compress.py`

**依赖任务**：T1

**内容**

- `build_summary_prompt()`：固定 9 个栏目（主要请求、关键概念、文件代码、错误修复、解决过程、用户原话、待办、当前工作、下一步）；开头与结尾各一句「禁止使用任何工具」；要求先输出分析草稿，再输出带开始/结束标记的正式摘要。
- `parse_summary()`：取标记间内容为摘要；缺标记、空摘要、内容中出现工具调用 JSON 特征 → 抛 `CompressionFailure`。
- 用户消息原文逐字进入「用户原话」栏目（解析后断言原文子串存在）。

**参考资料定位**：`zxcode/prompts.py` `build_stable_prompt` 的组织方式

---

## T6 - 二层执行与替换

**影响文件**：`zxcode/compress.py`、`tests/test_compress.py`

**依赖任务**：T5

**内容**

- `summarize_block()`：调用 `client.stream_events(messages, summary_model or model, tools=())`（不传工具定义）；异常、超时、结果为工具调用特征 → 抛 `CompressionFailure`。
- `compress_history()`：以 user 消息为界从最旧开始切块，切到估算 ≤ `target_ratio * context_window`；被切块替换为一条 user 角色摘要消息，随后追加边界消息；压缩后仍超目标 → 失败。
- 失败保证：任何异常路径都不修改传入的历史（先构造新列表，成功后才替换）。

**参考资料定位**：`zxcode/client.py` `stream_events`；`zxcode/session.py` 消息结构

---

## T7 - 熔断器

**影响文件**：`zxcode/compress.py`、`tests/test_compress.py`

**依赖任务**：T6

**内容**

- `CircuitBreaker`：连续失败 ≥ `breaker_limit` 时 `allowed()` 返回 False（自动触发短路）；一次成功复位计数；手动调用不受限制。
- 熔断状态可查询并用于提示。

**参考资料定位**：`zxcode/agent.py` 循环异常处理模式

---

## T8 - 循环内请求前两层流水线

**影响文件**：`zxcode/agent.py`、`zxcode/compress.py`、`tests/test_agent.py`

**依赖任务**：T3、T6、T7

**内容**

- `_call_model` 前执行：一层（幂等）+ 二层（估算 ≥ `trigger_ratio` 且熔断未开才触发）。
- 二层在循环内对工作 `history` 就地替换（摘要 + 边界消息），压缩结果进入 `final_history`。
- 被切块内的落盘文件在替换成功后清理。

**参考资料定位**：`zxcode/agent.py` `_call_model`（256 行）、`_stream_model`（289 行）

---

## T9 - 会话持久化一致性

**影响文件**：`zxcode/app.py`、`zxcode/session.py`、`tests/test_app.py`

**依赖任务**：T8

**内容**

- `generate` 结束后用 `AgentComplete.final_history` 重建 `session.messages`：剥掉开头全部 system 角色消息（稳定前缀、环境消息、plan-only 消息），保留其余部分。
- 不再用 `commit_messages` 拼接，避免压缩发生在循环内时会话与模型历史不一致。
- 兼容 plan-only：额外 system 消息也在前缀剥离范围内。

**参考资料定位**：`zxcode/app.py` `generate` 中 `commit_messages` 调用；`zxcode/prompts.py` `build_environment_message`（返回 system 角色）

---

## T10 - 手动压缩命令

**影响文件**：`zxcode/app.py`、`tests/test_app.py`

**依赖任务**：T9

**内容**

- `handle_command` 增加 `/compact`：先跑一层重检，再跑二层（不受熔断限制）；无可压缩内容、成功、失败三种提示。
- `/help` 文案加入 `/compact`。

**参考资料定位**：`zxcode/app.py` `handle_command`、`notice`

---

## T11 - 边界消息与 README

**影响文件**：`zxcode/compress.py`、`README.md`、`tests/test_compress.py`

**依赖任务**：T6

**内容**

- 边界消息常量与精确文本（见 checklist）。
- README 操作列表加入 `/compact`；新增「上下文压缩」小节说明落盘目录与两层机制。

**参考资料定位**：`README.md` 操作与安全策略章节；`docs/compress/checklist.md`

---

## T12 - 接入主流程（集成收尾）

**影响文件**：`zxcode/agent.py`、`zxcode/session.py`、`zxcode/app.py`、`tests/`

**依赖任务**：T4、T8、T9、T10、T11

**内容**

- 全链路联调：`prepare_request` → 循环内每请求前两层 → `final_history` 回写会话。
- 确认稳定前缀与动态环境消息不被压缩误伤、顺序不变。
- 回归：既有测试全部通过，新增用例无冲突。

**参考资料定位**：`zxcode/app.py` `generate` 全流程；`zxcode/session.py` `request_messages`

---

## T13 - 端到端验证

**影响文件**：`tests/test_compress.py`、`tests/test_app.py`、`docs/compress/checklist.md`

**依赖任务**：T12

**内容**

- 伪 LLM + 大工具结果场景：超限结果落盘，模型拿到预览路径后 ReadFile 读回完整内容。
- 长会话场景：历史逼近上限自动摘要 + 边界消息；后续请求估算不超过目标。
- 手动 `/compact` 与熔断恢复场景。
- 全量 `unittest discover -s tests -v` 通过。

**参考资料定位**：`docs/compress/checklist.md` 端到端验收节
