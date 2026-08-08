---
name: test
description: 发现并运行项目测试后汇总结果
mode: shared
tools:
- Bash
- Glob
- ReadFile
---
按以下 SOP 运行项目测试：
1. 先读 `pyproject.toml` 和 `README.md` 确认测试命令；没有明确命令时使用：
   `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`
2. 用 Bash 工具直接运行该命令并等待结果。不要使用 Start-Process、cmd /c、
   输出重定向（>、2>&1）、后台任务或临时文件——Bash 工具会自动返回命令的
   stdout/stderr。
3. 汇总通过数、失败数和失败原因；如果命令失败，读取失败输出并给出修复建议。
