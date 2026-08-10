# ZXCode 规则引擎任务拆解

实现顺序按编号执行；每个任务应在一次专注会话内完成。最后两个任务是「接入主流程」与「端到端验证」。

## 任务 1：事件目录与规则数据模型

**影响文件**

- `zxcode/rules/__init__.py`：新包。
- `zxcode/rules/model.py`：规则数据模型。
- `tests/test_rules_model.py`：模型测试。

**依赖**：无。

**参考资料**

- `zxcode/events.py::EventType`：现有事件类型枚举，规则事件名不得与其冲突。
- `zxcode/skills/model.py`：数据类与常量定义的既有风格。

**内容**

定义规则模型：规则标识、事件名、条件组（组合方式 + 条件列表）、动作列表、执行控制（once/async/timeout）、reject 字段。定义事件目录常量，覆盖会话级、轮次级、消息级、工具级、系统级；标注哪些事件允许拦截（pre_tool_use）。模型测试覆盖：合法规则构造、默认值、reject 出现在非 pre_tool_use 时的校验入口。

## 任务 2：规则 YAML 加载与集中校验

**影响文件**

- `zxcode/rules/loader.py`：加载与校验。
- `tests/test_rules_loader.py`：加载测试。

**依赖**：任务 1。

**参考资料**

- `zxcode/skills/frontmatter.py::parse_frontmatter`：现有无依赖 YAML 子集解析器，可扩展复用或引入同等能力。
- `zxcode/skills/loader.py::scan_skills`：多目录扫描与错误收集的既有风格。

**内容**

从项目 `.zxcode/rules/` 扫描并解析规则文件；对每条规则集中校验：事件名在事件目录内、动作类型必须是 command/prompt/http/agent 之一、reject 只能用于 pre_tool_use、async 不能用于 pre_tool_use、每种动作必填字段齐全（command 必须含 command、prompt 必须含 prompt、http 必须含 url、agent 必须含 name）、规则标识唯一。任何非法规则抛出定位错误（文件路径 + Hook/规则标识），整体加载失败。测试覆盖：合法文件、非法事件、非法动作、reject 位置错误、pre_tool_use 带 async、缺失必填字段、重复标识。

## 任务 3：条件匹配器

**影响文件**

- `zxcode/rules/matcher.py`：条件求值。
- `tests/test_rules_matcher.py`：匹配测试。

**依赖**：任务 1。

**参考资料**

- `zxcode/security.py::SecurityPolicy._rule_decision`：现有权限规则的匹配语义，条件操作符与其对齐。

**内容**

实现条件求值：精确、反向、正则、glob 四种操作符；组合方式「全部满足/任一满足」；对缺失字段按不匹配处理。匹配目标为事件上下文（事件名、工具名、消息内容、文件路径、错误信息、工具参数字段）。测试覆盖四种操作符、两种组合、组合混用被拒绝、字段缺失、正则非法时按不匹配处理不抛异常。

## 任务 4：模板渲染与动作执行器

**影响文件**

- `zxcode/rules/actions.py`：动作执行器。
- `tests/test_rules_actions.py`：动作测试。

**依赖**：任务 2。

**参考资料**

- `zxcode/tools/shell.py::_run`：子进程执行与编码处理的既有实现。
- `zxcode/tools/base.py::ToolResult`：动作结果的统一结构。
- `zxcode/skills/runner.py`：消息构造的既有风格（prompt 注入参考）。

**内容**

实现动作模板渲染：上下文变量占位替换，未定义变量替换为空串。实现四个动作执行器：command（复用项目安全策略与确认流程）、prompt（注入消息并返回注入内容）、http（发起请求并记录状态）、agent（占位，返回未实现结果）。测试覆盖：变量替换、未定义变量为空串、command 动作受安全策略约束、prompt 注入内容正确、http 请求可达本地测试服务器、agent 占位结果。

## 任务 5：执行控制（once / async / timeout）

**影响文件**

- `zxcode/rules/executor.py`：动作执行调度。
- `tests/test_rules_executor.py`：执行控制测试。

**依赖**：任务 4。

**参考资料**

- `zxcode/skills/tool.py::_terminate_process`：超时后终止子进程的既有实现。
- `zxcode/commands/dispatcher.py::dispatch_command`：异步任务调度的既有模式。

**内容**

实现执行控制：once 状态记录（会话级，跨轮次生效，重启即重置）、async 后台执行、动作超时终止。pre_tool_use 强制同步，async 标记在加载期已被拒绝，执行器再做一次防御性校验。测试覆盖：once 只执行一次、重启后新会话可再次执行、async 不阻塞主流程、超时后动作终止且主流程继续、pre_tool_use 同步执行。

## 任务 6：规则引擎与错误隔离

**影响文件**

- `zxcode/rules/engine.py`：事件分发与 Hook 执行。
- `tests/test_rules_engine.py`：引擎测试。

**依赖**：任务 3、任务 5。

**参考资料**

- `zxcode/events.py::EventChannel`：事件通道的既有抽象。
- `zxcode/skills/manager.py`：状态管理与激活顺序的既有风格。

**内容**

实现引擎：按事件名查规则，按声明顺序求值；命中 reject 规则时返回拒绝决策并跳过该规则动作；否则按序执行动作。所有 Hook 异常与超时被捕获并记日志（日志含 Hook/规则标识），不向调用方抛异常。测试覆盖：规则顺序、多个命中、reject 优先、动作异常被隔离、日志记录 Hook/规则标识。

## 任务 7：嵌入 Agent Loop（会话/轮次/消息节点）

**影响文件**

- `zxcode/agent.py`：Agent 循环。
- `zxcode/session.py`：会话消息。
- `tests/test_rules_integration.py`：集成测试。

**依赖**：任务 6。

**参考资料**

- `zxcode/agent.py::AgentLoop.run`：主循环入口。
- `zxcode/agent.py::_apply_skill_context`：环境上下文注入的既有位置。
- `zxcode/session.py::ChatSession.prepare_request`：请求构造位置。

**内容**

在 Agent Loop 的关键节点接入规则引擎：会话开始/结束、轮次开始/结束、消息发送前/接收后。消息发送前事件可注入 prompt 动作产物；所有节点调用引擎均包裹错误隔离。测试覆盖：轮次起止事件触发、消息前后事件触发、prompt 注入出现在请求中、Hook 异常不中断循环。

## 任务 8：工具级钩子与拦截回灌

**影响文件**

- `zxcode/dispatch.py`：工具调度器。
- `zxcode/agent.py`：工具结果处理。
- `tests/test_rules_intercept.py`：拦截测试。

**依赖**：任务 6。

**参考资料**

- `zxcode/dispatch.py::ToolDispatcher._pre_hook`：工具执行前检查点。
- `zxcode/dispatch.py::ToolDispatcher._run_one`：单工具执行路径。
- `zxcode/agent.py::AgentLoop.run`：工具结果进入下一轮请求的位置。

**内容**

在 pre_tool_use 接入同步可拦截钩子：命中 reject 时返回错误工具结果（含拒绝原因），工具本体不执行，错误结果作为该轮工具结果进入历史并回馈 LLM；post_tool_use 在工具完成后触发。测试覆盖：reject 后工具不执行、拒绝原因出现在工具结果中、post_tool_use 收到成功/失败状态、多条 reject 规则按声明顺序取首个拒绝。

## 任务 9：接入主流程（应用加载 + 管理命令）

**影响文件**

- `zxcode/app.py`：应用初始化。
- `zxcode/commands/rules.py`：管理命令。
- `zxcode/commands/builtins.py` 或注册入口：命令注册。
- `tests/test_rules_app.py`：应用测试。

**依赖**：任务 7、任务 8。

**参考资料**

- `zxcode/app.py::ZXCodeApp.__init__`：应用初始化加载顺序。
- `zxcode/app.py::rescan_skills`：加载后刷新注册的既有模式。
- `zxcode/commands/skills.py`：管理命令的实现风格。

**内容**

应用启动时加载规则集并构建引擎；提供 `/rules` 管理命令：查看已加载规则、查看单条规则详情、重新加载；重新加载失败时保留旧规则集并提示错误。测试覆盖：启动加载、命令列表与详情、重载成功与失败回退。

## 任务 10：端到端验证

**影响文件**

- `tests/test_rules_e2e.py`：端到端测试。
- `docs/rules/checklist.md`：验收依据。

**依赖**：任务 9。

**参考资料**

- `tests/test_skills_integration.py`：技能集成测试写法。
- `tests/test_app.py`：应用级端到端写法。

**内容**

用临时项目构造规则集覆盖：无条件规则、条件拦截、once、async、超时、变量占位、错误隔离。驱动完整 Agent 循环验证：拦截规则在工具前生效且拒绝原因回灌 LLM；prompt 注入出现在请求中；Hook 异常不中断循环。最后跑 `python -m unittest discover -s tests -v` 全量通过并勾选 checklist。
