# ZXCode 上下文记忆系统任务分解

> 共 12 个任务，按依赖顺序排列。参考规格：`docs/memory/spec.md`；验收清单：`docs/memory/checklist.md`。

---

## T1 - 指令文件加载（项目级 + 用户级）

**影响文件**：`zxcode/instructions.py`（新建）、`tests/test_instructions.py`（新建）

**依赖任务**：无

**内容**

- 按项目根目录、用户目录两个层级读取指令文件（默认文件名与目录见 checklist，路径允许配置）。
- UTF-8 读取失败、文件缺失、内容超限时分别处理：缺失/不可读跳过并记录，超限截断并标记提示。
- 返回结果携带来源层级、原始路径与是否截断，供注入与恢复复用。
- 单元测试覆盖：两级文件都存在/只存在一级/都不存在/编码损坏/超限五种情况。

**参考资料定位**

- 现有提示词目录加载模式：`zxcode/prompts.py` 的 `load_project_modules`（约 52 行起）
- 项目根解析：`zxcode/app.py` `ZXCodeApp.__init__` 中 `Path.cwd()` 用法
- 测试风格：`tests/test_app.py` 的 `FakeClient`、`Settings` 构造

---

## T2 - @include 展开与安全校验

**影响文件**：`zxcode/instructions.py`、`tests/test_instructions.py`

**依赖任务**：T1

**内容**

- 解析指令文件中的引用行，相对路径以被引用文件所在目录为基准展开。
- 嵌套深度上限（默认 3）；用已展开路径集合检测循环；违规行跳过并收集警告。
- 同层限制：项目级文件只能引用项目根内文件，用户级文件只能引用用户目录内文件。
- 路径逃逸拦截：归一化后的父级跳转、绝对路径、符号链接解析后跳出所属根目录的情况全部拒绝。
- 单元测试覆盖：深度超限、循环、`..`、绝对路径、符号链接、跨层级引用六类违规。

**参考资料定位**

- 路径解析与沙箱可参考 `zxcode/security.py` 的路径策略
- `zxcode/compress.py` 中 `Path.resolve()` 与 spool 路径处理模式

---

## T3 - 优先级拼接与注入主流程

**影响文件**：`zxcode/prompts.py`、`zxcode/session.py`、`zxcode/app.py`、`tests/test_session.py`、`tests/test_app.py`

**依赖任务**：T2

**内容**

- 项目内容在前、用户内容在后，合并为一条独立指令消息，不重复展开。
- 指令消息存放在 `ChatSession` 独立字段，请求时插在稳定系统提示词与环境消息之后、对话历史之前；新会话与 `/clear` 后都生效。
- `ChatSession` 的轮次计数不受指令消息影响；指令消息不写入会话存档，`rebuild_from_history` 无需特殊处理。
- 恢复会话时按当前指令文件重新注入，不与存档内容重复。
- 单元测试覆盖：注入位置、`/clear` 后仍在、轮次计数不变、存档与消息历史不含指令消息。

**参考资料定位**

- `zxcode/session.py`：`ChatSession.turns`（约 17 行）、`request_messages`（约 22 行）、`rebuild_from_history`（约 46 行）
- `zxcode/app.py`：`on_mount`（约 168 行）、`handle_command` 中 `/clear` 分支（约 206 行）
- `zxcode/prompts.py`：`build_stable_prompt`、`build_environment_message`

---

## T4 - 会话 JSONL 追加写入

**影响文件**：`zxcode/storage.py`（新建）、`tests/test_storage.py`（新建）

**依赖任务**：无

**内容**

- 每条消息按模型服务商兼容结构序列化为一行 JSON，追加写入并立即 flush。
- 追加失败不影响内存会话，返回失败原因；崩溃后最后一行可能不完整但其余行可解析。
- 提供按会话 ID 的追加接口，会话 ID 唯一生成，目录布局固定在用户级目录（见 checklist）。
- 单元测试覆盖：顺序写入、多次追加、写入后逐行可解析、模拟中途崩溃。

**参考资料定位**

- 消息结构：`zxcode/session.py` `ChatSession.messages`
- 工具消息形态：`zxcode/agent.py` `_tool_message`（约 430 行）
- 测试桩：`tests/test_app.py` `FakeClient`（24 行起）

---

## T5 - 概要文件与会话列表

**影响文件**：`zxcode/storage.py`、`tests/test_storage.py`

**依赖任务**：T4

**内容**

- 每轮提交后原子刷新概要文件（临时文件 + 替换）：ID、标题、摘要、消息数、创建/更新时间、模型。
- 标题取首条用户消息截断（长度见 checklist）；摘要默认空，退出时可用则写入。
- 列表接口只遍历概要文件，不读完整日志；概要损坏时回退为读取日志首行并标记。
- 单元测试覆盖：概要字段、原子替换、损坏回退、列表不触碰完整日志（用探针断言）。

**参考资料定位**

- 提交点：`zxcode/app.py` `generate` 中 `rebuild_from_history` / `commit_messages`（约 245 行起）
- 消息提交：`zxcode/session.py` `commit_messages`

---

## T6 - 恢复解析与坏行处理

**影响文件**：`zxcode/storage.py`（或新建 `zxcode/recovery.py`）、`tests/test_storage.py`

**依赖任务**：T5

**内容**

- 逐行解析存档，坏行跳过并计数；解析结果包含跳过行数、有效消息数。
- 检测末尾悬空工具调用：最后一条 assistant 消息含工具调用但后续没有对应 `tool_call_id` 的工具消息时，截断到该消息之前的最后完整轮次。
- 恢复结果可直接喂给现有 `prepare_request` 与压缩流程；恢复报告返回给界面显示。
- 单元测试覆盖：正常恢复、中间坏行、末尾坏行、末尾悬空工具调用、仅剩坏行五种场景。

**参考资料定位**

- 配对校验可参考 `zxcode/agent.py` `_pair_dangling`（约 442 行）的 `tool_call_id` 配对逻辑
- 历史重建：`zxcode/session.py` `rebuild_from_history`

---

## T7 - 恢复时体积控制与空闲提醒

**影响文件**：`zxcode/recovery.py`（或 `zxcode/storage.py`）、`zxcode/compress.py`、`tests/test_storage.py`

**依赖任务**：T6

**内容**

- 恢复后估算体积，超过触发阈值时先执行一次压缩，复用现有压缩管理器。
- 压缩失败或仍超限时，从最旧完整轮次截断直到低于目标体积，保留指令消息与最近一轮，并插入边界提示。
- 距上次活跃超过阈值时插入时间跨度提醒消息；未超过则不插入。
- 压缩、截断、提醒三类动作都写进恢复报告。
- 单元测试覆盖：超限先压缩、压缩失败截断、未超限不动、超时插入提醒、未超时不插入。

**参考资料定位**

- `zxcode/compress.py`：`CompressionConfig`（约 45 行）、`estimate_messages`（约 60 行）、`compress_history`（约 240 行）、`prepare`（约 300 行）
- 边界提示文案常量：`zxcode/compress.py` `BOUNDARY_MESSAGE`（约 25 行）

---

## T8 - /resume 与 /sessions 命令

**影响文件**：`zxcode/app.py`、`tests/test_app.py`

**依赖任务**：T7

**内容**

- `/resume <id>`：按 ID 恢复会话，替换当前消息并重绘界面；未知 ID 有明确提示。
- `/sessions`：列表（仅概要数据）、删除单条、清空全部（均走确认弹窗）、打印会话目录路径。
- 界面交互测试覆盖：恢复后消息区内容、删除/清空确认流、未知 ID。

**参考资料定位**

- 命令分发：`zxcode/app.py` `handle_command`（约 198 行）、`notice`、`action_submit`
- 确认弹窗：`zxcode/app.py` `ConfirmScreen`（约 24 行）、`confirm_tool`
- 会话字段：`ZXCodeApp.__init__` 中 `self.session`（约 150 行）

---

## T9 - 自动笔记触发与 LLM 更新

**影响文件**：`zxcode/notes.py`（新建）、`tests/test_notes.py`（新建）

**依赖任务**：T4

**内容**

- 每完成固定轮数、应用退出时触发，且用户消息出现身份/偏好/纠正强信号（如「我是…」「我喜欢…」「不要…」）时当轮立即触发；异步执行，不阻塞主循环，超时丢弃。
- LLM 调用复用现有流式接口且不带工具；请求内容为当前笔记 + 最近对话，输出固定五类增量（用户身份、用户偏好、纠正反馈、项目知识、参考资料）。
- 写入采用原子替换；退出时同一调用成功则顺带生成一句话会话摘要。
- 防止并发重复触发（同一时间只有一个更新任务在跑）。
- 单元测试覆盖：轮数触发、强信号即时触发、退出触发、超时丢弃、请求无工具、原子写入、并发防抖。

**参考资料定位**

- LLM 调用模式：`zxcode/compress.py` `summarize_block`（约 210 行）与 `zxcode/client.py` `ChatClient.stream_events`
- 退出钩子：`zxcode/app.py` `on_unmount`（约 178 行）

---

## T10 - 笔记分层存储与 /notes 命令

**影响文件**：`zxcode/notes.py`、`zxcode/app.py`、`tests/test_notes.py`、`tests/test_app.py`

**依赖任务**：T9

**内容**

- 用户身份、用户偏好、纠正反馈写入用户级笔记文件；项目知识、参考资料写入项目级笔记文件（路径见 checklist）。
- `/notes` 支持查看（默认展示项目级，可查看全部）、清空（需确认）、定位编辑（打开系统编辑器或打印路径）。
- 单元测试覆盖：分类落盘位置、查看、清空确认、定位输出。

**参考资料定位**

- `zxcode/notes.py`（T9 产物）
- 命令分发：`zxcode/app.py` `handle_command`
- README 操作说明：`README.md` 操作章节

---

## T11 - 接入主流程

**影响文件**：`zxcode/app.py`、`zxcode/session.py`、`zxcode/prompts.py`、`README.md`

**依赖任务**：T3、T8、T10

**内容**

- 启动时加载并注入指令文件；每轮提交后追加存档并刷新概要；退出钩子触发笔记。
- 恢复入口接通界面命令；README 增补新命令、存储目录与恢复说明。
- 确认现有 `/help`、`/clear`、`/model`、`/plan`、`/compact`、`/exit` 行为无回归。

**参考资料定位**

- `zxcode/app.py`：`on_mount`、`generate`、`on_unmount`、`handle_command`
- `README.md`：操作、上下文压缩、测试章节
- `tests/test_main.py`：入口冒烟测试

---

## T12 - 端到端验证

**影响文件**：`tests/test_instructions.py`、`tests/test_storage.py`、`tests/test_notes.py`、`tests/test_app.py`、`docs/memory/checklist.md`

**依赖任务**：T11

**内容**

- 用伪 LLM 流跑通「新会话注入指令 → 多轮对话 → 退出存档 → 重启恢复 → 继续对话」全链路。
- 分别验证坏行跳过、悬空工具调用截断、超限压缩、空闲提醒、笔记落盘。
- 跑全量单测，并按 `docs/memory/checklist.md` 逐项勾验。

**参考资料定位**

- `tests/test_app.py`：`FakeClient`、`TrackingApp` 模式（128 行起）
- 现有测试命令：`.\.venv\Scripts\python.exe -m unittest discover -s tests -v`
