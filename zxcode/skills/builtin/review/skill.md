---
name: review
description: 对当前改动进行代码审查并输出摘要
mode: isolated
history: recent
history_size: 10
tools:
- Bash
- ReadFile
- Grep
---
在独立对话中审查当前改动，完成后只把摘要带回来。

1. 用 `git status --short` 和 `git diff` 获取待审查改动；diff 过大时先按文件列表分批读取。
2. 按固定要点审查：逻辑正确性、错误处理、安全风险、可读性、是否破坏无关功能。
3. 输出结构化摘要，必须包含「结论」「变更」「未决问题」「状态」四个部分。
