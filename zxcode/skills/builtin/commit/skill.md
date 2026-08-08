---
name: commit
description: 检查工作区并完成一次 git 提交
mode: shared
tools:
- Bash
- ReadFile
- Grep
---
按以下 SOP 完成一次 git 提交：

1. 先用 `git status --short` 和 `git diff --stat` 检查工作区，确认要提交的改动。
2. 如果用户没有明确指定提交信息，先读取最近的提交风格（`git log -3 --oneline`）再拟一条。
3. 用 `git add` 暂存本次要提交的文件；不要提交无关文件。
4. 用 `git commit -m <信息>` 提交。
5. 用 `git status --short` 和 `git log -1 --stat` 验证提交成功，并报告提交哈希和改动概览。
