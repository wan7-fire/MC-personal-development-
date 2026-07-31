"""Prompt assembly for stable cacheable context and runtime context."""

from __future__ import annotations

import os
import platform
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class PromptModule:
    name: str
    priority: int
    content: str


DEFAULT_STABLE_MODULES = (
    PromptModule(
        "identity",
        10,
        "You are ZXCode, a terminal AI programming assistant.",
    ),
    PromptModule(
        "behavior",
        20,
        "Provide concise, accurate, and actionable coding help. Follow the user's "
        "current request and preserve unrelated work.",
    ),
    PromptModule(
        "coding",
        30,
        "Read the relevant code before editing. Prefer the smallest change that "
        "solves the requested behavior.",
    ),
    PromptModule(
        "safety",
        40,
        "Do not expose secrets. Ask for confirmation before destructive or unclear "
        "writes.",
    ),
    PromptModule(
        "tools",
        50,
        "Prefer dedicated tools over shell: ReadFile for files, Glob or Grep for "
        "search, EditFile or WriteFile for edits, and Bash only when dedicated "
        "tools do not cover the task.",
    ),
    PromptModule(
        "output",
        60,
        "Lead with the result, keep explanations compact, and mention verification "
        "when relevant.",
    ),
)

PLAN_ONLY_CONTENT = (
    "You are currently in plan-only mode: write tools are blocked. Do not retry "
    "blocked calls. Instead, produce a numbered, step-by-step plan describing "
    "what you would change and why, and hand it back for approval."
)


def build_stable_prompt(
    modules: Iterable[PromptModule] | None = None, root: Path | None = None
) -> str:
    selected = list(DEFAULT_STABLE_MODULES if modules is None else modules)
    if root is not None:
        selected.extend(load_project_modules(root))
    parts = [
        f"## {module.name}\n{module.content.strip()}"
        for module in sorted(selected, key=lambda item: (item.priority, item.name))
        if module.content.strip()
    ]
    return "\n\n---\n\n".join(parts)


def load_project_modules(root: Path, directory: str = "prompts") -> list[PromptModule]:
    prompt_dir = root / directory
    if not prompt_dir.is_dir():
        return []
    modules = []
    for path in sorted(prompt_dir.glob("*.md")):
        try:
            content = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not content:
            continue
        priority, name = _module_name(path.stem)
        modules.append(PromptModule(name, priority, content))
    return modules


def build_environment_message(
    root: Path | None = None,
    *,
    now: str | None = None,
    environ: Mapping[str, str] | None = None,
    git_summary: str | None = None,
) -> dict[str, str]:
    cwd = root or Path.cwd()
    timestamp = now or datetime.now().astimezone().isoformat(timespec="seconds")
    values = environ or os.environ
    secret_names = [
        name
        for name in sorted(values)
        if any(marker in name.upper() for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
    ]
    git = git_summary if git_summary is not None else _git_summary(cwd)
    content = "\n".join(
        [
            "Runtime environment:",
            f"- Working directory: {cwd.as_posix()}",
            f"- OS: {platform.platform()}",
            f"- Time: {timestamp}",
            f"- Git: {git}",
            f"- Sensitive env values omitted: {', '.join(secret_names) if secret_names else 'none'}",
        ]
    )
    return {"role": "system", "content": content}


def plan_only_message() -> dict[str, str]:
    return {"role": "system", "content": PLAN_ONLY_CONTENT}


def _module_name(stem: str) -> tuple[int, str]:
    prefix, _, rest = stem.partition("-")
    if prefix.isdigit() and rest:
        return int(prefix), rest
    return 100, stem


def _git_summary(root: Path) -> str:
    try:
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=root,
            text=True,
            capture_output=True,
            timeout=2,
            check=False,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--short"],
            cwd=root,
            text=True,
            capture_output=True,
            timeout=2,
            check=False,
        ).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    if not branch:
        return "unavailable"
    return f"branch {branch}, {'dirty' if status else 'clean'}"
