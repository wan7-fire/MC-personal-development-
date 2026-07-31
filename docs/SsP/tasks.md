# ZXCode 提示词编排与缓存分层任务分解

> 共 10 个任务，按依赖顺序排列。参考规格：`docs/SsP/spec.md`；验收清单：`docs/SsP/checklist.md`。

---

## T1 - 梳理现有请求消息入口

**影响文件**：无代码改动，阅读 `zxcode/session.py`、`zxcode/agent.py`、`zxcode/app.py`、`zxcode/tools/base.py`

**依赖任务**：无

**内容**

- 确认当前 system prompt 来自 `zxcode/session.py` 的 `SYSTEM_PROMPT`。
- 确认 `ChatSession.request_messages` 是每轮请求消息的统一入口。
- 确认 `AgentLoop._apply_plan_only_prompt` 当前会修改 system 消息。
- 确认工具定义来自 `ToolRegistry.definitions()` 和 `Tool.definition()`。

**参考资料定位**

- `zxcode/session.py`：`SYSTEM_PROMPT`、`ChatSession.request_messages`
- `zxcode/agent.py`：`AgentLoop._apply_plan_only_prompt`、`AgentLoop._stream_model`
- `zxcode/tools/base.py`：`Tool.definition`、`ToolRegistry.definitions`

---

## T2 - 新增提示词模块数据结构

**影响文件**：`zxcode/prompts.py`（新建）、`tests/test_prompts.py`（新建）

**依赖任务**：T1

**内容**

- 定义稳定提示词模块的数据结构，字段包含模块名、优先级、内容。
- 提供默认稳定模块集合，覆盖身份、行为、代码规范、安全边界、工具使用、输出风格。
- 拼装时按优先级升序，再按模块名升序排序。
- 空内容模块不参与输出。

**参考资料定位**

- 现有最小系统提示：`zxcode/session.py` 的 `SYSTEM_PROMPT`
- 工具基础约束：`zxcode/tools/base.py` 的 `Tool.read_only`、`Tool.definition`

---

## T3 - 实现稳定前缀拼装

**影响文件**：`zxcode/prompts.py`、`tests/test_prompts.py`

**依赖任务**：T2

**内容**

- 提供稳定前缀构造函数，返回用于请求的 system 消息内容。
- 模块之间使用固定分隔符，保证输出可读且确定。
- 稳定前缀不读取工作目录、时间、Git 状态、会话开关或历史消息。
- 为稳定排序和稳定输出补测试。

**参考资料定位**

- 请求消息形态：`zxcode/session.py` 的 `request_messages`

---

## T4 - 支持项目内提示词模块文件

**影响文件**：`zxcode/prompts.py`、`tests/test_prompts.py`、`prompts/`（新目录，可选样例）

**依赖任务**：T3

**内容**

- 支持从项目根目录下的提示词模块目录加载 Markdown 文件。
- 文件名作为模块名，文件内容作为模块内容。
- 第一版不设计复杂配置格式；优先级可用简单文件名前缀表达。
- 读取失败、目录不存在或没有文件时回退到内置默认模块。

**参考资料定位**

- 工作目录来源：`ToolContext(Path.cwd(), ...)` 在 `zxcode/app.py` 创建 Agent 时的用法

---

## T5 - 新增动态环境消息构造

**影响文件**：`zxcode/prompts.py`、`tests/test_prompts.py`

**依赖任务**：T3

**内容**

- 提供环境补充消息构造函数。
- 第一版包含工作目录、操作系统、当前时间，Git 状态只做轻量摘要。
- 明确过滤 API Key、token、secret、password 等敏感环境变量。
- 环境消息单独返回，不拼入稳定前缀。

**参考资料定位**

- 当前工作目录传递：`zxcode/app.py` 中创建 `ToolContext`
- 运行配置来源：`zxcode/client.py` 的 `Settings.from_env`

---

## T6 - 改造会话请求消息顺序

**影响文件**：`zxcode/session.py`、`tests/test_session.py`

**依赖任务**：T3、T5

**内容**

- 用稳定前缀替换原有单字符串 `SYSTEM_PROMPT`。
- `request_messages` 输出顺序固定为：稳定 system 消息、动态环境消息、历史消息、当前用户消息。
- 保持 `commit_messages`、`clear`、`set_model` 行为不变。
- 补测试断言消息角色和顺序。

**参考资料定位**

- `zxcode/session.py`：`ChatSession.request_messages`、`commit_messages`

---

## T7 - 拆出会话开关动态提示

**影响文件**：`zxcode/agent.py`、`zxcode/prompts.py`、`tests/test_agent.py`

**依赖任务**：T6

**内容**

- plan-only 提示不再追加到稳定 system 消息。
- 把 plan-only 提示作为独立动态消息插入稳定前缀之后、历史消息之前。
- 保持 plan-only 写工具拦截行为不变。
- 补测试断言 `plan_only=True` 不改变稳定前缀内容。

**参考资料定位**

- `zxcode/agent.py`：`PLAN_ONLY_INSTRUCTION`、`_apply_plan_only_prompt`
- `zxcode/config.py`：`AgentConfig.plan_only`

---

## T8 - 强化工具使用规则描述

**影响文件**：`zxcode/prompts.py`、`zxcode/tools/files.py`、`zxcode/tools/search.py`、`zxcode/tools/shell.py`、`tests/test_tools.py`

**依赖任务**：T3

**内容**

- 在稳定工具使用模块中写明：优先使用专用工具；读文件用 ReadFile，搜索用 Glob/Grep，编辑用 EditFile/WriteFile，shell 只用于专用工具覆盖不了的场景。
- 在工具自身描述中保留或补齐关键边界：只读/写入、路径、确认、超时、排除目录。
- 不改工具执行逻辑，只改描述和测试断言。

**参考资料定位**

- `zxcode/tools/files.py`：ReadFile、WriteFile、EditFile 描述
- `zxcode/tools/search.py`：Glob、Grep 描述
- `zxcode/tools/shell.py`：Bash 描述和确认逻辑

---

## T9 - 接入主流程

**影响文件**：`zxcode/app.py`、`zxcode/session.py`、`zxcode/agent.py`、`README.md`

**依赖任务**：T6、T7、T8

**内容**

- 确保 `ZXCodeApp` 创建会话时可以使用新的提示词构造逻辑。
- 确保 Agent loop 每轮模型请求收到稳定前缀、动态环境、模式提示和历史消息。
- README 补充提示词分层的简短说明和不保存 API Key 的边界。
- 保持启动命令 `.\.venv\Scripts\python.exe -m zxcode` 不变。

**参考资料定位**

- `zxcode/app.py`：`ZXCodeApp.__init__`、`generate`
- `zxcode/session.py`：`ChatSession`
- `README.md`：配置、启动、内置工具章节

---

## T10 - 端到端验证

**影响文件**：`tests/test_session.py`、`tests/test_agent.py`、`docs/SsP/checklist.md`

**依赖任务**：T9

**内容**

- 用伪客户端验证一次普通多轮对话消息顺序正确。
- 用不同工作目录、不同时间构造两次请求，断言稳定前缀完全一致，动态环境消息不同。
- 用 plan-only 模式构造请求，断言稳定前缀不变，模式提示单独出现。
- 跑全量单元测试。
- 按 `docs/SsP/checklist.md` 勾验。

**参考资料定位**

- 现有测试命令：`.\.venv\Scripts\python.exe -m unittest discover -s tests -v`
- 现有 Agent 伪客户端写法：`tests/test_agent.py`
