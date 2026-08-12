# ZXCode 子工作者系统任务拆解

实现顺序按编号执行；每个任务应在一次专注会话内完成。最后两个任务是「接入主流程」与「端到端验证」。

## 任务 1：角色数据模型与加载器

**影响文件**

- `zxcode/workers/__init__.py`：新包。
- `zxcode/workers/model.py`：角色数据模型。
- `zxcode/workers/loader.py`：角色扫描与优先级覆盖。
- `tests/test_workers_loader.py`：加载测试。

**依赖**：无。

**参考资料**

- `zxcode/skills/frontmatter.py::parse_frontmatter`：复用现有 YAML frontmatter 子集解析。
- `zxcode/skills/loader.py::scan_skills`：多目录扫描、优先级覆盖与坏文件跳过的既有模式。

**内容**

定义角色模型：角色名、用途、系统提示正文、工具白名单/黑名单、模型、最大轮次、权限模式。按「项目 `.zxcode/workers/` > 用户 `~/.zxcode/workers/` > 内置 `zxcode/workers/builtin/` > 插件目录」扫描，同名高优先级覆盖；解析/校验失败的单个角色跳过并记录日志。测试覆盖：优先级覆盖、坏 frontmatter 跳过、白/黑名单解析、模型与权限模式字段。

## 任务 2：统一工具入口与全局嵌套防线

**影响文件**

- `zxcode/workers/tool.py`：统一子工作者工具。
- `zxcode/tools/base.py` 或注册处：工具注册。
- `tests/test_workers_tool.py`：工具测试。

**依赖**：任务 1。

**参考资料**

- `zxcode/tools/base.py::Tool`：工具基类与 schema。
- `zxcode/skills/load_skill.py::LoadSkill`：系统级工具的实现风格。

**内容**

实现唯一子工作者工具：参数含角色、任务、创建模式、模型覆盖、后台标记；按角色名解析定义，缺省角色走 Fork 模式。全局禁止列表把子工作者工具自身排除在可见工具之外，任何路径创建子任务都返回嵌套禁止错误。测试覆盖：工具列表只含一个子工作者入口、角色解析、缺省角色、嵌套调用被拒绝。

## 任务 3：定义式执行（空白对话 + 固定角色）

**影响文件**

- `zxcode/workers/runner.py`：子对话运行器。
- `tests/test_workers_runner.py`：执行测试。

**依赖**：任务 2。

**参考资料**

- `zxcode/skills/runner.py::run_isolated`：独立对话构造与摘要回流的既有实现。
- `zxcode/agent.py::AgentLoop.run`：可复用的循环入口。

**内容**

定义式模式：新建空白历史，注入角色系统提示与任务用户消息；使用角色声明的模型（可被调用参数覆盖）与工具过滤结果；运行到无工具调用的助手消息即完成，最后一条文本作为结果；异常与超限返回结构化失败。测试覆盖：任务注入、独立历史、结果取最后文本、失败路径、工具过滤生效。

## 任务 4：Fork 式执行与强硬指令注入

**影响文件**

- `zxcode/workers/runner.py`：Fork 路径。
- `zxcode/workers/prompts.py`：Fork 指令文本。
- `tests/test_workers_fork.py`：Fork 测试。

**依赖**：任务 3。

**参考资料**

- `zxcode/agent.py::AgentLoop._apply_plan_only_prompt`：在历史前插入系统指令的既有位置。
- `zxcode/prompts.py`：稳定提示词构造风格。

**内容**

Fork 模式：克隆父对话历史并在最前面注入强硬指令（不能再 Fork、不主动对话、不请求确认、直接使用工具、报告控制字数并按结构化字段输出）；复用父工具集；强制后台。首个请求必须与父对话前缀一致以命中 prompt cache。测试覆盖：历史继承、指令注入位置、工具集与父一致、强制后台、首请求前缀一致性。

## 任务 5：隔离与共享边界

**影响文件**

- `zxcode/workers/runtime.py`：子任务运行时状态。
- `tests/test_workers_runtime.py`：隔离测试。

**依赖**：任务 3。

**参考资料**

- `zxcode/session.py::ChatSession`：消息历史模型。
- `zxcode/security.py::SecurityPolicy.session_rules`：审批记录作用域参考。
- `zxcode/tools/files.py`：读缓存参考。
- `zxcode/rules/engine.py::RuleEngine`：共享 Hook 引擎。

**内容**

子任务运行时状态独立维护：消息历史、权限审批记录、文件读缓存、token 计数；LLM 客户端、Hook 引擎、文件系统引用共享。测试覆盖：两个子任务历史互不污染、审批记录互不影响、token 计数独立、Hook 引擎与文件系统为同一实例。

## 任务 6：工具过滤多层防线

**影响文件**

- `zxcode/workers/filters.py`：工具过滤。
- `tests/test_workers_filters.py`：过滤测试。

**依赖**：任务 2、任务 5。

**参考资料**

- `zxcode/skills/manager.py::active_tool_names`：白名单并集与系统工具的既有逻辑。
- `zxcode/tools/base.py::ToolRegistry.definitions`：按名称过滤定义。

**内容**

实现三层过滤：全局禁止列表（含子工作者工具自身）、角色白/黑名单、后台叠加白名单（默认只读与搜索类）。过滤结果用于构建子对话的模型工具定义。测试覆盖：全局禁止不可绕过、角色白名单收窄、后台叠加更严、三种模式过滤结果稳定。

## 任务 7：后台任务管理器

**影响文件**

- `zxcode/workers/manager.py`：任务管理器。
- `tests/test_workers_manager.py`：管理器测试。

**依赖**：任务 4、任务 6。

**参考资料**

- `zxcode/rules/engine.py::RuleEngine._background`：后台任务引用管理的既有模式。
- `zxcode/events.py::EventChannel`：异步通知参考。

**内容**

实现后台任务管理器：任务标识、状态机（运行中/成功/失败/已终止）、结果、token 用量、起止时间；支持列出、详情、终止；完成后生成结构化通知（任务标识、角色、状态、结果摘要、token、耗时）异步注入主对话。测试覆盖：状态迁移、结果与 token 记录、终止、完成通知内容与异步注入。

## 任务 8：前台转后台移交与三种进入路径

**影响文件**

- `zxcode/workers/manager.py`：移交逻辑。
- `zxcode/workers/tool.py`：进入路径。
- `zxcode/app.py`：超时与 ESC 切换。
- `tests/test_workers_transfer.py`：移交测试。

**依赖**：任务 7。

**参考资料**

- `zxcode/app.py::action_interrupt`：ESC/Ctrl+C 现有处理入口。
- `zxcode/app.py::generate`：前台生成入口与超时阈值。

**内容**

实现三种后台进入路径：调用时显式指定、前台运行超过时间阈值自动切换、用户手动切换；Fork 模式强制后台。前台转后台移交运行中的实例：不终止、不重启，保持同一任务标识与状态。测试覆盖：显式后台、超时自动切换、手动切换、移交后实例继续运行且标识不变。

## 任务 9：内置角色与后台任务管理命令

**影响文件**

- `zxcode/workers/builtin/`：内置角色（代码探索、计划制定、通用全能、验证）。
- `zxcode/commands/workers.py`：管理命令。
- `zxcode/commands/ui.py` 与应用注册：命令接入。
- `zxcode/config.py` 或配置加载处：验证角色开关。
- `tests/test_workers_app.py`：应用测试。

**依赖**：任务 8。

**参考资料**

- `zxcode/skills/builtin/`：内置 Skill 的组织方式。
- `zxcode/commands/rules.py`：管理命令实现风格。
- `zxcode/app.py::ZXCodeApp.__init__`：注册入口。

**内容**

内置四个角色：代码探索、计划制定、通用全能、验证；验证角色默认关闭，由配置开关启用。新增斜杠命令：列出后台任务、查看单任务详情、终止任务；命令接入帮助与补全。测试覆盖：内置角色可解析、验证角色开关生效、命令列出/详情/终止。

## 任务 10：端到端验证

**影响文件**

- `tests/test_workers_e2e.py`：端到端测试。
- `docs/workers/checklist.md`：验收依据。

**依赖**：任务 9。

**参考资料**

- `tests/test_rules_e2e.py`：规则引擎端到端写法。
- `tests/test_skills_integration.py`：独立对话驱动写法。

**内容**

用临时项目验证：定义式子任务跑到底并返回最后文本；Fork 式子任务继承历史、注入指令并强制后台；后台完成通知异步出现在主对话；嵌套创建被全局禁止；前台转后台实例不重启；Hook 在子工作者中生效。最后跑 `python -m unittest discover -s tests -v` 全量通过并勾选 checklist。
