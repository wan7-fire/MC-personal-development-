# ZXCode Skill 系统任务拆解

实现顺序按编号执行；每个任务都应在一次专注会话内可完成。最终两个任务是「接入主流程」和「端到端验证」。

## 任务 1：Skill 数据模型与 frontmatter 解析

**影响文件**

- `zxcode/skills/__init__.py`：新建包。
- `zxcode/skills/model.py`：Skill 数据模型。
- `zxcode/skills/frontmatter.py`：frontmatter 解析。
- `tests/test_skills_core.py`：frontmatter 测试。

**依赖**：无。

**参考**

- `zxcode/instructions.py::load_instructions`：读取文件与错误收集的既有风格。
- `zxcode/tools/base.py::Tool.definition`：工具 schema 的形状参考。
- `tests/test_instructions.py`：解析类测试的写法。

**内容**

定义 `SkillMeta`：`name`、`description`、`mode`、`model`、`history`、`history_size`、`tools`、`source`、`level`。实现 `parse_skill_file(path)`：读取 UTF-8，识别 `---` 分隔的 YAML frontmatter，用标准库解析扁平 YAML 子集（标量、`tools` 列表、JSON 形态的 `input_schema`），不新增 `PyYAML` 依赖。

解析规则：

- `name` 必填且非空；`description` 必填且非空；`mode` 只能是 `shared` 或 `isolated`。
- `history` 只允许 `all`、`recent`、`none`，默认 `recent`；`history_size` 为正整数，默认 10。
- `model` 可选；`tools` 可选，缺省为 `None`（表示全部工具可见）。
- 任何解析或校验失败抛 `SkillParseError`，异常信息包含文件路径和原因。

## 任务 2：三级扫描、优先级覆盖与启动索引

**影响文件**

- `zxcode/skills/loader.py`：扫描器与 Skill 索引。
- `tests/test_skills_core.py`：加载器测试。

**依赖**：任务 1。

**参考**

- `zxcode/instructions.py::load_instructions`：多根目录扫描与跳过错误的参考。
- `zxcode/app.py::ZXCodeApp.__init__`：应用初始化时加载指令的接入点。

**内容**

实现 `scan_skills(project_root, user_dir, builtin_root, registry)`：

- 扫描项目级 `.zxcode/skills`、用户级 `skills`、内置资源三层。
- 同时接受 `skills/<name>.md` 和 `skills/<name>/skill.md` 两种布局。
- 同名按项目 > 用户 > 内置覆盖，低优先级同名文件不进入索引。
- `SkillParseError` 收集为 `SkillIssue` 并跳过该文件，不中断整体扫描。
- 对已声明 `tools` 的 Skill 做白名单校验：名字不在传入 `registry` 工具集合、也不在该 Skill 自带工具集合中时抛 `SkillValidationError`，异常包含 skill 名和文件路径。MCP 等启动阶段尚未注册的工具暂不可白名单。
- 返回 `SkillIndex`：`by_name` 只含 `SkillMeta`，`issues` 含所有跳过原因。

## 任务 3：内置 commit / review / test 样板

**影响文件**

- `zxcode/skills/builtin/__init__.py`：内置资源包。
- `zxcode/skills/builtin/commit/skill.md`：共享模式样板。
- `zxcode/skills/builtin/review/skill.md`：隔离模式样板。
- `zxcode/skills/builtin/test/skill.md`：共享模式样板。
- `pyproject.toml`：把 `.md` 资源纳入 package-data。
- `tests/test_skills_app.py`：内置 Skill 注册测试。

**依赖**：任务 1。

**参考**

- `pyproject.toml::[tool.setuptools.packages.find]`：包发现配置。
- `zxcode/commands/builtins.py::_review`：现有 `/review` 占位，后续由 Skill 接管。

**内容**

三个内置 Skill 使用真实 frontmatter 与正文：

- `commit`：`mode: shared`，白名单含 git 操作所需工具；正文描述检查工作区、暂存、提交、验证的 SOP。
- `review`：`mode: isolated`，`history: recent`、`history_size: 10`；正文描述获取 diff、按固定审查要点检查、输出结构化摘要。
- `test`：`mode: shared`；正文描述发现测试命令、运行、汇总通过/失败。

## 任务 4：目录型 Skill 工具解析与执行

**影响文件**

- `zxcode/skills/tool.py`：目录型工具加载与执行。
- `tests/test_skills_core.py`：目录工具测试。

**依赖**：任务 1。

**参考**

- `zxcode/tools/base.py::Tool`：工具基类与 `definition`。
- `zxcode/tools/executor.py::ToolExecutor.execute`：schema 校验、超时、输出截断。
- `zxcode/dispatch.py::ToolDispatcher._pre_hook`：安全确认的既有调用点。

**内容**

支持目录布局 `skills/<name>/tools/<tool>.md` + `<tool>.py`：

- `<tool>.md` frontmatter 包含 `name`、`description`、`input_schema`、`read_only`、`timeout_seconds`，正文为工具说明。
- `<tool>.py` 约定：从 stdin 读一个 JSON 对象，向 stdout 写 `{"success": bool, "output": str, "error": {...}}`。
- 加载器把每个工具包装成 `Tool` 子类，复用 `ToolExecutor` 的 schema 校验和输出截断。
- 执行用当前解释器以子进程方式运行，工作目录为项目根目录，超时默认 30 秒。
- 工具执行前统一走 `SecurityPolicy.evaluate_script/guard_script`：默认模式需要确认（无论 `read_only` 取值），用户拒绝返回 `permission_denied`，不启动脚本；allow 模式直接放行，strict 模式无 allow 规则即拒绝。
- `load_skill_tools` 只加载解析后仍位于 skill 目录内的脚本；`tools/` 目录或脚本经符号链接指向目录外时跳过。
- 项目/用户级目录型 Skill 的脚本激活前需用户授权（`SkillManager.confirm_activate`），未授权或拒绝时 `activate` 抛 `SkillActivationError`；内置级默认可信。

## 任务 5：SkillManager 激活状态与环境消息

**影响文件**

- `zxcode/skills/manager.py`：Skill 管理器。
- `tests/test_skills_core.py`：管理器测试。

**依赖**：任务 2、3、4。

**参考**

- `zxcode/session.py::ChatSession`：消息列表与注入语义。
- `zxcode/tools/base.py::ToolRegistry`：工具集合。

**内容**

实现 `SkillManager`：

- 持有 `SkillIndex`、激活顺序字典 `active`、工具白名单并集。
- `activate(name)` 重新读取源文件；校验白名单（registry 现有工具 + 该 Skill 自带工具）；成功后在激活顺序末尾加入。
- `active_skill_messages()` 返回系统消息列表，每条以 `[Skill 指令：<name>]` 开头，后接完整正文。
- `active_tool_names()` 返回 `None`（任一激活 Skill 未声明白名单）或「各激活 Skill 白名单并集 + 系统级加载工具」。
- 重复激活同名 Skill 是幂等操作，不产生重复消息。
- `clear()` 清空激活状态。
- `rescan()` 重扫索引；已激活但已不存在或校验失败的 Skill 从激活列表移除并返回提示。

## 任务 6：环境上下文注入与每轮重建

**影响文件**

- `zxcode/agent.py`：Agent 循环。
- `tests/test_agent.py`、`tests/test_skills_integration.py`：补充测试。

**依赖**：任务 5。

**参考**

- `zxcode/agent.py::AgentLoop._run`：每轮调用模型前重建上下文的位置。
- `zxcode/agent.py::AgentLoop._stream_model`：发送模型请求的位置。
- `zxcode/app.py::ZXCodeApp.generate`：最终历史经 `session.rebuild_from_history` 回收。

**内容**

不改 `ChatSession` 的持久化历史：在 `AgentLoop` 增加 Skill 上下文提供者，每轮 `_call_model` 前把激活 Skill 的系统消息补到稳定全局规则之后、运行时环境信息之前，保证中途通过加载工具激活后下一轮立即可见。返回给应用的最终历史不包含这些系统消息，使它们永远不进入普通消息历史。

## 任务 7：工具白名单过滤与系统级加载工具

**影响文件**

- `zxcode/tools/base.py`：`ToolRegistry.definitions` 支持过滤。
- `zxcode/skills/load_skill.py`：系统级加载工具。
- `zxcode/agent.py`：按白名单传工具定义。
- `zxcode/app.py`：注册加载工具。
- `tests/test_skills_integration.py`：白名单与 Agent 集成测试。

**依赖**：任务 5、6。

**参考**

- `zxcode/tools/base.py::ToolRegistry.definitions`：当前无条件返回全部定义。
- `zxcode/agent.py::AgentLoop._stream_model`：当前调用 `self.registry.definitions()`。
- `zxcode/app.py::ZXCodeApp.__init__`：注册内置工具的位置。

**内容**

让 `ToolRegistry.definitions(names=None)` 支持按名字过滤，并始终保留系统级工具。新增 `LoadSkill` 工具：参数为 Skill 名；调用 `SkillManager.activate`；shared 模式返回激活成功信息，isolated 模式返回隔离执行摘要；重复激活返回已激活。该工具标记为系统级，不参与白名单过滤，也不受 plan-only 写拦截影响。

## 任务 8：共享模式执行

**影响文件**

- `zxcode/skills/manager.py`：shared 激活结果。
- `zxcode/agent.py`：共享模式继续当前循环。
- `zxcode/commands/skills.py`：短命令触发（与任务 10 共用文件）。
- `tests/test_skills_app.py`：共享短命令测试。

**依赖**：任务 6、7。

**参考**

- `zxcode/app.py::ZXCodeApp.generate`：当前 Agent 循环入口。
- `zxcode/commands/model.py::AIPrompt`：AI 流程命令的返回载荷。
- `zxcode/commands/dispatcher.py::dispatch_command`：AI 流程命令分发。

**内容**

共享模式激活后不新建对话：`AgentLoop` 每轮从管理器读取激活消息和工具白名单，模型在当前历史中继续调用工具，结果正常进入主历史。短命令 `/name` 对共享 Skill 在主 Agent 流程发送请求，发送前完成激活，使本次请求一开始就带完整指令。

## 任务 9：隔离模式执行与摘要回流

**影响文件**

- `zxcode/skills/runner.py`：隔离子会话运行器。
- `zxcode/skills/load_skill.py`：isolated 模式返回摘要。
- `zxcode/app.py`：短命令隔离执行入口。
- `zxcode/commands/ui.py`：UI 控制接口。
- `tests/test_skills_integration.py`：隔离执行测试。

**依赖**：任务 7、8。

**参考**

- `zxcode/agent.py::AgentLoop.run`：可复用的循环入口。
- `zxcode/client.py::ChatClient.stream_events`：模型调用。
- `zxcode/session.py::ChatSession.messages`：主历史来源。
- `zxcode/compress.py::CompressionManager`：超长工具结果落盘可复用。

**内容**

实现 `SkillManager.run_isolated(name, user_text=None)`：

- 按 `history` 策略选择主历史：`all` 全量、`recent` 最后 `history_size` 条、`none` 不带。
- 构造子会话：Skill 完整指令 + 选中历史 + 当前用户请求；使用 Skill 声明的模型，未声明则用当前模型。
- 复用同一 `AgentLoop` 和过滤后的工具定义执行；事件不进入主 UI 历史，可只显示「隔离执行中」提示。
- 运行结束后生成固定字段摘要：`结论`、`变更`、`未决问题`、`状态`。
- 隔离执行不写入普通会话存档；执行结束后自动从激活列表移除该 Skill，SOP 不残留主会话。`LoadSkill` 把摘要作为工具结果返回，短命令则把摘要作为主对话结果展示。

## 任务 10：管理命令、短命令注册与热更新

**影响文件**

- `zxcode/commands/skills.py`：`/skills` 命令。
- `zxcode/commands/builtins.py`：移除 `/review` 占位。
- `zxcode/commands/ui.py`：隔离执行与状态提示接口。
- `zxcode/app.py`：动态注册 Skill 短命令。
- `tests/test_skills_integration.py`、`tests/test_skills_app.py`：命令测试。

**依赖**：任务 8、9。

**参考**

- `zxcode/commands/registry.py::CommandRegistry.register`：冲突检测。
- `zxcode/commands/model.py::CommandType`：LOCAL / AI_FLOW。
- `zxcode/commands/builtins.py::register_builtins`：既有注册流程。

**内容**

新增 `/skills` 管理命令：

- `/skills`：列出已加载 Skill，每行含名字、说明、来源层级、模式。
- `/skills <name>`：显示 frontmatter、源文件路径、白名单和目录型工具。
- `/skills rescan`：重新扫描并展示新增、删除和跳过项。

每个已扫描 Skill 自动注册 `/name` 短命令；shared 走 AI 流程，isolated 走隔离执行。命令执行前重新读源文件，实现热更新。移除 `builtins.py` 中 `/review` 占位，避免与内置 review Skill 冲突。

## 任务 11：接入主流程

**影响文件**

- `zxcode/app.py`：初始化 SkillManager、注册加载工具和短命令、清空时重置激活状态。
- `zxcode/commands/ui.py`：`clear_chat` 同步清激活 Skill。
- `tests/test_app.py`：补充测试。

**依赖**：任务 6、7、10。

**参考**

- `zxcode/app.py::ZXCodeApp.__init__`：创建 registry、session、command registry 的位置。
- `zxcode/commands/ui.py::TextualUI.clear_chat`：`/clear` 清空会话的入口。

**内容**

在应用初始化时创建 `SkillManager`，完成启动扫描，注册系统级加载工具，注册 `/skills` 与全部短命令，并把「可用 Skill 索引」注入会话请求。`/clear` 时同时调用 `SkillManager.clear()`。启动扫描的跳过和校验失败提示在界面可见；静态白名单缺失时启动失败并显示文件路径。

## 任务 12：端到端验证

**影响文件**

- `tests/test_skills_core.py`、`tests/test_skills_integration.py`、`tests/test_skills_app.py`、`tests/test_commands_e2e.py`：新增与更新的端到端测试。
- `docs/skills/checklist.md`：验收依据。

**依赖**：任务 11。

**参考**

- `tests/test_commands_e2e.py`：命令级端到端测试写法。
- `tests/test_app.py`：应用级集成测试写法。

**内容**

用临时项目构造一个 shared Skill 和一个 isolated Skill，覆盖：启动索引、激活、环境上下文位置、白名单过滤、隔离摘要、热更新、`/clear` 清激活、管理命令展示。最后手工跑一遍 `python -m unittest discover -s tests -v` 与 `docs/skills/checklist.md` 中的端到端验收项。
