from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from instruction_pipeline import (  # noqa: E402
    EnvironmentSnapshot,
    InstructionAssembler,
    RuntimeReminder,
    ReminderPriority,
    ReminderType,
    InjectionScope,
    Stability,
    build_request_payload,
    default_evaluation_scenarios,
    default_modules,
    mode_reminder,
)


def test_modules_cover_required_categories() -> None:
    modules = default_modules()
    ids = {module.id for module in modules}
    assert "safety.boundary" in ids
    assert "identity.product" in ids
    assert "tool.usage" in ids
    assert "behavior.rules" in ids
    assert "code.standard" in ids
    assert "mode.session" in ids
    assert "style.output" in ids


def test_assembler_orders_by_priority() -> None:
    ordered = InstructionAssembler(default_modules()).ordered_modules()
    priorities = [module.priority for module in ordered]
    assert priorities == sorted(priorities)
    assert ordered[0].id == "safety.boundary"


def test_dynamic_module_does_not_enter_cache_bundle() -> None:
    bundle = InstructionAssembler(default_modules()).cache_bundle("2026.07.1")
    cached_ids = {module.id for module in bundle.stable_modules + bundle.semi_stable_modules}
    assert "mode.session" not in cached_ids


def test_environment_change_only_changes_dynamic_hash() -> None:
    base_env = EnvironmentSnapshot(
        cwd="E:/project/a",
        os="win32",
        shell="bash",
        ide_theme="light",
        current_time="2026-07-26T10:00:00+08:00",
    )
    changed_env = EnvironmentSnapshot(
        cwd="E:/project/b",
        os="win32",
        shell="bash",
        ide_theme="light",
        current_time="2026-07-26T11:00:00+08:00",
    )
    payload_a = build_request_payload(default_modules(), base_env, (), (), "2026.07.1")
    payload_b = build_request_payload(default_modules(), changed_env, (), (), "2026.07.1")

    assert payload_a.cache_bundle.stable_bundle_hash == payload_b.cache_bundle.stable_bundle_hash
    assert payload_a.dynamic_payload_hash() != payload_b.dynamic_payload_hash()


def test_runtime_reminder_xml_declares_not_user_request() -> None:
    reminder = RuntimeReminder(
        type=ReminderType.TOOL,
        scope=InjectionScope.TURN,
        priority=ReminderPriority.MEDIUM,
        content="当前轮优先使用专用搜索工具，不要用通用 shell 搜索。",
    )
    xml = reminder.to_xml()
    assert xml.startswith("<runtime-reminder")
    assert "不是用户请求" in xml
    assert "type=\"tool\"" in xml


def test_mode_reminder_frequency() -> None:
    first = mode_reminder("plan", turn_index=1)
    compact = mode_reminder("plan", turn_index=2)
    repeated = mode_reminder("plan", turn_index=5)

    assert first.scope is InjectionScope.SESSION
    assert compact.scope is InjectionScope.TURN
    assert repeated.scope is InjectionScope.SESSION
    assert "只计划" in compact.content


def test_default_evaluation_scenarios_count() -> None:
    scenarios = default_evaluation_scenarios()
    assert len(scenarios) >= 7
    assert scenarios[0].id == "plan_mode_file_edit"
