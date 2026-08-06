"""Project/user instruction files with bounded @include expansion.

Instruction files are plain Markdown read at session start and injected as a
single independent message at the head of the conversation.  Lines starting
with ``@include`` pull in other Markdown files relative to the including
file's directory; expansion is bounded by depth and containment so content can
never escape the owning root (project or user).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

PROJECT_FILE_NAME = "ZXCODE.md"
USER_FILE_NAME = "AGENTS.md"
MAX_INCLUDE_DEPTH = 3
MAX_CONTENT_CHARS = 32_000
INCLUDE_PREFIX = "@include "

PROJECT_SCOPE = "project"
USER_SCOPE = "user"


def default_user_dir() -> Path:
    return Path.home() / ".zxcode"


@dataclass(frozen=True)
class InstructionIssue:
    message: str


@dataclass
class LoadedInstruction:
    scope: str
    source: Path
    content: str
    truncated: bool = False
    issues: list[InstructionIssue] = field(default_factory=list)

    def to_message(self) -> dict[str, str]:
        label = "项目指令" if self.scope == PROJECT_SCOPE else "用户指令"
        return {
            "role": "system",
            "content": (
                f"[{label}文件：{self.source.name}] 本文件是{label}的权威来源，"
                "涵盖技术栈、编码规范、项目约定与注意事项。涉及本文件已覆盖的内容时，"
                "直接依据本文件回答，无需先读取代码；本文件未覆盖的细节再读取代码。\n\n"
                f"{self.content}"
            ),
        }


def load_instructions(
    project_root: Path,
    user_dir: Path | None = None,
    *,
    project_name: str = PROJECT_FILE_NAME,
    user_name: str = USER_FILE_NAME,
    max_depth: int = MAX_INCLUDE_DEPTH,
    max_content_chars: int = MAX_CONTENT_CHARS,
) -> list[LoadedInstruction]:
    """Load project then user instructions, each @include-expanded.

    Project-level content comes first so the model encounters the higher
    priority scope before the user scope.  Missing or unreadable files are
    skipped without raising.
    """
    user_dir = user_dir or default_user_dir()
    loaded: list[LoadedInstruction] = []
    for scope, root, name in (
        (PROJECT_SCOPE, Path(project_root), project_name),
        (USER_SCOPE, user_dir, user_name),
    ):
        root = root.resolve()
        source = root / name
        if not source.exists():
            continue
        issues: list[InstructionIssue] = []
        content = _expand(
            source,
            root,
            depth=1,
            stack={source.resolve()},
            issues=issues,
            max_depth=max_depth,
        )
        truncated = False
        if len(content) > max_content_chars:
            content = content[:max_content_chars]
            truncated = True
            issues.append(InstructionIssue("内容超过上限，已截断"))
        if content.strip() or issues:
            loaded.append(
                LoadedInstruction(scope, source, content, truncated, issues)
            )
    return loaded


def _expand(
    path: Path,
    root: Path,
    *,
    depth: int,
    stack: set[Path],
    issues: list[InstructionIssue],
    max_depth: int,
) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        issues.append(InstructionIssue(f"无法读取 {path}"))
        return ""
    base = path.resolve().parent
    lines = text.splitlines()
    expanded: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith(INCLUDE_PREFIX):
            expanded.append(line)
            continue
        raw = stripped[len(INCLUDE_PREFIX) :].strip().strip('"').strip("'")
        if not raw:
            issues.append(InstructionIssue(f"{path}: 空的 @include 行"))
            continue
        try:
            target = Path(raw)
            if _is_absolute_like(target):
                issues.append(
                    InstructionIssue(f"{path}: 拒绝绝对路径 @include {raw}")
                )
                continue
            resolved = (base / target).resolve()
        except (OSError, ValueError):
            issues.append(
                InstructionIssue(f"{path}: 无法解析 @include {raw}")
            )
            continue
        if not _is_within(resolved, root):
            issues.append(
                InstructionIssue(f"{path}: @include {raw} 超出所属根目录")
            )
            continue
        if resolved in stack:
            issues.append(
                InstructionIssue(f"{path}: @include {raw} 形成循环引用")
            )
            continue
        if depth + 1 > max_depth:
            issues.append(
                InstructionIssue(f"{path}: @include {raw} 超过嵌套深度上限")
            )
            continue
        stack.add(resolved)
        expanded.append(
            _expand(
                resolved,
                root,
                depth=depth + 1,
                stack=stack,
                issues=issues,
                max_depth=max_depth,
            )
        )
        stack.remove(resolved)
    return "\n".join(expanded)


def _is_absolute_like(path: Path) -> bool:
    if path.is_absolute() or path.drive:
        return True
    first = path.parts[0] if path.parts else ""
    return first in ("/", "\\") or first.endswith(":")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
