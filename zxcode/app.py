"""Textual chat workbench."""

from __future__ import annotations

import asyncio
from pathlib import Path
from time import monotonic

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Label, Static, TextArea
from textual.worker import Worker

from .agent import AgentComplete, AgentLoop
from .cancel import CancelToken
from .client import ChatClient, Settings, friendly_error, friendly_error_name
from .config import AgentConfig
from .events import EventChannel, EventType
from .security import load_policy
from .session import ChatSession
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
    ) -> None:
        super().__init__()
        self.settings = settings
        self.client = client or ChatClient(settings)
        self.registry = registry or ToolRegistry(
            [ReadFile(), WriteFile(), EditFile(), Bash(), Glob(), Grep()]
        )
        self.cancel_token = CancelToken()
        self.config = AgentConfig(cancel_token=self.cancel_token)
        self.security = load_policy(Path.cwd(), self.config.security_mode)
        self.agent = agent or AgentLoop(
            self.client,
            self.registry,
            ToolExecutor(self.registry),
            config=self.config,
            context=ToolContext(Path.cwd(), self.confirm_tool, self.security),
        )
        self.session = ChatSession(settings.model)
        self.active_worker: Worker | None = None
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

    def on_mount(self) -> None:
        self.set_status("就绪")
        self.query_one("#input", TextArea).focus()

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
            self.notice("/help  /clear  /exit  /model <名称>  /plan")
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
        else:
            self.notice(f"未知命令：{name}")

    def notice(self, text: str) -> None:
        self.query_one("#messages", VerticalScroll).mount(
            Static(text, classes="message notice", markup=False)
        )

    def action_interrupt(self) -> None:
        if self.active_worker and self.active_worker.is_running:
            self.cancel_token.cancel()
            # A pending approval dialog blocks the tool that owns it, so a
            # cooperative flag alone would never reach the loop.
            if isinstance(self.screen, ConfirmScreen):
                self.screen.dismiss("deny")
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
                self.session.request_messages(user_text), self.session.model, channel
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

        self.session.commit_messages(user_text, completed.messages)
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
