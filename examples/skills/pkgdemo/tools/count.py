import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

data = json.load(sys.stdin)
text = data.get("text", "")
json.dump(
    {"success": True, "output": f"字符数={len(text)}：{text}"},
    sys.stdout,
    ensure_ascii=False,
)
