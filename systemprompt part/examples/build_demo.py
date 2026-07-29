from __future__ import annotations

from json import dumps
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from instruction_pipeline import (  # noqa: E402
    EnvironmentSnapshot,
    build_request_payload,
    default_evaluation_scenarios,
    default_modules,
    digest,
    mode_reminder,
)


def main() -> None:
    environment = EnvironmentSnapshot(
        cwd="E:/mewcode exploit/systemprompt part",
        os="win32",
        shell="bash",
        ide_theme="light",
        current_time="2026-07-26T15:59:50+08:00",
        git_branch=None,
        git_status_summary="not_checked",
        python_version="3.13.12",
        node_version="22.22.2",
    )

    reminders = (
        mode_reminder("plan", turn_index=1),
        mode_reminder("craft", turn_index=6, changed=True),
    )

    payload = build_request_payload(
        modules=default_modules(),
        environment=environment,
        runtime_reminders=reminders,
        conversation_messages=(
            {"role": "user", "content": "按照这三份文档进行开发"},
        ),
        tool_schema_version="2026.07.1",
    )

    scenarios = default_evaluation_scenarios()

    output = {
        "cache": {
            "stable_bundle_hash": payload.cache_bundle.stable_bundle_hash,
            "dynamic_payload_hash": payload.dynamic_payload_hash(),
        },
        "assembled_global_instruction": payload.global_instruction,
        "messages": payload.as_messages(),
        "evaluation_scenarios": [scenario.__dict__ for scenario in scenarios],
        "runtime_reminder_hashes": [digest(reminder.to_xml()) for reminder in reminders],
    }

    out_dir = ROOT / "dist"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "demo_payload.json").write_text(dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "assembled_global_instruction.md").write_text(payload.global_instruction, encoding="utf-8")
    print("Wrote dist/demo_payload.json")
    print("Wrote dist/assembled_global_instruction.md")
    print("Stable bundle hash:", payload.cache_bundle.stable_bundle_hash)
    print("Dynamic payload hash:", payload.dynamic_payload_hash())


if __name__ == "__main__":
    main()
