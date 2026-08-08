"""Three-level skill scanning and index building."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..tools import ToolRegistry
from .frontmatter import SkillParseError, parse_skill_file
from .model import SkillIssue, SkillMeta


class SkillValidationError(ValueError):
    pass


@dataclass
class SkillIndex:
    by_name: dict[str, SkillMeta] = field(default_factory=dict)
    issues: list[SkillIssue] = field(default_factory=list)


def _candidates(root: Path):
    if not root.is_dir():
        return
    for path in sorted(root.glob("*.md")):
        yield path
    for directory in sorted(item for item in root.iterdir() if item.is_dir()):
        entry = directory / "skill.md"
        if entry.exists():
            yield entry


def is_within(root: Path, path: Path) -> bool:
    try:
        return path.resolve(strict=False).is_relative_to(root.resolve())
    except (OSError, RuntimeError):
        return False


def _own_tool_names(skill_file: Path) -> set[str]:
    tools_dir = skill_file.parent / "tools"
    if not tools_dir.is_dir():
        return set()
    return {
        path.stem
        for path in tools_dir.glob("*.md")
        if is_within(skill_file.parent, path)
    }


def scan_skills(
    project_root: Path | str,
    user_dir: Path | str | None,
    builtin_root: Path | str | None,
    registry: ToolRegistry,
) -> SkillIndex:
    roots = (
        (Path(project_root) / ".zxcode" / "skills", "project"),
        (Path(user_dir) / "skills" if user_dir else None, "user"),
        (Path(builtin_root) if builtin_root else None, "builtin"),
    )
    by_name: dict[str, SkillMeta] = {}
    issues: list[SkillIssue] = []
    known = set(registry.names())
    for root, level in roots:
        if root is None or not root.is_dir():
            continue
        for skill_file in _candidates(root):
            if not is_within(root, skill_file):
                issues.append(
                    SkillIssue(
                        str(skill_file),
                        "skill file resolves outside skill root",
                    )
                )
                continue
            try:
                meta, _ = parse_skill_file(skill_file, level=level)
            except SkillParseError as error:
                issues.append(SkillIssue(str(skill_file), error.message))
                continue
            if meta.name in by_name:
                continue
            if meta.tools is not None:
                own = _own_tool_names(skill_file)
                unknown = [
                    tool
                    for tool in meta.tools
                    if tool not in known and tool not in own
                ]
                if unknown:
                    raise SkillValidationError(
                        f"{meta.name}: whitelist references unknown tool(s) "
                        f"{', '.join(unknown)} in {skill_file}"
                    )
            by_name[meta.name] = meta
    return SkillIndex(by_name=by_name, issues=issues)
