# ZXCode Skill 系统验收清单

每一项都必须可勾选、可观测。

## 文件格式与解析

- [ ] `python -c "import zxcode.skills"` 退出码为 0。
- [ ] `grep -n "class SkillMeta" zxcode/skills/model.py` 返回至少 1 条。
- [ ] 临时目录放入只有 `name`、`description`、`mode` 的 `skills/demo.md`，`scan_skills` 返回 1 个 Skill，且 `demo.description` 与文件一致。
- [ ] 同目录放入缺 `name` 的 `broken.md`，扫描不抛异常、返回 0 个 `broken`，且 `issues` 中至少有 1 条包含 `broken.md` 和原因。
- [ ] 三级目录同时存在同名 `demo` 时，`scan_skills` 只返回 1 个，且 `source` 指向项目级路径。
- [ ] `history_size` 未填写时解析结果为 10；填写 `history_size: 3` 时解析结果为 3。
- [ ] 未声明 `tools` 时解析结果为 `None`；声明 `tools: [ReadFile]` 时元组长度为 1 且只含 `ReadFile`。
- [ ] 白名单含 `NoSuchTool` 时 `scan_skills` 抛 `SkillValidationError`，异常文本包含 Skill 名和文件路径。

## 启动加载

- [ ] 应用启动后 `/skills` 显示 3 个内置 Skill：`commit`、`review`、`test`。
- [ ] 启动后首次请求的 system 区包含「可用 Skills」，且只含名字和一句话说明，不含任何 Skill 的 SOP 正文。
- [ ] 项目/用户目录新增 Skill 后执行 `/skills rescan`，不重启应用即可在列表看到。
- [ ] 用户级放入坏 frontmatter 的 `broken.md` 后启动不崩溃，`/skills` 不含 `broken`，界面上有跳过提示。
- [ ] 项目级放入 `tools: [NoSuchTool]` 的 `bad.md` 后启动失败（退出码非 0 或抛异常），错误信息包含 `bad.md`。

## 环境上下文与激活

- [ ] 通过系统级加载工具激活 `commit` 后，下一次请求 system 区包含 `[Skill 指令：commit]`。
- [ ] 激活后 `session.messages` 中不含 `[Skill 指令：commit]`，完整指令未进入普通历史。
- [ ] 连续激活 `commit` 和 `test` 后，system 区按激活顺序同时包含两条 `[Skill 指令：…]`。
- [ ] 再次激活已激活的 `commit` 不产生重复消息，system 区中 `[Skill 指令：commit]` 仍只有 1 条。
- [ ] 修改 `commit/skill.md` 正文后再次执行 `/commit` 或重新激活，新正文出现在 system 区。
- [ ] 执行 `/clear` 后，下一次请求 system 区不包含任何 `[Skill 指令：…]`，且 `/skills` 仍可列出 Skill 索引。

## 工具白名单

- [ ] `tools: [ReadFile]` 的 Skill 激活后，`registry.definitions()` 只包含 `ReadFile` 和系统级加载工具，不包含 `WriteFile`、`Bash`、`Grep`。
- [ ] 白名单分别为 `[ReadFile]` 与 `[Grep]` 的两个 Skill 同时激活后，`definitions()` 包含 `ReadFile`、`Grep` 和系统级加载工具。
- [ ] 未声明白名单的 Skill 激活后，`definitions()` 为全部注册工具加系统级加载工具。
- [ ] 系统级加载工具始终出现在 `definitions()` 中，即使当前白名单没有它。
- [ ] 目录型 Skill 自带工具 `echo` 激活后，`definitions()` 包含 `echo`。
- [ ] 白名单引用 `mcp_x` 但 registry 中没有该工具时，启动扫描抛 `SkillValidationError`，错误含 Skill 名和文件路径。

## 共享模式

- [ ] 内置 `commit` 的 frontmatter `mode` 为 `shared`。
- [ ] 执行 `/commit` 后主历史出现 assistant 的 git 相关工具调用和最终文本。
- [ ] 执行 `/commit` 后 `session.messages` 中不含 `[Skill 指令：commit]`。
- [ ] plan-only 模式下激活共享 Skill 不报错，但写类工具调用被拦截，`blocked_calls` 非空。

## 隔离模式

- [ ] 内置 `review` 的 frontmatter `mode` 为 `isolated`，`history` 为 `recent`。
- [ ] 触发 `/review` 后，主历史新增 1 条摘要消息，摘要包含 `结论：`、`变更：`、`未决问题：`、`状态：` 四个字段。
- [ ] 隔离执行完成后，主历史不包含 review 的完整 SOP 正文。
- [ ] `history: none` 的隔离 Skill，其子会话消息中不包含主历史的任何 user/assistant/tool 消息。
- [ ] `history: recent` 且 `history_size: 3` 时，子会话只携带主历史最后 3 条消息。
- [ ] 隔离执行后 `/sessions` 列表数量不增加。
- [ ] 隔离 Skill 执行结束后 `active_skill_messages()` 为空，SOP 不残留主会话。

## 目录型 Skill 工具

- [ ] 临时目录型 Skill 带 `tools/echo.md` 与 `echo.py`，`/skills <name>` 详情列出 `echo`。
- [ ] 调用 `echo` 并传 `{"text": "hi"}` 时输出包含 `hi`。
- [ ] 调用 `echo` 缺必填参数时返回 `invalid_arguments`。
- [ ] 带安全策略（默认模式）时，即使声明 `read_only: true`，调用脚本工具也请求确认；拒绝后返回 `permission_denied`，且脚本未执行。
- [ ] 安全策略为 allow 模式时脚本工具直接执行，不弹确认；strict 模式无 allow 规则时返回 `security_blocked`。
- [ ] 项目级目录型 Skill 带脚本时直接 `activate` 抛 `SkillActivationError` 且错误含 `confirmation`；经 `confirm_activate` 批准后脚本工具可加载，拒绝后不激活。
- [ ] 内置级目录型 Skill 带脚本时无需确认即可激活。
- [ ] `tools/` 目录以联接/符号链接指向 skill 目录外时，`load_skill_tools` 返回空；skill 文件本身解析到扫描根目录外时被跳过且 `issues` 记录该路径。
- [ ] 工具脚本输出超过 65536 字符时返回截断结果，metadata 含 `truncated: true`。
- [ ] 工具脚本超过 30 秒未返回时返回 `timeout`。

## 命令与帮助

- [ ] `/help` 输出包含 `/skills`、`/commit`、`/review`、`/test`。
- [ ] `/skills` 默认列出全部已加载 Skill，每行含名字、说明、来源层级和模式。
- [ ] `/skills commit` 输出包含 `mode: shared`、工具白名单和源文件路径。
- [ ] `/skills rescan` 后项目新增 Skill 出现在列表，删除后从列表消失。
- [ ] `/skills rescan` 后新增 Skill 自动注册 `/新名字` 短命令，并出现在 `/help` 中。
- [ ] `grep -n "_review" zxcode/commands/builtins.py` 返回 0 条（旧占位已移除）。

## 内置样板

- [ ] `commit/skill.md` 正文包含检查 git 状态、暂存、提交、验证的步骤。
- [ ] `review/skill.md` 正文包含获取 diff、按固定要点审查、输出摘要的步骤。
- [ ] `test/skill.md` 正文包含发现测试命令、运行测试、汇总结果的步骤。
- [ ] `commit` 与 `test` 的 mode 为 `shared`；`review` 的 mode 为 `isolated`。

## 端到端验收

- [ ] `python -m unittest discover -s tests -v` 全部通过。
- [ ] 手工流程：临时项目放 `demo`（shared）和 `iso`（isolated），启动应用 → `/skills` 两者可见 → `/demo` 后主历史出现结果且白名单生效 → `/iso` 后主历史出现四字段摘要 → `/clear` 后无激活指令残留。
- [ ] 手工热更新：修改 `demo/skill.md` 描述后 `/skills rescan`，`/skills demo` 显示新描述；修改正文后 `/demo`，请求中的正文为新内容。
