# ZXCode 六核心工具与 LLM 工具循环验收清单

## 基线与工具注册

- [ ] 执行 `python -m unittest discover -s tests -v` 返回退出码 `0`，原有纯文本对话、快捷键、斜杠命令、失败和取消测试全部通过。
- [ ] `pyproject.toml` 的运行依赖仍只有 `openai>=2.11` 与 `textual>=6.6`，没有为工具运行时新增第三方依赖。
- [ ] 注册表导出的工具名称集合严格等于 `ReadFile`、`WriteFile`、`EditFile`、`Bash`、`Glob`、`Grep`，没有缺项或额外工具。
- [ ] 注册同名工具时立即失败，错误码为 `duplicate_tool`，且原注册项仍可查询。
- [ ] 查询不存在的工具返回结构化结果，错误码为 `unknown_tool`，错误文本为 `unknown tool: <name>`，主循环不抛出未处理异常。
- [ ] 每个结果至少可观察到 `success`、`output`、`error`、`metadata`；失败结果的 `error` 至少包含 `code` 与 `message`。

## 执行器、超时与输出

- [ ] 未声明专用超时的工具使用 `30` 秒默认超时；声明专用超时的伪工具按自己的值停止，不等待默认值。
- [ ] 参数不符合工具定义时不进入工具实现，错误码为 `invalid_arguments`，错误文本以 `invalid arguments:` 开头。
- [ ] 工具超时返回错误码 `timeout`，错误文本为 `tool timed out after <seconds>s`，同批其他调用仍返回各自结果。
- [ ] 工具内部异常返回错误码 `execution_error`，错误文本为 `execution failed`，结果和日志中均不出现 Python traceback。
- [ ] 一批包含至少两个只读伪工具时，两者的执行时间区间发生重叠，证明读取并发执行。
- [ ] 一批同时包含读写伪工具时，全部只读工具的结束时间早于第一个写工具的开始时间。
- [ ] 调度分类中 `ReadFile`、`Glob`、`Grep` 属于并发读取阶段，`WriteFile`、`EditFile`、`Bash` 属于串行写阶段；只读 Bash 命令也不进入并发阶段。
- [ ] 一批包含至少两个写伪工具时，写工具按模型给出的先后顺序执行且时间区间不重叠。
- [ ] 同批一个只读工具失败时，其他只读工具和后续写工具仍被执行，最终结果按原调用顺序排列并保留各自调用 ID。
- [ ] 单个工具输出上限为 `65,536` 个 UTF-8 字节；构造更大输出后，返回文本仍是合法 UTF-8，`metadata.truncated=true` 且包含原始字节数。
- [ ] 日志包含工具名、调用 ID、错误类别与耗时，但搜索 `LLM_API_KEY` 的真实值和测试敏感样本文本均返回 `0` 条日志命中。

## 路径边界与 ReadFile

- [ ] 传入 `../outside.txt` 返回错误码 `path_outside_root` 和文本 `path is outside working directory`，项目外文件未被读取或修改。
- [ ] 传入指向工作目录外的绝对路径得到相同拒绝结果。
- [ ] 在工作目录内建立指向外部目录的符号链接或目录联接后，经该入口读取外部文件仍得到 `path_outside_root`。
- [ ] 大小为 `1,048,576` 字节的有效 UTF-8 文件可以读取；大小为 `1,048,577` 字节的文件返回 `file_too_large` 和文本 `file exceeds 1048576 bytes`。
- [ ] 包含无效 UTF-8 字节的文件返回 `invalid_utf8` 和文本 `file is not valid UTF-8`，不使用系统默认编码降级读取。
- [ ] 对三行文件请求第 `2` 到第 `3` 行时只返回两行，输出前缀分别为 `2: ` 和 `3: `。
- [ ] 起止行缺省时返回完整文件；起止行越界或起始行大于结束行时返回 `invalid_arguments`。

## WriteFile 与 EditFile

- [ ] `WriteFile` 创建不存在的项目内 UTF-8 文件时不弹出确认，写入后内容逐字节等于请求内容。
- [ ] `WriteFile` 覆盖现有文件但未提供当前内容 SHA-256 时返回 `invalid_arguments`，文件保持不变。
- [ ] `WriteFile` 提供错误 SHA-256 时不弹出确认，返回 `conflict` 和文本 `file changed since it was read`，文件保持不变。
- [ ] `WriteFile` 提供正确 SHA-256 时只弹出一次确认；拒绝返回 `permission_denied` 和文本 `permission denied by user`，批准后才覆盖文件。
- [ ] `EditFile` 的每个 `old_text` 在编辑前内容中恰好匹配一次；零次或多次匹配均返回 `edit_match_error` 且文件保持不变。
- [ ] 两段编辑的原始匹配区间重叠时返回 `edit_overlap`，任何替换均不落盘。
- [ ] 多段编辑全部基于编辑前快照定位；前一段替换产生的新文本不会成为后一段的匹配目标。
- [ ] 任一编辑段失败、内容指纹冲突、用户拒绝或任务取消时，目标文件的 SHA-256 与执行前完全相同。
- [ ] 多段编辑通过校验后只确认一次，并通过同目录临时文件与原子替换一次性提交；测试过程观察不到半写内容。

## Glob 验收

- [ ] `Glob` 的元信息严格包含 `read_only=true`、`destructive=false`、`category="search"`。
- [ ] 对包含 `.git`、`node_modules`、`vendor`、`.idea`、`.venv`、`__pycache__` 的临时目录执行 `**/*`，结果中这些目录下的文件命中数为 `0`。
- [ ] 搜索根目录只能是工作目录或其子目录；根目录越界和经符号链接逃逸均返回 `path_outside_root`。
- [ ] 三个匹配文件具有不同修改时间时，输出顺序严格按修改时间倒序；修改时间相同时按相对路径升序。
- [ ] 输出每行只有一个相对于搜索根目录的文件路径，不包含大小、时间、绝对路径或说明文字。
- [ ] 构造 `201` 个匹配文件后只返回前 `200` 个，`metadata.truncated=true`，且这 `200` 个仍满足修改时间倒序。

## Grep 验收

- [ ] `Grep` 使用 Python 标准库正则语义；模式 `class\s+\w+` 能命中类定义，无效正则返回 `invalid_arguments`。
- [ ] 每条匹配按 `相对路径:行号:文本` 输出，行号从 `1` 开始。
- [ ] `.git`、`.venv`、`__pycache__` 内的文本不会产生匹配；`Grep` 不继承本次仅为 `Glob` 增加的其他目录排除项。
- [ ] 无效 UTF-8 文件和超过 `1,048,576` 字节的文件会被跳过，元数据分别报告跳过数量。
- [ ] 构造超过 `1,000` 条匹配后只返回前 `1,000` 条并设置 `metadata.truncated=true`。
- [ ] `Grep` 输出超过统一 `65,536` 字节限制时仍应用执行器截断规则，且不会产生无效 UTF-8。

## Bash 与授权

- [ ] `Bash` 在 Windows 上启动非交互式 PowerShell，执行 `Get-Location` 返回的真实路径等于当前项目工作目录。
- [ ] 对项目内文件执行简单 `Get-Content` 或执行 `Get-ChildItem` 时自动运行，不出现授权提示。
- [ ] 含根目录外绝对路径、`..` 越界或已知符号链接逃逸的命令直接返回 `path_outside_root`，不进入授权提示。
- [ ] `Set-Content`、`Remove-Item`、下载、重定向、嵌套命令或动态变量命令均不会自动执行，而是显示当前完整命令和风险原因并请求单次确认。
- [ ] 用户拒绝 Bash 请求时返回 `permission_denied`，命令没有产生文件或进程副作用。
- [ ] 用户批准后只执行当前这一条完整命令；再次提交相同命令仍再次请求确认。
- [ ] `Start-Job`、持久 `Start-Process` 或其他明确后台常驻命令返回 `background_process_not_allowed`，即使用户愿意授权也不启动。
- [ ] Bash 达到超时或用户取消时返回对应结构化结果，父 PowerShell 及其测试子进程均在 `1` 秒内结束。
- [ ] Bash 标准输出与标准错误均被捕获；合计超过 `65,536` 字节时按统一规则截断。

## LLM 工具循环与终止

- [ ] 每次 LLM 请求都携带注册表导出的六个工具定义，普通纯文本模型响应仍逐段显示。
- [ ] 流式工具参数被 SDK 累积为完整 JSON 后才执行；分片本身不会触发重复或半参数调用。
- [ ] assistant 工具调用消息保留服务端给出的调用 ID，每个回传结果使用完全相同的 ID 和 `tool` 角色。
- [ ] 一轮模型返回多个工具调用时只调用执行器一次，所有结构化结果都写回下一次模型请求。
- [ ] 伪造模型依次返回 `ReadFile` 调用和文本 `读取完成` 时，文件结果出现在第二次请求中，界面最终显示 `读取完成`。
- [ ] 模型没有返回工具调用时不进入执行器，完整文本直接作为最终回答提交。
- [ ] 模型或兼容服务不支持工具调用时显示 `tool calling is not supported by the configured model`，不尝试从普通文本解析命令。
- [ ] 循环终止默认阈值保持为最大 `20` 轮、同工具/参数/观察重复 `3` 次、同错误重复 `2` 次、无进展 `4` 轮。
- [ ] 达到任一终止条件后不再发送 LLM 请求或执行工具，并在结构化结束状态中报告 `max_turns`、`repeated_observation`、`repeated_error` 或 `no_progress`。
- [ ] 用户取消时停止当前模型流、并发读取、待执行写入和 Bash 进程；残缺助手文本与内部工具消息不提交到下一轮会话。
- [ ] 一轮中包含内部工具消息时，状态栏用户轮数仍只增加 `1`；下一轮请求保留完成工具调用所需的 assistant/tool 历史。

## 终端与端到端验证

- [ ] 覆盖文件或执行 `EditFile` 时，Textual 界面显示工具名、目标相对路径和“批准/拒绝”选项；一次选择只解决当前调用。
- [ ] Bash 请求授权时界面显示完整命令和风险原因，拒绝后模型能收到 `permission_denied` 并继续给出解释性最终回答。
- [ ] `Enter` 仍插入换行；Windows 下 `Ctrl+S` 仍发送；支持增强键盘协议的终端中 `Ctrl+Enter` 仍发送。
- [ ] 生成期间按 `Ctrl+C` 后 `1` 秒内停止追加文本或启动新工具，用户原输入保留，并且随后可以重新发送消息。
- [ ] 使用 Textual `run_test()` 与伪造 LLM 完成“用户请求—Glob/Grep/ReadFile—WriteFile 新建—EditFile 授权—Bash 拒绝—最终回答”，全程无真实网络请求。
- [ ] 在临时项目中先读取文件取得 SHA-256，再修改该文件并尝试覆盖，界面显示冲突且磁盘保留外部修改内容。
- [ ] 使用有效的 OpenAI-compatible 配置启动 `python -m zxcode`，让模型至少成功调用一次 `ReadFile`，随后最终回答准确引用文件内容。
- [ ] 在上述真实 API 会话中继续追问上一轮结果，模型能够利用已提交的用户、assistant 工具调用、tool 结果和最终 assistant 文本作答。
- [ ] 完成真实 API 测试后，项目文件、会话消息、日志和界面中均未出现 `LLM_API_KEY` 的完整值。
