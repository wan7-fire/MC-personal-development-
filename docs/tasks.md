# MewCode 六核心工具与 LLM 工具循环任务

## 任务 1：建立工具契约与显式注册表

- **目标**：定义最小工具契约、调用上下文、结构化结果和注册表；显式注册六个内置工具，拒绝重名，并能导出 OpenAI-compatible 工具描述。
- **影响文件**：`mewcode/tools/__init__.py`、`mewcode/tools/base.py`、`tests/test_tool_runtime.py`。
- **依赖任务**：无。
- **参考资料定位**：`agnent loop part/实现方案与工程指南.md:46` 的工具注册职责；`agnent loop part/事件流与工具执行规范.md:110` 的工具结果结构；`pyproject.toml:8-14` 的 Python 版本和现有依赖。

## 任务 2：实现统一执行器

- **目标**：实现单调用校验、默认与工具级超时、异常归一、输出截断以及批量“读并发、读后写、写串行”调度；保持每个结果与原调用标识对应。
- **影响文件**：`mewcode/tools/base.py`、`mewcode/tools/executor.py`、`tests/test_tool_runtime.py`。
- **依赖任务**：任务 1。
- **参考资料定位**：`agnent loop part/事件流与工具执行规范.md:206` 的批量执行策略；`agnent loop part/实现方案与工程指南.md:413` 的执行器参考实现；Python `asyncio.gather`、`asyncio.timeout` 文档。

## 任务 3：建立共享文件边界并实现 ReadFile

- **目标**：集中实现工作目录真实路径校验、路径穿越/绝对路径/符号链接逃逸拒绝、UTF-8 与文件大小限制；在此基础上实现按行读取和行号输出。
- **影响文件**：`mewcode/tools/files.py`、`tests/test_file_tools.py`。
- **依赖任务**：任务 1。
- **参考资料定位**：`docs/spec.md` 的“文件与搜索边界”；Python `pathlib.Path.resolve` 与 `Path.is_relative_to` 文档；`agnent loop part/实现方案与工程指南.md` 的工具安全边界章节。

## 任务 4：实现 WriteFile 与多段 EditFile

- **目标**：实现新建文件、覆盖前内容指纹校验与授权，以及多段唯一匹配、基于原快照定位、区间重叠检测和同目录临时文件原子替换；任何失败保持原文件不变。
- **影响文件**：`mewcode/tools/files.py`、`tests/test_file_tools.py`。
- **依赖任务**：任务 1、任务 3。
- **参考资料定位**：`docs/spec.md` 的“数据完整性”和“文件与搜索边界”；Python `hashlib.sha256`、`tempfile.NamedTemporaryFile`、`os.replace` 文档。

## 任务 5：实现 Glob 与 Grep

- **目标**：用标准库实现根目录相对文件匹配、固定目录排除、修改时间倒序与稳定排序；实现正则文本搜索，并复用文件边界、忽略规则和有界输出。
- **影响文件**：`mewcode/tools/search.py`、`tests/test_search_tools.py`。
- **依赖任务**：任务 1、任务 3。
- **参考资料定位**：`docs/spec.md` 的“文件与搜索边界”；`docs/checklist.md` 的“Glob 验收”和“Grep 验收”；Python `pathlib`、`fnmatch`、`re` 文档。

## 任务 6：实现 PowerShell Bash 与单次授权策略

- **目标**：实现非交互式 PowerShell 子进程、项目工作目录、输出捕获、超时与进程树清理；只自动放行明确的项目内只读命令，其他命令通过执行上下文中的单次确认回调处理，越界和持久后台命令直接拒绝。
- **影响文件**：`mewcode/tools/shell.py`、`mewcode/tools/base.py`、`tests/test_shell_tool.py`。
- **依赖任务**：任务 1、任务 2、任务 3。
- **参考资料定位**：`docs/spec.md` 的“命令执行与授权”；`mewcode/app.py:110-114` 的现有取消入口；Python `asyncio.create_subprocess_exec` 文档；Windows `taskkill /T` 行为说明。

## 任务 7：接入 SDK 流式工具调用与循环终止

- **目标**：让客户端在保留文本增量的同时获得 SDK 累积后的完整工具调用；实现“请求—执行工具—回传结果—再次请求”的循环，并把现有终止器迁入可导入的 MewCode 模块复用。
- **影响文件**：`mewcode/client.py`、`mewcode/agent.py`、`mewcode/session.py`、`tests/test_client.py`、`tests/test_agent.py`、`tests/test_session.py`。
- **依赖任务**：任务 1、任务 2。
- **参考资料定位**：`mewcode/client.py:32-56` 的现有流式客户端；`mewcode/session.py:13-34` 的会话快照与提交；`agnent loop part/agent/loop/terminator.py:10-127` 的现有终止逻辑；Context7 `/openai/openai-python/v2.11.0` 中 `chat.completions.stream()`、`get_final_completion()` 和 tool message 文档。

## 任务 8：补齐错误、取消、安全和回归测试

- **目标**：覆盖结构化错误码、非 fail-fast 批处理、输出截断、工具超时、用户拒绝、模型不支持工具、循环终止、取消清理以及原有纯文本聊天回归；测试不得访问真实 API 或项目外真实文件。
- **影响文件**：`tests/test_tool_runtime.py`、`tests/test_file_tools.py`、`tests/test_search_tools.py`、`tests/test_shell_tool.py`、`tests/test_agent.py`、`tests/test_app.py`。
- **依赖任务**：任务 2、任务 4、任务 5、任务 6、任务 7。
- **参考资料定位**：`tests/test_app.py:47-176` 的无头 UI 测试模式；`tests/test_client.py:11-82` 的伪造流模式；`agnent loop part/tests/test_terminator.py` 的终止器测试；`docs/checklist.md` 的错误、取消与限制条目。

## 任务 9：接入主流程

- **目标**：在应用启动时显式构造注册表、六个工具、执行器和 Agent 循环；把 `MewCodeApp.generate` 改为消费文本、工具状态、授权请求和最终结果，并保持现有快捷键、斜杠命令、状态栏和事务式会话语义。
- **影响文件**：`mewcode/app.py`、`mewcode/__main__.py`、`mewcode/session.py`、`tests/test_app.py`、`tests/test_main.py`、`README.md`。
- **依赖任务**：任务 3、任务 4、任务 5、任务 6、任务 7、任务 8。
- **参考资料定位**：`mewcode/app.py:42-48` 的依赖构造；`mewcode/app.py:66-83` 的提交入口；`mewcode/app.py:117-145` 的现有生成流程；`mewcode/__main__.py:9-20` 的启动入口；`mewcode/session.py:21-34` 的请求与提交边界。

## 任务 10：端到端验证

- **目标**：运行全部自动化测试；在临时项目中用伪造 LLM 完成读取、搜索、创建、确认覆盖、编辑、命令授权和最终回答；最后用真实兼容 API 完成至少一次工具调用和下一轮上下文追问，并逐项记录验收结果。
- **影响文件**：`docs/checklist.md`（仅勾选已实际验证的条目）；发现缺陷时返回对应实现或测试文件修复。
- **依赖任务**：任务 9。
- **参考资料定位**：`docs/checklist.md` 全部条目；`docs/spec.md` 的“完成定义”；`tests/test_app.py:57-174` 的现有流式、失败和取消端到端路径。
