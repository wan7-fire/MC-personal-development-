---
name: hello
description: 演示共享模式与白名单
mode: shared
tools:
- ReadFile
---
按以下 SOP 执行：

1. 用 ReadFile 读取项目根目录的 README.md（若存在，只读前 20 行）。
2. 用一句话概括这个项目的用途，并说明你当前可见的工具只有 ReadFile。
