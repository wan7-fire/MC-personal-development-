"""Automatic note-taking with LLM-driven categorization.

Every N completed turns, at app exit, and immediately when a user message
carries a strong identity/preference/correction signal, the LLM reads the
current notes plus the recent conversation and rewrites the fixed category
sections.  User identity, preferences and corrections land in the user-level
notes file; project knowledge and reference material land in the project-level
notes file.  Deduplication is entirely the LLM's judgment.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .client import AssistantMessage, TextDelta
from .instructions import default_user_dir

NOTE_SECTIONS = ("用户身份", "用户偏好", "纠正反馈", "项目知识", "参考资料")
USER_SECTIONS = ("用户身份", "用户偏好", "纠正反馈")
PROJECT_SECTIONS = ("项目知识", "参考资料")
USER_FILE_NAME = "notes.md"
PROJECT_FILE_NAME = "notes.md"
DEFAULT_TURNS = 3
DEFAULT_TIMEOUT_SECONDS = 10.0
RECENT_MESSAGE_LIMIT = 30
SUMMARY_MARKER = "【一句话摘要】"

_MEMORABLE = re.compile(
    r"我是|我叫|我的(?:名字|身份|职业|背景|角色|工作|年龄|习惯)|"
    r"我(?:喜欢|偏好|偏爱|习惯|希望|要求|建议|更愿意)|"
    r"请记住|记住|用中文|用英文|不要|别(?:再)?|"
    r"\bI am\b|\bI'?m\b|\bmy (?:name|role|job|background)\b|"
    r"\bI prefer\b|\bI like\b|\bplease remember\b",
    re.IGNORECASE,
)


def _looks_memorable(text: str) -> bool:
    """True when the user message likely states identity/preference/correction."""
    return bool(_MEMORABLE.search(text))


@dataclass
class NotesConfig:
    interval_turns: int = DEFAULT_TURNS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    user_notes_path: Path | None = None
    project_notes_path: Path | None = None


def build_notes_prompt(
    user_notes: str, project_notes: str, conversation: Sequence[Mapping[str, Any]]
) -> str:
    lines = []
    for message in list(conversation)[-RECENT_MESSAGE_LIMIT:]:
        role = message.get("role", "?")
        content = message.get("content")
        if content is None and message.get("tool_calls"):
            content = str(message.get("tool_calls"))
        lines.append(f"--- {role} ---\n{content}")
    return (
        "你是 ZXCode 的笔记整理器。请阅读当前笔记和最近的对话，把它们整理成五类 "
        "Markdown 笔记。\n"
        "规则：\n"
        "- 只输出一个 Markdown 文档，包含五个小节，标题必须依次是："
        "## 用户身份、## 用户偏好、## 纠正反馈、## 项目知识、## 参考资料。\n"
        "- 用户身份：用户是谁——职业、背景、角色、学习阶段、语言习惯等；"
        "用户偏好：用户表达的偏好、习惯、措辞偏好；纠正反馈：用户纠正过你的地方；"
        "项目知识：与当前项目相关的技术栈、约定、结构；参考资料：涉及的文件、文档、"
        "网址等引用。\n"
        "- 对话中出现任何关于用户身份、偏好、习惯或纠正的新信息，必须立即记入对应"
        "小节，不要等待重复出现，也不要因为信息简短而遗漏；每条新事实单独成一条。\n"
        "- 去重：新内容与已有笔记重复时不要重复写，保留原有表述。\n"
        "- 已有小节的内容除非被新信息更新或明确取代，否则必须保留；原有内容为空且"
        "没有新内容时，该小节留空。\n"
        "- 文档最后单独一行输出【一句话摘要】加上对整个会话的一句话中文摘要。\n"
        "- 不要使用任何工具，不要输出其他内容。\n\n"
        "当前用户级笔记：\n"
        f"{user_notes or '（空）'}\n\n"
        "当前项目级笔记：\n"
        f"{project_notes or '（空）'}\n\n"
        "最近对话：\n"
        f"{lines and chr(10).join(lines) or '（无）'}"
    )


def split_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(buffer).strip()
            current = line[3:].strip()
            buffer = []
        elif current is not None:
            buffer.append(line)
    if current is not None:
        sections[current] = "\n".join(buffer).strip()
    return sections


def parse_notes_output(
    output: str, user_notes: str, project_notes: str
) -> tuple[str, str, str | None]:
    """Return (user_file_content, project_file_content, summary)."""
    summary: str | None = None
    if SUMMARY_MARKER in output:
        summary = output.split(SUMMARY_MARKER, 1)[1].strip() or None
        output = output.split(SUMMARY_MARKER, 1)[0]
    fresh = split_sections(output)
    existing_user = split_sections(user_notes)
    existing_project = split_sections(project_notes)

    def section(name: str, existing: dict[str, str]) -> str:
        return fresh.get(name) or existing.get(name, "")

    user_content = "\n\n".join(
        f"## {name}\n{section(name, existing_user)}" for name in USER_SECTIONS
    )
    project_content = "\n\n".join(
        f"## {name}\n{section(name, existing_project)}"
        for name in PROJECT_SECTIONS
    )
    return user_content, project_content, summary


class NotesManager:
    def __init__(
        self,
        root: Path,
        client: Any,
        user_dir: Path | None = None,
        config: NotesConfig | None = None,
    ) -> None:
        self.root = Path(root)
        self.client = client
        self.user_dir = Path(user_dir) if user_dir else default_user_dir()
        self.config = config or NotesConfig()
        self.turn_count = 0
        self._running = False

    def user_notes_path(self) -> Path:
        return self.config.user_notes_path or self.user_dir / USER_FILE_NAME

    def project_notes_path(self) -> Path:
        return self.config.project_notes_path or self.root / ".zxcode" / PROJECT_FILE_NAME

    def read_notes(self) -> tuple[str, str]:
        return (
            self._read(self.user_notes_path()),
            self._read(self.project_notes_path()),
        )

    def on_turn_completed(
        self, model: str, conversation: Sequence[Mapping[str, Any]] | None = None
    ) -> asyncio.Task | None:
        self.turn_count += 1
        memorable = False
        if conversation:
            for message in reversed(list(conversation)):
                if message.get("role") == "user":
                    memorable = _looks_memorable(
                        str(message.get("content") or "")
                    )
                    break
        due = memorable or self.turn_count % self.config.interval_turns == 0
        if not due or self._running:
            return None
        task = asyncio.create_task(
            self.update_notes(model, conversation=list(conversation or []))
        )
        return task
    async def update_notes(
        self, model: str, conversation: Sequence[Mapping[str, Any]] | None = None
    ) -> str | None:
        """Update notes; return the one-sentence session summary if produced."""
        if self._running:
            return None
        self._running = True
        try:
            user_notes, project_notes = self.read_notes()
            prompt = build_notes_prompt(
                user_notes, project_notes, list(conversation or [])
            )
            output = await self._call_model(prompt, model)
            if not output:
                return None
            user_content, project_content, summary = parse_notes_output(
                output, user_notes, project_notes
            )
            self._write_atomic(self.user_notes_path(), user_content)
            self._write_atomic(self.project_notes_path(), project_content)
            return summary
        except Exception:
            return None
        finally:
            self._running = False

    def clear_notes(self, scope: str = "project") -> None:
        if scope in ("user", "all"):
            self._write_atomic(self.user_notes_path(), "")
        if scope in ("project", "all"):
            self._write_atomic(self.project_notes_path(), "")

    async def _call_model(self, prompt: str, model: str) -> str:
        parts: list[str] = []
        async for event in self.client.stream_events(
            [{"role": "user", "content": prompt}], model, tools=()
        ):
            if isinstance(event, TextDelta):
                parts.append(event.text)
            elif isinstance(event, AssistantMessage):
                content = event.message.get("content")
                if content and not parts:
                    parts.append(str(content))
        return "".join(parts)

    @staticmethod
    def _read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return ""

    @staticmethod
    def _write_atomic(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
