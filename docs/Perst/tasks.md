# ZXCode 纵深防御安全检查任务分解

> 共 10 个任务，按依赖顺序排列。参考规格：`docs/Perst/spec.md`；验收清单：`docs/Perst/checklist.md`。

---

## T1 - 梳理现有安全边界

**影响文件**：无代码改动，阅读 `zxcode/tools/base.py`、`zxcode/tools/files.py`、`zxcode/tools/shell.py`、`zxcode/dispatch.py`、`zxcode/app.py`

**依赖任务**：无

**内容**

- 确认当前路径限制和确认逻辑分别落在哪些工具里。
- 确认调度层已经有 plan-only 和执行前后钩子的切点。
- 确认哪些检查已经存在，哪些只是局部防护。

**参考资料定位**

- `zxcode/tools/base.py`：`ToolContext`、`Tool`、`ToolRegistry`
- `zxcode/tools/files.py`：`resolve_path`、`_approved`
- `zxcode/tools/shell.py`：`_is_read_only`、`_path_policy`
- `zxcode/dispatch.py`：`_pre_hook`、`_post_hook`

---

## T2 - 定义安全策略数据结构

**影响文件**：`zxcode/security.py`（新建）、`tests/test_security.py`（新建）

**依赖任务**：T1

**内容**

- 定义策略结果类型：允许、询问、拒绝。
- 定义三档权限模式：严格、默认、放行。
- 定义规则对象：工具名、签名、模式、作用域、原因。
- 定义会话级临时规则、项目级规则和用户全局默认的承载方式。

**参考资料定位**

- 现有确认语义：`zxcode/tools/base.py` 中 `Confirm`
- 现有权限模式：`zxcode/config.py` 中 `AgentConfig.plan_only`

---

## T3 - 实现项目安全配置加载与写回

**影响文件**：`zxcode/security.py`、`tests/test_security.py`、`zxcode-security.toml`（新建样例）

**依赖任务**：T2

**内容**

- 读取项目根目录 `zxcode-security.toml`。
- 支持加载全局模式、黑名单、允许/拒绝/询问规则和临时规则存储段。
- 支持把永久允许规则写回同一配置文件。
- 读取失败时回退到安全默认值，不影响启动。

**参考资料定位**

- 项目根目录解析：`Path.cwd()` 在 `zxcode/app.py` 的使用

---

## T4 - 实现统一策略引擎

**影响文件**：`zxcode/security.py`、`tests/test_security.py`

**依赖任务**：T2、T3

**内容**

- 根据模式、规则优先级和黑名单返回 allow / ask / deny。
- 支持按工具名、命令签名、路径签名和参数模式匹配。
- 支持会话级临时允许优先于项目规则。
- 未命中规则时默认返回 ask。

**参考资料定位**

- 现有工具类别：`zxcode/tools/files.py`、`zxcode/tools/shell.py`

---

## T5 - 接入调度层预检

**影响文件**：`zxcode/dispatch.py`、`zxcode/security.py`、`tests/test_dispatch.py`

**依赖任务**：T4

**内容**

- 在工具执行前调用策略引擎。
- 对被 deny 的调用直接短路并返回结构化错误。
- 对 ask 的调用把确认请求交回界面或上下文确认函数。
- 保留现有读写并发和结果顺序。

**参考资料定位**

- `zxcode/dispatch.py`：`_pre_hook`、`_run_one`

---

## T6 - 接入工具层兜底

**影响文件**：`zxcode/tools/files.py`、`zxcode/tools/shell.py`、`zxcode/tools/base.py`、`tests/test_file_tools.py`、`tests/test_shell_tool.py`

**依赖任务**：T4

**内容**

- 文件工具继续保留路径沙箱兜底。
- shell 工具继续保留危险命令和越权路径检查。
- 工具自身在执行前再次咨询策略引擎。
- 只读工具保持原有行为，不扩入第一版范围。

**参考资料定位**

- `zxcode/tools/files.py`：`resolve_path`、`WriteFile.execute`、`EditFile.execute`
- `zxcode/tools/shell.py`：`Bash.execute`、`_path_policy`

---

## T7 - 实现 HITL 允许态

**影响文件**：`zxcode/security.py`、`zxcode/tools/base.py`、`zxcode/app.py`、`tests/test_security.py`、`tests/test_app.py`

**依赖任务**：T4、T5、T6

**内容**

- 支持本次允许、本会话允许、永久允许。
- 本次允许只影响当前调用。
- 本会话允许记录到会话级规则。
- 永久允许写回 `zxcode-security.toml`，只写精确命令或精确路径签名。

**参考资料定位**

- 现有确认弹窗：`zxcode/app.py` 中 `ConfirmScreen`、`confirm_tool`

---

## T8 - 强化权限模式与优先级

**影响文件**：`zxcode/security.py`、`zxcode/config.py`、`tests/test_security.py`

**依赖任务**：T4

**内容**

- 实现严格、默认、放行三档全局模式。
- 硬黑名单在任何模式下都生效。
- 规则优先级固定为会话级临时规则 > 项目级固定规则 > 用户全局默认 > 模式兜底。
- 默认模式下未命中规则时必须 ask。

**参考资料定位**

- 现有配置容器：`zxcode/config.py`

---

## T9 - 接入主流程

**影响文件**：`zxcode/app.py`、`zxcode/dispatch.py`、`zxcode/security.py`、`README.md`

**依赖任务**：T5、T6、T7、T8

**内容**

- 把安全策略接入应用启动路径。
- 让确认弹窗和安全结果在界面中可见。
- README 增加安全配置和权限模式说明。
- 默认行为保持可用，不破坏原有启动命令。

**参考资料定位**

- `zxcode/app.py`：`ZXCodeApp.__init__`、`confirm_tool`
- `README.md`：配置、启动、工具章节

---

## T10 - 端到端验证

**影响文件**：`tests/test_security.py`、`tests/test_dispatch.py`、`tests/test_app.py`、`docs/Perst/checklist.md`

**依赖任务**：T9

**内容**

- 验证高危 shell 命令被黑名单拦截。
- 验证越权路径读写被拒绝。
- 验证默认模式未命中时会询问用户。
- 验证本会话允许和永久允许都会生效。
- 验证永久允许写回配置文件。
- 跑全量单元测试。
- 按 `docs/Perst/checklist.md` 勾验。

**参考资料定位**

- 现有测试命令：`.\.venv\Scripts\python.exe -m unittest discover -s tests -v`
- 现有确认测试：`tests/test_app.py`
