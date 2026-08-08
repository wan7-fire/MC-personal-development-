"""UI control interface decoupling commands from the terminal framework."""

from __future__ import annotations

from time import monotonic
from typing import TYPE_CHECKING, Any, Protocol, Sequence, runtime_checkable

from textual.containers import VerticalScroll
from textual.widgets import Static

from ..compress import estimate_messages

if TYPE_CHECKING:
    from ..app import ZXCodeApp


@runtime_checkable
class UIControl(Protocol):
    """Operations commands may perform; implemented by a Textual adapter."""

    def notice(self, text: str) -> None: ...

    def clear_chat(self) -> None: ...

    def exit_app(self) -> None: ...

    def set_model(self, name: str) -> None: ...

    def toggle_plan_mode(self) -> bool: ...

    def refresh_status(self) -> None: ...

    def token_estimate(self) -> int: ...

    def status_summary(self) -> dict[str, Any]: ...

    def send_user_message(
        self, text: str, system_parts: Sequence[str] = ()
    ) -> None: ...

    def run_compact(self) -> None: ...

    def resume_session(self, session_id: str) -> None: ...

    def choose_session(self) -> None: ...

    def delete_session(self, session_id: str) -> None: ...

    def clear_sessions(self) -> None: ...

    def list_sessions(self) -> None: ...

    def sessions_path(self) -> None: ...

    def list_notes(self, scope: str = "all") -> None: ...

    def clear_notes(self, scope: str = "project") -> None: ...

    def notes_path(self, scope: str = "all") -> None: ...

    def security_summary(self) -> str: ...

    def run_skill(self, name: str, args: str = "") -> Any: ...

    def rescan_skills(self) -> list: ...


class TextualUI:
    """Textual adapter: delegates UIControl operations to a ZXCodeApp."""

    def __init__(self, app: ZXCodeApp) -> None:
        self._app = app

    def notice(self, text: str) -> None:
        self._app.notice(text)

    def clear_chat(self) -> None:
        app = self._app
        app.session.clear()
        if hasattr(app, "skill_manager"):
            app.skill_manager.clear()
        if app.instruction_messages:
            app.session.inject_instructions(app.instruction_messages)
        app.session_id = None
        app.query_one("#messages", VerticalScroll).remove_children()
        app.set_status("就绪")

    def exit_app(self) -> None:
        self._app.exit()

    def set_model(self, name: str) -> None:
        app = self._app
        app.session.set_model(name)
        app.notice(f"已切换模型：{app.session.model}")
        app.set_status("就绪")

    def toggle_plan_mode(self) -> bool:
        app = self._app
        app.config = app.config.with_plan_only(not app.config.plan_only)
        if hasattr(app.agent, "config"):
            app.agent.config = app.config
        app.notice(
            "已进入 plan-only 模式：写类工具将被拦截。"
            if app.config.plan_only
            else "已退出 plan-only 模式。"
        )
        app.set_status("就绪")
        return app.config.plan_only

    def refresh_status(self) -> None:
        self._app.set_status("就绪")

    def token_estimate(self) -> int:
        return estimate_messages(self._app.session.messages)

    def status_summary(self) -> dict[str, Any]:
        app = self._app
        return {
            "model": app.session.model,
            "turns": app.session.turns,
            "plan_only": app.config.plan_only,
            "session_id": app.session_id,
            "token_estimate": self.token_estimate(),
            "sessions_dir": str(app.store.root),
            "user_notes": str(app.notes.user_notes_path()),
            "project_notes": str(app.notes.project_notes_path()),
        }

    def send_user_message(
        self, text: str, system_parts: Sequence[str] = ()
    ) -> None:
        app = self._app
        view = app.query_one("#messages", VerticalScroll)
        view.mount(Static(f"You:\n{text}", classes="message user", markup=False))
        assistant = Static("ZXCode:\n", classes="message assistant", markup=False)
        view.mount(assistant)
        app.request_started = monotonic()
        app.active_worker = app.generate(
            text, assistant, system_parts=tuple(system_parts)
        )

    def run_compact(self) -> None:
        app = self._app
        app.compact_worker = app.compact()

    def resume_session(self, session_id: str) -> None:
        app = self._app
        app.resume_worker = app.resume_session(session_id)

    def choose_session(self) -> None:
        self._app.choose_session()

    def delete_session(self, session_id: str) -> None:
        app = self._app
        app.sessions_worker = app.delete_session(session_id)

    def clear_sessions(self) -> None:
        app = self._app
        app.sessions_worker = app.clear_sessions()

    def list_sessions(self) -> None:
        app = self._app
        metas = app.store.list_meta()
        if not metas:
            app.notice("没有会话")
            return
        lines = [
            f"{meta.id} | {meta.title} | {meta.message_count} 条 | {meta.updated_at}"
            for meta in metas[:20]
        ]
        app.notice("会话列表：\n" + "\n".join(lines))

    def sessions_path(self) -> None:
        self._app.notice(f"会话目录：{self._app.store.root}")

    def list_notes(self, scope: str = "all") -> None:
        self._app.show_notes(scope)

    def clear_notes(self, scope: str = "project") -> None:
        app = self._app
        app.notes_worker = app.clear_notes(scope)

    def notes_path(self, scope: str = "all") -> None:
        self._app.show_notes_paths(scope)

    def security_summary(self) -> str:
        app = self._app
        policy = getattr(app, "security", None)
        mode = getattr(policy, "mode", "default")
        return (
            f"安全策略模式：{mode}\n"
            "策略文件：zxcode-security.toml（规则编辑请直接修改文件）"
        )

    def run_skill(self, name: str, args: str = "") -> Any:
        return self._app.run_skill(name, args)

    def rescan_skills(self) -> list:
        return self._app.rescan_skills()
