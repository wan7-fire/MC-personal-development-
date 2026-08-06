"""Append-only JSONL session archive with sidecar meta files."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SESSION_SUFFIX = ".jsonl"
META_SUFFIX = ".meta.json"
TITLE_LIMIT = 60


def default_sessions_dir() -> Path:
    override = os.environ.get("ZXCODE_SESSIONS_DIR")
    if override:
        return Path(override)
    return Path.home() / ".zxcode" / "sessions"


def utc_now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class SessionMeta:
    id: str
    title: str
    summary: str
    message_count: int
    created_at: str
    updated_at: str
    model: str
    meta_broken: bool = False


class SessionStore:
    """Append-only JSONL archive plus a small meta file per session.

    Appending is O(1); a crash can only leave the final line incomplete.
    Listing reads meta files only and never scans the full logs.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def ensure_dir(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def jsonl_path(self, session_id: str) -> Path:
        return self.root / f"{session_id}{SESSION_SUFFIX}"

    def meta_path(self, session_id: str) -> Path:
        return self.root / f"{session_id}{META_SUFFIX}"

    def append_messages(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        model: str,
        *,
        now: str | None = None,
    ) -> None:
        """Append each message as one JSON line and refresh the meta file."""
        path = self.jsonl_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        meta = self.read_meta(session_id)
        if meta is None or meta.meta_broken:
            count_before = self.count_lines(path)
        else:
            count_before = meta.message_count
        with path.open("a", encoding="utf-8") as handle:
            for message in messages:
                handle.write(json.dumps(message, ensure_ascii=False) + "\n")
            handle.flush()
        stamp = now or utc_now_iso()
        title = meta.title if meta and not meta.meta_broken else ""
        summary = meta.summary if meta and not meta.meta_broken else ""
        created = meta.created_at if meta and meta.created_at else stamp
        if not title:
            for message in messages:
                if message.get("role") == "user":
                    title = str(message.get("content") or "")[:TITLE_LIMIT]
                    break
        new_meta = SessionMeta(
            id=session_id,
            title=title,
            summary=summary,
            message_count=count_before + len(messages),
            created_at=created,
            updated_at=stamp,
            model=model,
        )
        self._write_meta(new_meta)

    def update_summary(
        self, session_id: str, summary: str, *, now: str | None = None
    ) -> None:
        meta = self.read_meta(session_id)
        if meta is None or meta.meta_broken:
            return
        self._write_meta(
            SessionMeta(
                id=meta.id,
                title=meta.title,
                summary=summary,
                message_count=meta.message_count,
                created_at=meta.created_at,
                updated_at=now or utc_now_iso(),
                model=meta.model,
            )
        )

    def read_meta(self, session_id: str) -> SessionMeta | None:
        path = self.meta_path(session_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return SessionMeta(
                id=str(data["id"]),
                title=str(data.get("title", "")),
                summary=str(data.get("summary", "")),
                message_count=int(data.get("message_count", 0)),
                created_at=str(data.get("created_at", "")),
                updated_at=str(data.get("updated_at", "")),
                model=str(data.get("model", "")),
            )
        except (OSError, ValueError, KeyError, TypeError):
            return SessionMeta(
                id=session_id,
                title="",
                summary="",
                message_count=0,
                created_at="",
                updated_at="",
                model="",
                meta_broken=True,
            )

    def list_meta(self) -> list[SessionMeta]:
        metas: list[SessionMeta] = []
        seen: set[str] = set()
        for path in sorted(self.root.glob(f"*{META_SUFFIX}")):
            session_id = path.name[: -len(META_SUFFIX)]
            seen.add(session_id)
            meta = self.read_meta(session_id)
            if meta is not None and not meta.meta_broken:
                metas.append(meta)
                continue
            fallback = self._fallback_meta(session_id)
            if fallback is not None:
                metas.append(fallback)
        for path in sorted(self.root.glob(f"*{SESSION_SUFFIX}")):
            session_id = path.name[: -len(SESSION_SUFFIX)]
            if session_id in seen:
                continue
            fallback = self._fallback_meta(session_id)
            if fallback is not None:
                metas.append(fallback)
        metas.sort(key=lambda item: item.updated_at, reverse=True)
        return metas

    def read_raw_lines(self, session_id: str) -> tuple[list[dict[str, Any]], int]:
        """Parse the archive; return (messages, skipped_line_count)."""
        path = self.jsonl_path(session_id)
        messages: list[dict[str, Any]] = []
        skipped = 0
        if not path.exists():
            return messages, skipped
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    skipped += 1
                    continue
                if isinstance(message, dict):
                    messages.append(message)
                else:
                    skipped += 1
        return messages, skipped

    def delete_session(self, session_id: str) -> None:
        for path in (self.jsonl_path(session_id), self.meta_path(session_id)):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def clear_all(self) -> int:
        removed = 0
        for suffix in (SESSION_SUFFIX, META_SUFFIX):
            for path in self.root.glob(f"*{suffix}"):
                try:
                    path.unlink(missing_ok=True)
                    removed += 1
                except OSError:
                    pass
        return removed

    def count_lines(self, path: Path) -> int:
        if not path.exists():
            return 0
        count = 0
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    count += 1
        return count

    def _write_meta(self, meta: SessionMeta) -> None:
        target = self.meta_path(meta.id)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(
            json.dumps(asdict(meta), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, target)

    def _fallback_meta(self, session_id: str) -> SessionMeta | None:
        path = self.jsonl_path(session_id)
        if not path.exists():
            return None
        title = ""
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    message = json.loads(line.strip())
                except ValueError:
                    continue
                if isinstance(message, dict) and message.get("role") == "user":
                    title = str(message.get("content") or "")[:TITLE_LIMIT]
                    break
        stamp = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        return SessionMeta(
            id=session_id,
            title=title,
            summary="",
            message_count=self.count_lines(path),
            created_at=stamp.isoformat(timespec="seconds"),
            updated_at=stamp.isoformat(timespec="seconds"),
            model="",
            meta_broken=True,
        )
