"""Worker role loading with priority override."""

from __future__ import annotations

import logging
from pathlib import Path

from ..skills.frontmatter import parse_frontmatter
from .model import WorkerRole


logger = logging.getLogger("zxcode.workers")

_PERMISSION_MODES = ("strict", "default", "allow")
_VERIFIER = "verifier"


def _parse_role(path: Path) -> WorkerRole | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        logger.warning("skip worker role %s: cannot read", path)
        return None
    if not text.startswith("---"):
        logger.warning("skip worker role %s: missing frontmatter", path)
        return None
    end = text.find("\n---", 3)
    if end < 0:
        logger.warning("skip worker role %s: missing closing ---", path)
        return None
    try:
        data = parse_frontmatter(text[3:end])
    except ValueError as error:
        logger.warning("skip worker role %s: %s", path, error)
        return None
    name = data.get("name")
    description = data.get("description")
    if not isinstance(name, str) or not name.strip():
        logger.warning("skip worker role %s: missing name", path)
        return None
    if not isinstance(description, str) or not description.strip():
        logger.warning("skip worker role %s: missing description", path)
        return None

    raw_allow = data.get("tools")
    tools_allow = None
    if raw_allow is not None:
        if not isinstance(raw_allow, list) or not all(
            isinstance(item, str) and item for item in raw_allow
        ):
            logger.warning("skip worker role %s: invalid tools", path)
            return None
        tools_allow = tuple(raw_allow)
    raw_deny = data.get("deny_tools", [])
    if not isinstance(raw_deny, list) or not all(
        isinstance(item, str) and item for item in raw_deny
    ):
        logger.warning("skip worker role %s: invalid deny_tools", path)
        return None
    tools_deny = tuple(raw_deny)

    model = data.get("model")
    if model is not None and not isinstance(model, str):
        logger.warning("skip worker role %s: invalid model", path)
        return None
    max_turns = data.get("max_turns", 20)
    if (
        not isinstance(max_turns, int)
        or isinstance(max_turns, bool)
        or max_turns <= 0
    ):
        logger.warning("skip worker role %s: invalid max_turns", path)
        return None
    mode = data.get("permission_mode", "default")
    if mode not in _PERMISSION_MODES:
        logger.warning("skip worker role %s: invalid permission_mode", path)
        return None
    body = text[end + 4 :].strip()
    return WorkerRole(
        name=name.strip(),
        description=description.strip(),
        body=body,
        tools_allow=tools_allow,
        tools_deny=tools_deny,
        model=model,
        max_turns=max_turns,
        permission_mode=mode,
    )


def load_roles(
    project_root: Path | str,
    *,
    user_dir: Path | str | None = None,
    builtin_root: Path | str | None = None,
    plugin_dirs=(),
    include_verifier: bool = False,
) -> dict[str, WorkerRole]:
    roots: list[tuple[str, Path]] = []
    roots.append(("project", Path(project_root) / ".zxcode" / "workers"))
    if user_dir is not None:
        roots.append(("user", Path(user_dir) / "workers"))
    if builtin_root is not None:
        roots.append(("builtin", Path(builtin_root)))
    for index, plugin in enumerate(plugin_dirs or ()):
        roots.append((f"plugin-{index}", Path(plugin)))

    roles: dict[str, WorkerRole] = {}
    for _level, root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.md")):
            role = _parse_role(path)
            if role is None:
                continue
            if role.name == _VERIFIER and not include_verifier:
                continue
            if role.name in roles:
                continue
            roles[role.name] = role
    return roles
