---
name: audit
description: 隔离审查当前改动并回流四字段摘要
mode: isolated
history: none
tools:
- Bash
- ReadFile
- Grep
---
在独立对话中审查当前工作区改动，完成后只把摘要带回主对话。

1. 用 `git status --short` 和 `git diff --stat` 查看改动。
2. 按「逻辑正确性、错误处理、安全风险、可读性」四点快速审查。
3. 输出结构化摘要，必须包含「结论」「变更」「未决问题」「状态」四部分。
