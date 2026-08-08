# ZXCode 命令系统任务分解

> 共 10 个任务，按依赖顺序排列。参考规格：`docs/commands/spec.md`；验收清单：`docs/commands/checklist.md`。

---

## T1 - 命令元数据模型与注册中心

**影响文件**：`zxcode/commands/model.py`（新建）、`zxcode/commands/registry.py`（新建）、`tests/test_commands_registry.py`（新建）

**依赖任务**：无

**内容**

- 定义命令类型枚举（纯本地 / 影响 UI 状态 / AI 对话流）与命令元数据（名称、别名、描述、用法、类型、参数提示、隐藏标记、处理函数）。
- 注册表提供注册、按名/别名查询（大小写不敏感）、可见命令枚举、前缀补全查询。
- 注册时检测名称与别名冲突（含大小写变体），冲突抛异常；隐藏命令从补全查询中排除。
- 单元测试覆盖：正常注册、别名等价、重名/重别名/大小写变体冲突、隐藏命令补全排除。

**参考资料定位**

- 现有命令清单与分支结构：`zxcode/app.py` `handle_command`（339 行起）
- 可复用的模态选择模式：`zxcode/app.py` `SessionPickerScreen`（104 行）
- 测试风格：`tests/test_app.py` 的 `FakeClient`、`Settings` 构造

---

## T2 - 命令解析器

**影响文件**：`zxcode/commands/parser.py`（新建）、`tests/test_commands_parser.py`（新建）

**依赖任务**：T1

**内容**

- 输入行解析：`/` 前缀识别，首个空格前为命令名（转小写）、之后为参数（保留大小写，去首尾空白）。
- 无 `/` 前缀返回"非命令"；仅 `/` 或空命令名归为未知命令。
- 单元测试覆盖：大小写、多空格、无参数、参数含空格、`/` 单独输入、非命令输入。

**参考资料定位**

- 现有 `command.startswith("/")` 分流：`zxcode/app.py` `action_submit`（320 行起）

---

## T3 - UI 控制接口与 Textual 适配器

**影响文件**：`zxcode/commands/ui.py`（新建）、`tests/test_commands_ui.py`（新建）

**依赖任务**：无（可与 T1 并行）

**内容**

- 定义抽象接口：显示系统消息、发送用户消息、切换模式、查询 token 估算、刷新状态栏、退出、弹出选择列表。
- Textual 适配器包装 `ZXCodeApp` 现有能力，命令实现只依赖接口。
- token 估算复用现有估算逻辑，不新造算法。
- 单元测试覆盖：接口各能力映射到 `ZXCodeApp` 对应方法；无头环境下可调用。

**参考资料定位**

- `ZXCodeApp.set_status`（`zxcode/app.py` 313 行）、`notice`（527 行）、`generate`（566 行）
- 消息请求构造：`zxcode/session.py` `request_messages`（25 行）、`prepare_request`（39 行）
- token 估算：`zxcode/compress.py` 的 `estimate_messages`

---

## T4 - 分发器与 AI 类 A+B 执行

**影响文件**：`zxcode/commands/dispatcher.py`（新建）、`tests/test_commands_dispatcher.py`（新建）

**依赖任务**：T2、T3

**内容**

- 按命令类型分发：纯本地/UI 状态直接调用处理函数；运行期异常捕获并提示，不中断应用。
- AI 类命令：处理函数返回（触发消息、预设提示词列表）；分发器把触发消息作为用户消息走生成链路，预设提示词作为当次请求的 system 侧注入（`dynamic_messages` 通道）。
- 存档只包含触发消息，预设提示词不进 JSONL（A+B 混合语义）。
- 单元测试覆盖：三类分发、异常捕获、AI 类请求组成（触发消息可见、预设词在请求内且不在存档）。

**参考资料定位**

- `dynamic_messages` 注入点：`zxcode/session.py` `request_messages`（25 行）
- 生成与存档链路：`zxcode/app.py` `generate`（566 行）、`_persist_turn`（618 行）

---

## T5 - 内置命令迁移与新增 /status

**影响文件**：`zxcode/commands/builtins.py`（新建）、`zxcode/app.py`、`tests/test_app.py`、`tests/test_memory_app.py`

**依赖任务**：T1-T4

**内容**

- 将 `/help /clear /exit /model /plan /compact /resume /sessions /notes` 迁移为注册表命令，处理函数委托现有实现，行为不变。
- 新增 `/status`（纯本地）：输出模型、轮次、当前模式、token 估算、会话 ID（或"无会话"）。
- 原测试保持通过；为 `/status` 增加输出断言。

**参考资料定位**

- 现有命令入口：`handle_command`（`zxcode/app.py` 339 行）、`handle_sessions`（386 行）、`handle_notes`（424 行）
- 恢复与压缩：`resume_session`（469 行）、`compact`（642 行）
- 会话与笔记数据：`SessionStore.list_meta`（`zxcode/storage.py` 145 行）、`NotesManager.update_notes`（`zxcode/notes.py` 180 行）

---

## T6 - 权限与代码审查占位命令

**影响文件**：`zxcode/commands/builtins.py`、`tests/test_commands_registry.py`

**依赖任务**：T5

**内容**

- `/permissions`（纯本地占位）：显示安全策略摘要（当前策略模式），规则编辑留 Out of Scope。
- `/review`（AI 类占位）：注册完整元数据与参数提示，执行提示"待接入"。
- 两者参与 `/help` 与 Tab 补全。

**参考资料定位**

- 安全策略文件与加载：`zxcode-security.toml`、`ZXCodeApp.__init__` 中 `load_policy` 用法

---

## T7 - Tab 补全

**影响文件**：`zxcode/commands/completion.py`（新建，或并入 registry）、`zxcode/app.py`、`tests/test_commands_completion.py`（新建）

**依赖任务**：T1、T3

**内容**

- 输入框 Tab 事件触发前缀补全：单匹配直接补全命令名；多匹配弹出选择列表（复用模态模式）；无匹配不动作。
- 隐藏命令不参与补全。
- 单元测试覆盖：单匹配、多匹配、无匹配、隐藏命令排除、参数已被输入时只补命令名。

**参考资料定位**

- 模态列表复用：`zxcode/app.py` `SessionPickerScreen`（104 行）
- 输入框按键与提交：`action_submit`（`zxcode/app.py` 320 行）

---

## T8 - 状态栏与未知命令引导

**影响文件**：`zxcode/app.py`、`zxcode/commands/builtins.py`

**依赖任务**：T5、T7

**内容**

- 状态栏常驻显示当前模式（执行/计划）与高频命令提示（`/help`、`/status`、`/compact` 等）。
- 未知命令统一提示并引导到 `/help`。
- `/help` 输出改为由注册表生成：可见命令、用法、参数提示。

**参考资料定位**

- 状态栏实现：`set_status`（`zxcode/app.py` 313 行）与 `CSS`（115 行起）

---

## T9 - 接入主流程

**影响文件**：`zxcode/app.py`、`zxcode/commands/__init__.py`

**依赖任务**：T4-T8

**内容**

- 回车入口改为分流器：`/` 前缀走解析 → 分发，非命令走 AI。
- 移除 `handle_command` 的硬编码分支（或降级为注册表委托），删除旧路径。
- 启动时注册全部内置命令；Tab 补全与状态栏接入。

**参考资料定位**

- 分流点：`action_submit`（`zxcode/app.py` 320 行）；旧分发：`handle_command`（339 行）

---

## T10 - 端到端验证

**影响文件**：`tests/test_commands_e2e.py`（新建，或并入 `tests/test_app.py`）、`docs/commands/checklist.md`

**依赖任务**：T9

**内容**

- 全量测试通过（原测试 + 新命令测试）。
- 端到端验证：`/HELP` 大小写、Tab 补全单/多匹配、`/status` 内容、`/plan` 模式切换与状态栏、未知命令引导、AI 类命令存档语义（触发消息在 JSONL、预设词不在）。
- 回归确认 9 个旧命令行为不变。

**参考资料定位**

- 验收条目：`docs/commands/checklist.md` 全部
- 全量测试命令：`python -m unittest discover -s tests`
