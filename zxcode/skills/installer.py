"""Remote skill installation from skills.sh download URLs."""

from __future__ import annotations

import json
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .frontmatter import SkillParseError, parse_skill_file


ALLOWED_SUFFIXES = {
    "",
    ".md",
    ".py",
    ".json",
    ".js",
    ".ts",
    ".sh",
    ".ps1",
    ".toml",
    ".yaml",
    ".yml",
    ".txt",
    ".csv",
}


class SkillInstallError(ValueError):
    pass


def parse_skills_url(url: str) -> tuple[str, str, str]:
    """Return ``(owner, repo, skill)`` from a skills.sh skill URL."""
    try:
        parsed = urllib.parse.urlparse(url.strip())
    except ValueError as error:
        raise SkillInstallError(f"invalid skill URL: {url}") from error
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or host not in (
        "skills.sh",
        "www.skills.sh",
    ):
        raise SkillInstallError(f"unsupported skill URL: {url}")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 3:
        raise SkillInstallError(
            "expected https://www.skills.sh/<owner>/<repo>/<skill>, "
            f"got {url}"
        )
    owner, repo, skill = parts
    if skill in (".", "..") or re.fullmatch(r"[A-Za-z0-9._-]+", skill) is None:
        raise SkillInstallError(f"invalid skill name in URL: {url}")
    return owner, repo, skill


def _download(owner: str, repo: str, skill: str, opener=None) -> dict:
    url = f"https://skills.sh/api/download/{owner}/{repo}/{skill}"
    opener = opener or urllib.request.build_opener()
    try:
        with opener.open(url, timeout=30) as response:
            raw = response.read()
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        raise SkillInstallError(f"download failed: {error}") from error
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SkillInstallError("invalid response from skills.sh") from error
    if not isinstance(payload, dict):
        raise SkillInstallError("invalid response from skills.sh")
    return payload


def _validated_files(payload: dict) -> list[tuple[Path, str]]:
    files = payload.get("files")
    if not isinstance(files, list):
        raise SkillInstallError("invalid payload: missing files list")
    entries: list[tuple[Path, str]] = []
    seen: set[str] = set()
    has_entry = False
    for item in files:
        if not isinstance(item, dict):
            raise SkillInstallError("invalid payload: file entry must be an object")
        raw_path = item.get("path")
        contents = item.get("contents")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise SkillInstallError("invalid payload: empty file path")
        if not isinstance(contents, str):
            raise SkillInstallError(
                f"invalid payload: contents of {raw_path} must be text"
            )
        rel = Path(raw_path.strip().replace("\\", "/"))
        if rel.drive or rel.is_absolute() or any(
            part in ("..", "") for part in rel.parts
        ):
            raise SkillInstallError(f"unsafe file path in skill: {raw_path}")
        if rel.suffix.lower() not in ALLOWED_SUFFIXES:
            raise SkillInstallError(
                f"disallowed file type in skill: {raw_path}"
            )
        if rel.name in ("SKILL.md", "skill.md"):
            has_entry = True
            rel = Path("skill.md")
        normalized = rel.as_posix()
        if normalized in seen:
            raise SkillInstallError(f"duplicate file path in skill: {normalized}")
        seen.add(normalized)
        entries.append((rel, contents))
    if not has_entry:
        raise SkillInstallError("skill payload has no SKILL.md")
    return entries


def _ensure_mode(contents: str) -> str:
    """Add ``mode: shared`` to frontmatter when the skill omits it."""
    if not contents.startswith("---"):
        return contents
    end = contents.find("\n---", 3)
    if end < 0:
        return contents
    front = contents[3:end]
    if re.search(r"(?m)^mode\s*:", front):
        return contents
    return contents[:3] + "\nmode: shared" + contents[3:]


def _rollback(dest: Path) -> None:
    shutil.rmtree(dest, ignore_errors=True)
    for parent in (dest.parent, dest.parent.parent):
        try:
            parent.rmdir()
        except OSError:
            pass


def install_skill(
    project_root: Path | str,
    url: str,
    *,
    opener=None,
) -> Path:
    """Download and install a skills.sh skill into ``.zxcode/skills/<skill>``.

    Returns the installed skill directory. Raises ``SkillInstallError`` and
    leaves nothing behind when the payload is unsafe or invalid.
    """
    owner, repo, skill = parse_skills_url(url)
    dest = Path(project_root) / ".zxcode" / "skills" / skill
    if dest.exists():
        raise SkillInstallError(f"skill already exists: {dest}")
    payload = _download(owner, repo, skill, opener)
    files = _validated_files(payload)
    files = [
        (rel, _ensure_mode(contents) if rel == Path("skill.md") else contents)
        for rel, contents in files
    ]
    try:
        dest.mkdir(parents=True)
        for rel, contents in files:
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(contents, encoding="utf-8")
        parse_skill_file(dest / "skill.md")
    except SkillInstallError:
        _rollback(dest)
        raise
    except (OSError, UnicodeError, SkillParseError) as error:
        _rollback(dest)
        raise SkillInstallError(f"install failed: {error}") from error
    return dest
