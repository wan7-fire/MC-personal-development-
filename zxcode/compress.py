"""Two-layer context compression for long sessions.

Layer 1 spools oversized tool results to disk so the conversation keeps only a
preview and a read path.  Layer 2 summarizes the oldest turns when the
estimated history approaches the context window, and a circuit breaker stops
automatic retries after repeated summary failures.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .client import AssistantMessage, TextDelta


BOUNDARY_MESSAGE = (
    "[上下文已压缩] 较早的历史已被结构化为摘要。如需文件、代码或错误信息的具体细节，"
    "请使用 ReadFile / Grep 重新读取对应文件或输出，不要根据摘要猜测内容。"
)

BEGIN_ANALYSIS_DRAFT = "【分析草稿】"
END_ANALYSIS_DRAFT = "【草稿结束】"
BEGIN_SUMMARY = "【正式摘要】"
END_SUMMARY = "【摘要结束】"

SPOOL_PREFIX = ".zxcode/spool"

SUMMARY_SECTIONS = (
    "主要请求",
    "关键概念",
    "文件代码",
    "错误修复",
    "解决过程",
    "用户原话",
    "待办",
    "当前工作",
    "下一步",
)

_FORBID_TOOLS = "本次调用禁止使用任何工具：只能阅读给定的历史文本并输出压缩结果。"
_SPOOL_LINK = re.compile(r"\.zxcode[/\\]spool[/\\]([0-9a-f]{64})\.txt")
_TOOL_CALL = re.compile(
    r'"tool_calls"\s*:|\{"function"\s*:|"type"\s*:\s*"function"'
)


@dataclass(frozen=True)
class CompressionConfig:
    """Tunable limits.  Values are documented in docs/compress/checklist.md."""

    context_window: int = 131072
    trigger_ratio: float = 0.8
    target_ratio: float = 0.4
    single_result_limit: int = 8192
    batch_total_limit: int = 32768
    summary_model: str | None = None
    breaker_limit: int = 2
    spool_dir: str = ".zxcode/spool"


class CompressionFailure(Exception):
    """Typed summary failure; ``kind`` is one of the documented reasons."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass
class CompressionOutcome:
    changed: bool = False
    removed_messages: int = 0
    error: str | None = None


def estimate_chars(text: str) -> int:
    return len(text)


def estimate_tokens(text: str) -> int:
    return len(text) // 4


def estimate_messages(messages: list[dict[str, Any]]) -> int:
    """Rough token estimate for a message list (content chars // 4)."""
    total = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            total += len(content)
    return total // 4


class CircuitBreaker:
    """Stops automatic compression after repeated failures; one success resets."""

    def __init__(self, limit: int = 2) -> None:
        self.limit = max(1, int(limit))
        self._failures = 0

    def allowed(self) -> bool:
        return self._failures < self.limit

    @property
    def tripped(self) -> bool:
        return not self.allowed()

    def record_failure(self) -> None:
        self._failures += 1

    def record_success(self) -> None:
        self._failures = 0


def _preview(content: str, name: str) -> str:
    return (
        f"[工具结果已溢出: {len(content)} 字符，完整内容见 {SPOOL_PREFIX}/{name}，"
        "可用 ReadFile 读取]\n"
        + content[:200]
    )


class CompressionManager:
    """Owns spooling, summarization, the breaker and the spool directory."""

    def __init__(
        self,
        root: Path | str,
        config: CompressionConfig | None = None,
        client: Any | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.config = config or CompressionConfig()
        self.client = client
        self.breaker = CircuitBreaker(self.config.breaker_limit)

    # ── layer 1: spool oversized tool results ───────────────────────────

    def spool_tool_message(
        self, message: dict[str, Any], *, force: bool = False
    ) -> tuple[dict[str, Any], Path | None]:
        """Write one oversized tool result to disk; return (new_message, path)."""
        if message.get("role") != "tool":
            return message, None
        content = message.get("content")
        if not isinstance(content, str):
            return message, None
        if len(content) <= self.config.single_result_limit and not force:
            return message, None
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        target = self.root / self.config.spool_dir / f"{digest}.txt"
        preview = _preview(content, target.name)
        if len(preview) >= len(content):
            return message, None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError:
            return message, None
        spooled = {**message, "content": preview}
        return spooled, target

    def spool_batch(
        self, messages: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[Path]]:
        """Apply the single-result rule, then the batch total rule."""
        result: list[dict[str, Any]] = []
        spooled_files: list[Path] = []
        already_spooled: set[int] = set()
        for index, message in enumerate(messages):
            new_message, path = self.spool_tool_message(message)
            result.append(new_message)
            if path is not None:
                spooled_files.append(path)
                already_spooled.add(index)

        tool_indices = [
            index
            for index, message in enumerate(result)
            if message.get("role") == "tool" and index not in already_spooled
        ]
        total = sum(
            len(str(result[index].get("content") or "")) for index in tool_indices
        )
        for index in sorted(
            tool_indices,
            key=lambda i: len(str(result[i].get("content") or "")),
            reverse=True,
        ):
            if total <= self.config.batch_total_limit:
                break
            new_message, path = self.spool_tool_message(result[index], force=True)
            if new_message is result[index]:
                break
            total -= len(str(result[index].get("content") or ""))
            result[index] = new_message
            total += len(str(new_message.get("content") or ""))
            if path is not None:
                spooled_files.append(path)
        return result, spooled_files

    def recheck(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Idempotent layer-1 pass over stored history, grouped by user turns."""
        rebuilt: list[dict[str, Any]] = []
        turn: list[dict[str, Any]] = []
        for message in messages:
            if message.get("role") == "user" and turn:
                rebuilt.extend(self._spool_turn(turn))
                turn = []
            turn.append(dict(message))
        if turn:
            rebuilt.extend(self._spool_turn(turn))
        return rebuilt

    def _spool_turn(self, turn: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if turn and turn[0].get("role") == "user":
            spooled, _ = self.spool_batch(turn[1:])
            return [turn[0], *spooled]
        return turn

    # ── layer 2: structured summarization ───────────────────────────────

    def build_summary_prompt(self, block: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for message in block:
            role = message.get("role", "?")
            content = message.get("content")
            if content is None and message.get("tool_calls"):
                content = str(message.get("tool_calls"))
            lines.append(f"--- {role} ---\n{content}")
        sections = "\n".join(f"- {name}" for name in SUMMARY_SECTIONS)
        return (
            "你正在压缩一段旧对话历史，为当前任务保留必要上下文。\n"
            f"{_FORBID_TOOLS}\n\n"
            "步骤：\n"
            f"1. 先输出一段 {BEGIN_ANALYSIS_DRAFT}……{END_ANALYSIS_DRAFT} 的分析草稿"
            "（不会保留）：整理要点、关键文件名、报错信息和未完成待办。\n"
            f"2. 草稿结束后输出 {BEGIN_SUMMARY}……{END_SUMMARY} 的正式摘要。\n\n"
            "正式摘要必须按以下固定栏目组织：\n"
            f"{sections}\n\n"
            "规则：\n"
            "- 「用户原话」栏目必须逐字摘录被压缩用户消息的原文，不得改写。\n"
            "- 其余栏目用简洁中文概括，代码只保留关键片段。\n"
            f"- 正式摘要以「{BEGIN_SUMMARY}」开头、以「{END_SUMMARY}」结尾。\n"
            f"- 再次强调：{_FORBID_TOOLS}\n\n"
            "以下是待压缩的历史：\n\n"
            + "\n\n".join(lines)
        )

    def parse_summary(self, text: str) -> str:
        start = text.find(BEGIN_SUMMARY)
        end = text.rfind(END_SUMMARY)
        if start < 0 or end < 0 or end <= start:
            raise CompressionFailure("parse_error", "摘要缺少开始或结束标记")
        summary = text[start + len(BEGIN_SUMMARY) : end].strip()
        if not summary:
            raise CompressionFailure("parse_error", "摘要是空的")
        if _TOOL_CALL.search(summary):
            raise CompressionFailure("tool_call", "摘要包含工具调用")
        return summary

    async def summarize_block(self, block: list[dict[str, Any]], model: str) -> str:
        if self.client is None:
            raise CompressionFailure("model_error", "未配置摘要模型客户端")
        prompt = self.build_summary_prompt(block)
        summary_model = self.config.summary_model or model
        parts: list[str] = []
        assistant: dict[str, Any] | None = None
        try:
            async for event in self.client.stream_events(
                [{"role": "user", "content": prompt}],
                summary_model,
                tools=(),
            ):
                if isinstance(event, TextDelta):
                    parts.append(event.text)
                elif isinstance(event, AssistantMessage):
                    assistant = event.message
        except CompressionFailure:
            raise
        except Exception as error:
            raise CompressionFailure("model_error", str(error)) from error
        if assistant and assistant.get("tool_calls"):
            raise CompressionFailure("tool_call", "摘要模型尝试调用工具")
        content = "".join(parts)
        if not content and isinstance(assistant, dict):
            content = str(assistant.get("content") or "")
        return self.parse_summary(content)

    async def compress_history(
        self, messages: list[dict[str, Any]], model: str
    ) -> tuple[list[dict[str, Any]], CompressionOutcome]:
        """Replace the oldest turns with one summary plus the boundary message."""
        history = [dict(message) for message in messages]
        first = 0
        while first < len(history) and history[first].get("role") == "system":
            first += 1
        user_indices = [
            index
            for index in range(first, len(history))
            if history[index].get("role") == "user"
        ]
        if len(user_indices) < 2:
            return history, CompressionOutcome(changed=False)

        target = int(self.config.context_window * self.config.target_ratio)
        if estimate_messages(history) <= target:
            return history, CompressionOutcome(changed=False)

        cut = 0
        while (len(user_indices) - cut) > 1:
            next_cut = cut + 1
            remaining = (
                history[user_indices[next_cut] :]
                if next_cut < len(user_indices)
                else []
            )
            if estimate_messages(history[:first] + remaining) <= target:
                cut = next_cut
                break
            cut = next_cut
        if cut == 0:
            return history, CompressionOutcome(changed=False)

        removed = history[first : user_indices[cut]]
        summary_text = await self.summarize_block(removed, model)
        summary_message = {"role": "user", "content": summary_text}
        boundary_message = {"role": "user", "content": BOUNDARY_MESSAGE}
        final = (
            history[:first]
            + [summary_message, boundary_message]
            + history[user_indices[cut] :]
        )
        if estimate_messages(final) > target:
            raise CompressionFailure("still_over_limit", "压缩后仍超过目标体积")
        self._cleanup_spooled(removed)
        return final, CompressionOutcome(changed=True, removed_messages=len(removed))

    def _cleanup_spooled(self, block: list[dict[str, Any]]) -> None:
        for message in block:
            content = message.get("content")
            if not isinstance(content, str):
                continue
            for name in _SPOOL_LINK.findall(content):
                try:
                    (self.root / self.config.spool_dir / f"{name}.txt").unlink(
                        missing_ok=True
                    )
                except OSError:
                    pass

    # ── combined pipeline ───────────────────────────────────────────────

    async def prepare(
        self, history: list[dict[str, Any]], model: str
    ) -> list[dict[str, Any]]:
        """Run layer 1, then layer 2 if the history approaches the window."""
        history = self.recheck(history)
        if estimate_messages(history) < int(
            self.config.context_window * self.config.trigger_ratio
        ):
            return history
        if not self.breaker.allowed():
            return history
        try:
            new_history, outcome = await self.compress_history(history, model)
        except CompressionFailure:
            self.breaker.record_failure()
            return history
        if outcome.changed:
            self.breaker.record_success()
            return new_history
        return history

    async def manual_compress(
        self, messages: list[dict[str, Any]], model: str
    ) -> tuple[list[dict[str, Any]], CompressionOutcome]:
        """Layer 1 + layer 2 for the /compact command, bypassing the breaker."""
        messages = self.recheck(messages)
        new_messages, outcome = await self.compress_history(messages, model)
        if outcome.changed:
            self.breaker.record_success()
        return new_messages, outcome
