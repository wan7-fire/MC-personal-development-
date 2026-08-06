"""Textual chat workbench."""

from __future__ import annotations

import asyncio
from pathlib import Path
from time import monotonic
from uuid import uuid4

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Label, ListItem, ListView, Static, TextArea
from textual.worker import Worker

from .agent import AgentComplete, AgentLoop
from .cancel import CancelToken
from .client import ChatClient, Settings, friendly_error, friendly_error_name
from .compress import CompressionConfig, CompressionFailure, CompressionManager
from .config import AgentConfig
from .events import EventChannel, EventType
from .instructions import load_instructions
from .mcp import ConfigError, McpConfig, McpManager
from .notes import NotesManager
from .recovery import recover_session
from .security import load_policy
from .session import ChatSession
from .storage import SessionMeta, SessionStore, default_sessions_dir
from .tools import (
    Bash,
    EditFile,
    Glob,
    Grep,
    ReadFile,
    ToolContext,
    ToolExecutor,
    ToolRegistry,
    WriteFile,
)


# confirm_tool returns one of these only when the user explicitly approved the
# action; any other result (deny, dialog dismissal, error) must cancel.
CONFIRMED_VALUES = ("once", "session", "permanent")


def _message_text(content: object) -> str:
    """Best-effort text rendering for persisted message content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(part for part in parts if part)
    return str(content or "")


class ConfirmScreen(ModalScreen[str]):
    CSS = """
    ConfirmScreen { align: center middle; }
    #confirm-dialog {
        grid-size: 2 4;
        grid-columns: 1fr 1fr;
        grid-rows: auto 1fr auto auto;
        width: 70%;
        max-width: 90;
        height: auto;
        max-height: 70%;
        padding: 1 2;
        border: round $warning;
        background: $surface;
    }
    #confirm-title, #confirm-detail { column-span: 2; }
    #confirm-detail { height: auto; max-height: 12; overflow-y: auto; }
    """

    def __init__(self, title: str, detail: str) -> None:
        super().__init__()
        self.title_text = title
        self.detail = detail

    def compose(self) -> ComposeResult:
        yield Grid(
            Label(self.title_text, id="confirm-title"),
            Static(self.detail, id="confirm-detail", markup=False),
            Button("本次允许", id="approve", variant="success"),
            Button("本会话允许", id="session", variant="primary"),
            Button("永久允许", id="permanent", variant="warning"),
            Button("拒绝", id="deny", variant="error"),
            id="confirm-dialog",
        )

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        self.dismiss({"approve": "once"}.get(str(event.button.id), str(event.button.id)))


class SessionPickerScreen(ModalScreen[str]):
    """Modal list of saved sessions; dismisses with the chosen session id."""

    CSS = """
    SessionPickerScreen { align: center middle; }
    #session-picker {
        width: 80%;
        max-width: 110;
        height: 70%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    #session-picker-title { height: auto; margin-bottom: 1; text-style: bold; }
    #session-list { height: 1fr; }
    """

    BINDINGS = [Binding("escape", "dismiss_picker", "取消")]

    def __init__(self, metas: list[SessionMeta]) -> None:
        super().__init__()
        self.metas = list(metas)

    def compose(self) -> ComposeResult:
        with Vertical(id="session-picker"):
            yield Label(
                "选择要恢复的会话（方向键选择，Enter 恢复，Esc 取消）",
                id="session-picker-title",
            )
            with ListView(id="session-list"):
                for meta in self.metas:
                    title = meta.title or "(无标题)"
                    yield ListItem(
                        Label(
                            f"{title}\n"
                            f"{meta.id} · {meta.message_count} 条 · {meta.updated_at}"
                        )
                    )

    def on_mount(self) -> None:
        self.query_one("#session-list", ListView).focus()

    @on(ListView.Selected, "#session-list")
    def pick(self, event: ListView.Selected) -> None:
        self.dismiss(self.metas[event.index].id)

    def action_dismiss_picker(self) -> None:
        self.dismiss(None)


def _resume_summary_lines(report) -> list[str]:
    lines = [
        f"已恢复会话 {report.session_id}：{report.restored_messages} 条消息"
    ]
    if report.skipped_lines:
        lines.append(f"跳过 {report.skipped_lines} 行坏数据")
    if report.dangling_truncated:
        lines.append(
            f"截断 {report.dangling_dropped} 条未完成工具调用消息"
        )
    if report.compressed:
        lines.append("已执行一次上下文压缩")
    if report.over_limit_dropped:
        lines.append(f"超限截断 {report.over_limit_dropped} 条旧消息")
    if report.idle_reminder:
        lines.append("已插入时间跨度提醒")
    return lines


class ZXCodeApp(App):
    TITLE = "ZXCode"
    CSS = """
    Screen { layout: vertical; }
    #status { height: 1; padding: 0 1; background: $primary-background; }
    #messages { height: 1fr; padding: 1 2; }
    .message { margin-bottom: 1; }
    .user { color: $accent; }
    .assistant { color: $text; }
    #input { height: 6; border: round $accent; }
    Footer { height: 1; }
    """
    BINDINGS = [
        Binding(
            "ctrl+enter,ctrl+s",
            "submit",
            "发送",
            key_display="Ctrl+Enter / Ctrl+S",
            priority=True,
        ),
        Binding("ctrl+c", "interrupt", "取消/退出", priority=True),
    ]

    def __init__(
        self,
        settings: Settings,
        client: ChatClient | None = None,
        *,
        agent: AgentLoop | None = None,
        registry: ToolRegistry | None = None,
        mcp_manager: McpManager | None = None,
        compressor: CompressionManager | None = None,
        store: SessionStore | None = None,
        notes: NotesManager | None = None,
        user_dir: Path | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.client = client or ChatClient(settings)
        self.registry = registry or ToolRegistry(
            [ReadFile(), WriteFile(), EditFile(), Bash(), Glob(), Grep()]
        )
        self.mcp_manager = mcp_manager
        self.mcp_config_error: str | None = None
        if self.mcp_manager is None:
            try:
                self.mcp_manager = McpManager.from_root(Path.cwd())
            except ConfigError as error:
                self.mcp_config_error = str(error)
                self.mcp_manager = McpManager(McpConfig())
        self._mcp_task: asyncio.Task | None = None
        self.cancel_token = CancelToken()
        self.config = AgentConfig(cancel_token=self.cancel_token)
        self.security = load_policy(Path.cwd(), self.config.security_mode)
        self.compressor = compressor or CompressionManager(
            Path.cwd(), CompressionConfig(), client=self.client
        )
        self.agent = agent or AgentLoop(
            self.client,
            self.registry,
            ToolExecutor(self.registry),
            config=self.config,
            context=ToolContext(Path.cwd(), self.confirm_tool, self.security),
            compressor=self.compressor,
        )
        self.session = ChatSession(settings.model)
        self.store = store or SessionStore(default_sessions_dir())
        self.session_id: str | None = None
        self.notes = notes or NotesManager(Path.cwd(), self.client, user_dir=user_dir)
        self.notes_task: asyncio.Task | None = None
        self.resume_worker = None
        self.sessions_worker = None
        self.notes_worker = None
        loaded_instructions = load_instructions(Path.cwd())
        self.instruction_messages = [
            loaded.to_message() for loaded in loaded_instructions
        ]
        self.instruction_issues = [
            issue.message
            for loaded in loaded_instructions
            for issue in loaded.issues
        ]
        if self.instruction_messages:
            self.session.inject_instructions(self.instruction_messages)
        self.active_worker: Worker | None = None
        self.compact_worker: Worker | None = None
        self.request_started = 0.0

    async def confirm_tool(self, title: str, detail: str) -> str:
        screen = ConfirmScreen(title, detail)
        try:
            return str(await self.push_screen_wait(screen))
        except asyncio.CancelledError:
            if self.screen is screen:
                screen.dismiss("deny")
            raise

    def compose(self) -> ComposeResult:
        yield Static(id="status")
        yield VerticalScroll(id="messages")
        yield TextArea(id="input", show_line_numbers=False)
        yield Footer()

    async def on_mount(self) -> None:
        self.set_status("就绪")
        self.query_one("#input", TextArea).focus()
        for issue in self.instruction_issues:
            self.notice(issue)
        if self.mcp_config_error:
            self.notice(f"MCP 配置错误：{self.mcp_config_error}")
        if self.mcp_manager.config.servers:
            self._mcp_task = asyncio.create_task(self._start_mcp())

    async def _start_mcp(self) -> None:
        report = await self.mcp_manager.register_all(self.registry)
        for item in report:
            if not item["ok"]:
                self.notice(f"MCP server {item['server']} 连接失败：{item['error']}")

    async def on_unmount(self) -> None:
        task = self._mcp_task
        self._mcp_task = None
        if task is not None and not task.done():
            task.cancel()
        await self.mcp_manager.close_all()
        if self.notes_task is not None and not self.notes_task.done():
            self.notes_task.cancel()
        if self.session_id is not None and self.session.messages:
            try:
                summary = await asyncio.wait_for(
                    self.notes.update_notes(
                        self.session.model, conversation=self.session.messages
                    ),
                    timeout=self.notes.config.timeout_seconds,
                )
                if summary:
                    self.store.update_summary(self.session_id, summary)
            except Exception:
                pass

    def set_status(self, state: str) -> None:
        elapsed = monotonic() - self.request_started if self.request_started else 0.0
        plan = "  |  plan-only" if self.config.plan_only else ""
        self.query_one("#status", Static).update(
            f"模型: {self.session.model}  |  {self.session.turns} 轮  |  {state}  |  {elapsed:.1f}s{plan}"
        )

    def action_submit(self) -> None:
        input_box = self.query_one("#input", TextArea)
        user_text = input_box.text
        if not user_text.strip() or (self.active_worker and self.active_worker.is_running):
            return

        input_box.load_text("")
        command = user_text.strip()
        if command.startswith("/"):
            self.handle_command(command)
            return

        messages = self.query_one("#messages", VerticalScroll)
        messages.mount(Static(f"You:\n{user_text}", classes="message user", markup=False))
        assistant = Static("ZXCode:\n", classes="message assistant", markup=False)
        messages.mount(assistant)
        self.request_started = monotonic()
        self.active_worker = self.generate(user_text, assistant)

    def handle_command(self, command: str) -> None:
        name, _, argument = command.partition(" ")
        if name == "/help":
            self.notice(
                "/help  /clear  /exit  /model <名称>  /plan  /compact"
                "  /resume [ID]  /sessions  /notes"
            )
        elif name == "/plan":
            self.config = self.config.with_plan_only(not self.config.plan_only)
            if hasattr(self.agent, "config"):
                self.agent.config = self.config
            self.notice(
                "已进入 plan-only 模式：写类工具将被拦截。"
                if self.config.plan_only
                else "已退出 plan-only 模式。"
            )
            self.set_status("就绪")
        elif name == "/clear":
            self.session.clear()
            if self.instruction_messages:
                self.session.inject_instructions(self.instruction_messages)
            self.session_id = None
            self.query_one("#messages", VerticalScroll).remove_children()
            self.set_status("就绪")
        elif name == "/exit":
            self.exit()
        elif name == "/model":
            if argument.strip():
                self.session.set_model(argument.strip())
                self.notice(f"已切换模型：{self.session.model}")
                self.set_status("就绪")
            else:
                self.notice("用法：/model <名称>")
        elif name == "/compact":
            self.compact_worker = self.compact()
        elif name == "/resume":
            if argument.strip():
                self.resume_worker = self.resume_session(argument.strip())
            else:
                self.choose_session()
        elif name == "/sessions":
            self.handle_sessions(argument.strip())
        elif name == "/notes":
            self.handle_notes(argument.strip())
        else:
            self.notice(f"未知命令：{name}")

    def handle_sessions(self, argument: str) -> None:
        if not argument:
            metas = self.store.list_meta()
            if not metas:
                self.notice("没有会话")
                return
            lines = [
                f"{meta.id} | {meta.title} | {meta.message_count} 条 | {meta.updated_at}"
                for meta in metas[:20]
            ]
            self.notice("会话列表：\n" + "\n".join(lines))
            return
        sub, _, rest = argument.partition(" ")
        if sub == "delete":
            if rest.strip():
                self.sessions_worker = self.delete_session(rest.strip())
            else:
                self.notice("用法：/sessions delete <会话ID>")
        elif sub == "clear":
            self.sessions_worker = self.clear_sessions()
        elif sub == "path":
            self.notice(f"会话目录：{self.store.root}")
        else:
            self.notice(f"未知子命令：{sub}")

    def choose_session(self) -> None:
        metas = self.store.list_meta()
        if not metas:
            self.notice("没有会话")
            return
        self.push_screen(
            SessionPickerScreen(metas), callback=self._on_session_picked
        )

    def _on_session_picked(self, session_id: str | None) -> None:
        if session_id:
            self.resume_worker = self.resume_session(session_id)

    def handle_notes(self, argument: str) -> None:
        sub, _, scope = argument.partition(" ")
        if not sub:
            self.show_notes("all")
        elif sub == "view":
            self.show_notes(scope or "all")
        elif sub == "clear":
            target = scope or "project"
            if target in ("project", "user", "all"):
                self.notes_worker = self.clear_notes(target)
            else:
                self.notice("用法：/notes clear [project|user|all]")
        elif sub == "edit":
            self.show_notes_paths(scope or "project")
        elif sub == "path":
            self.show_notes_paths(scope or "all")
        else:
            self.notice("用法：/notes [view|clear|edit|path] [project|user|all]")

    def show_notes(self, scope: str) -> None:
        user_path = self.notes.user_notes_path()
        project_path = self.notes.project_notes_path()
        user_content, project_content = self.notes.read_notes()
        parts = []
        if scope in ("user", "all"):
            parts.append(f"用户级笔记（{user_path}）：\n{user_content or '（空）'}")
        if scope in ("project", "all"):
            parts.append(
                f"项目级笔记（{project_path}）：\n{project_content or '（空）'}"
            )
        if parts:
            self.notice("\n\n".join(parts))
        else:
            self.notice("未知作用域")

    def show_notes_paths(self, scope: str) -> None:
        user_path = self.notes.user_notes_path()
        project_path = self.notes.project_notes_path()
        if scope == "all":
            self.notice(f"用户级：{user_path}\n项目级：{project_path}")
        else:
            path = user_path if scope == "user" else project_path
            self.notice(f"可编辑路径：{path}")

    @work(exclusive=False)
    async def resume_session(self, session_id: str) -> None:
        if not self.store.jsonl_path(session_id).exists():
            self.notice(f"未找到会话：{session_id}")
            return
        messages, report = await recover_session(
            self.store,
            session_id,
            compressor=self.compressor,
            model=self.session.model,
        )
        self.session.messages = messages
        self.session_id = session_id
        messages_view = self.query_one("#messages", VerticalScroll)
        messages_view.remove_children()
        for line in _resume_summary_lines(report):
            self.notice(line)
        self._render_history(messages)
        messages_view.scroll_end(animate=False)
        self.set_status("就绪")

    @work(exclusive=False)
    async def delete_session(self, session_id: str) -> None:
        if not (
            self.store.jsonl_path(session_id).exists()
            or self.store.meta_path(session_id).exists()
        ):
            self.notice(f"未找到会话：{session_id}")
            return
        result = await self.confirm_tool(
            "删除会话", f"确定删除会话 {session_id}？该操作不可恢复。"
        )
        if result not in CONFIRMED_VALUES:
            self.notice("已取消删除")
            return
        self.store.delete_session(session_id)
        self.notice(f"已删除会话：{session_id}")

    @work(exclusive=False)
    async def clear_sessions(self) -> None:
        count = len(self.store.list_meta())
        result = await self.confirm_tool(
            "清空会话", f"确定清空全部 {count} 个会话？"
        )
        if result not in CONFIRMED_VALUES:
            self.notice("已取消清空")
            return
        removed = self.store.clear_all()
        self.notice(f"已清空 {removed} 个会话文件")

    @work(exclusive=False)
    async def clear_notes(self, scope: str) -> None:
        result = await self.confirm_tool("清空笔记", f"确定清空{scope}笔记？")
        if result not in CONFIRMED_VALUES:
            self.notice("已取消清空")
            return
        self.notes.clear_notes(scope)
        self.notice(f"已清空{scope}笔记")

    def notice(self, text: str) -> None:
        self.query_one("#messages", VerticalScroll).mount(
            Static(text, classes="message notice", markup=False)
        )

    def _render_history(self, messages) -> None:
        view = self.query_one("#messages", VerticalScroll)
        for message in messages:
            role = message.get("role")
            if role == "user":
                view.mount(
                    Static(
                        f"You:\n{_message_text(message.get('content'))}",
                        classes="message user",
                        markup=False,
                    )
                )
            elif role == "assistant":
                view.mount(
                    Static(
                        f"ZXCode:\n{_message_text(message.get('content'))}",
                        classes="message assistant",
                        markup=False,
                    )
                )

    def action_interrupt(self) -> None:
        if self.active_worker and self.active_worker.is_running:
            self.cancel_token.cancel()
            # A pending approval dialog blocks the tool that owns it, so a
            # cooperative flag alone would never reach the loop.
            if isinstance(self.screen, ConfirmScreen):
                self.screen.dismiss("deny")
            elif isinstance(self.screen, SessionPickerScreen):
                self.screen.dismiss(None)
        else:
            self.exit()

    @work(exclusive=True)
    async def generate(self, user_text: str, assistant: Static) -> None:
        self.cancel_token.reset()
        self.set_status("连接中")
        answer = ""
        status = "就绪"
        channel = EventChannel()
        runner = asyncio.create_task(
            self.agent.run(
                self.session.prepare_request(user_text, compressor=self.compressor),
                self.session.model,
                channel,
            )
        )

        try:
            async for event in channel:
                answer, status = self._render_event(event, assistant, answer, status)
            completed: AgentComplete = await runner
        except asyncio.CancelledError:
            runner.cancel()
            self._abandon(user_text, assistant, answer, "已取消")
            raise
        except Exception as error:
            runner.cancel()
            self._abandon(user_text, assistant, answer, friendly_error(error))
            return

        reason = completed.termination_reason
        if reason in ("cancelled", "error") and not completed.messages:
            self._abandon(user_text, assistant, answer, status)
            return

        if completed.final_history is not None:
            self.session.rebuild_from_history(completed.final_history)
        else:
            self.session.commit_messages(user_text, completed.messages)
        self._persist_turn(user_text, completed)
        if completed.blocked_calls:
            blocked = "\n".join(
                f"  - {item['tool_name']} {item['arguments']}: {item.get('reason', '')}"
                for item in completed.blocked_calls
            )
            if all(
                "plan-only" in str(item.get("reason", ""))
                for item in completed.blocked_calls
            ):
                self.notice(f"plan-only 已拦截以下写操作：\n{blocked}")
            else:
                self.notice(f"工具调用已被安全策略拦截：\n{blocked}")
        self.request_started = 0.0
        self.set_status(status)

    def _persist_turn(
        self, user_text: str, completed: AgentComplete
    ) -> None:
        if not completed.messages:
            return
        if self.session_id is None:
            self.session_id = uuid4().hex
        new_messages = [
            {"role": "user", "content": user_text},
            *list(completed.messages),
        ]
        try:
            self.store.append_messages(
                self.session_id, new_messages, self.session.model
            )
        except OSError:
            self.notice("会话存档写入失败")
        task = self.notes.on_turn_completed(
            self.session.model, conversation=self.session.messages
        )
        if task is not None:
            self.notes_task = task

    @work(exclusive=True)
    async def compact(self) -> None:
        """Manually run layer 1 + layer 2; the breaker never blocks manual use."""
        if self.active_worker and self.active_worker.is_running:
            self.notice("正在生成，请稍后再执行 /compact")
            return
        if not self.session.messages:
            self.notice("没有可压缩的内容")
            return
        self.set_status("压缩中")
        try:
            messages, outcome = await self.compressor.manual_compress(
                self.session.messages, self.session.model
            )
        except CompressionFailure as error:
            self.notice(f"压缩失败：{error.message}")
            self.set_status("就绪")
            return
        if outcome.changed:
            self.session.messages = messages
            self.notice(f"压缩完成：{outcome.removed_messages} 条旧消息已替换为摘要。")
        else:
            self.notice("没有可压缩的内容")
        self.set_status("就绪")

    def _abandon(
        self, user_text: str, assistant: Static, answer: str, status: str
    ) -> None:
        """Nothing usable came back: restore the input and commit nothing."""
        self.query_one("#input", TextArea).load_text(user_text)
        suffix = "\n[已取消]" if status == "已取消" else "\n[失败]"
        assistant.update(f"ZXCode:\n{answer}{suffix}")
        self.request_started = 0.0
        self.set_status(status)

    def _render_event(
        self, event, assistant: Static, answer: str, status: str
    ) -> tuple[str, str]:
        messages = self.query_one("#messages", VerticalScroll)
        if event.type == EventType.TEXT:
            answer += event.data.get("content", "")
            assistant.update(f"ZXCode:\n{answer}")
            self.set_status("生成中")
            messages.scroll_end(animate=False)
        elif event.type == EventType.THINKING:
            self.set_status("思考中")
        elif event.type == EventType.TOOL_CALL_START:
            self.notice(f"⚙ {event.data['tool_name']} …")
            messages.scroll_end(animate=False)
        elif event.type == EventType.TOOL_CALL_END:
            mark = {"success": "✓", "timeout": "⏱", "error": "✗"}[event.data["status"]]
            self.notice(
                f"{mark} {event.data['tool_name']}  {event.data['duration_ms']}ms"
            )
            messages.scroll_end(animate=False)
        elif event.type == EventType.ERROR:
            assistant.update(f"ZXCode:\n{answer}\n[失败]")
            status = friendly_error_name(event.data.get("error_type", ""))
        elif event.type == EventType.CANCELLED:
            assistant.update(f"ZXCode:\n{answer}\n[已取消]")
            status = "已取消"
        return answer, status
