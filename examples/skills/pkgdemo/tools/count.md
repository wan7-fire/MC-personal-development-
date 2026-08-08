---
name: count
description: 统计输入文本的字符数并原样返回
input_schema: {"type":"object","properties":{"text":{"type":"string","minLength":1}},"required":["text"],"additionalProperties":false}
read_only: true
timeout_seconds: 5
---
约定：从 stdin 读取 JSON 参数，向 stdout 输出
{"success": bool, "output": str}。
