"""Project-local security policy for risky tool calls."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .tools.base import ToolContext, ToolResult


CONFIG_NAME = "zxcode-security.toml"
ALLOW = "allow"
ASK = "ask"
DENY = "deny"
_ACTIONS = {ALLOW, ASK, DENY}
_MODES = {"strict", "default", "allow"}

_READ_ONLY = {
    "get-childitem",
    "get-content",
    "get-item",
    "get-location",
    "select-string",
    "test-path",
}
_READ_ONLY_GIT = {"status", "diff", "log", "show", "branch"}
_BACKGROUND = re.compile(r"\b(?:Start-Job|Start-Process)\b", re.IGNORECASE)
_DYNAMIC = re.compile(r"[;|><`(){}\r\n]|\$|\&")
_WORDS = re.compile(r'''[^\s'\"]+|'[^']*'|\"[^\"]*\"''')
_ABSOLUTE = re.compile(
    r"(?<!\w)(?:[A-Za-z]:[\\/][^\s'\"]*|\\\\[^\s'\"]+|(?<!\S)[\\/][^\s'\"]+)"
)
_PARENT = re.compile(r"(?:^|[\\/\s'\"])\.\.(?:[\\/\s'\"]|$)")
_HOME = re.compile(r"(?:^|\s|['\"])~(?:[\\/]|\s|['\"]|$)")
_PROVIDER = re.compile(
    r"(?:^|\s|['\"])(?:Env|Variable|Function|Alias|Registry|HKLM|HKCU|Cert|WSMan)::?",
    re.IGNORECASE,
)
_REMOTE_SCRIPT = re.compile(
    r"\b(?:irm|iwr|invoke-webrequest|curl|wget)\b.*(?:\||;|&&).*\b"
    r"(?:iex|invoke-expression|powershell|pwsh|bash|sh)\b|"
    r"\b(?:iex|invoke-expression)\b.*\b(?:irm|iwr|invoke-webrequest|curl|wget)\b",
    re.IGNORECASE,
)
_DANGEROUS_SHELL = (
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\bremove-item\b(?=.*\b-recurse\b)(?=.*\b-force\b)", re.IGNORECASE),
    re.compile(r"\b(?:del|erase)\b.*\s/(?:s|q)\b", re.IGNORECASE),
    re.compile(r"\bformat(?:\.com)?\b", re.IGNORECASE),
    re.compile(r"\bdiskpart\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class SecurityRule:
    tool: str
    kind: str
    match: str
    signature: str
    reason: str = ""


@dataclass(frozen=True)
class SecurityDecision:
    action: str
    reason: str
    tool: str
    match: str
    signature: str
    code: str = "security_blocked"


@dataclass
class SecurityPolicy:
    root: Path
    mode: str = "default"
    allowed_roots: tuple[Path, ...] = field(default_factory=tuple)
    rules: list[SecurityRule] = field(default_factory=list)
    config_path: Path | None = None
    session_rules: dict[tuple[str, str, str], str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.root = self.root.resolve()
        self.config_path = self.config_path or self.root / CONFIG_NAME
        if self.mode not in _MODES:
            self.mode = "default"
        if not self.allowed_roots:
            self.allowed_roots = (self.root,)

    def allow_session(self, tool: str, match: str, signature: str) -> None:
        self.session_rules[(tool, match, signature)] = ALLOW

    def allow_permanent(self, tool: str, match: str, signature: str) -> None:
        rule = SecurityRule(tool, ALLOW, match, signature, "permanent allow")
        if rule not in self.rules:
            self.rules.append(rule)
            self.save()

    def evaluate_shell(self, command: str, root: Path | None = None) -> SecurityDecision:
        root = (root or self.root).resolve()
        signature = normalize_command(command)
        hard = shell_hard_block(signature, root)
        if hard is not None:
            return SecurityDecision(
                DENY, hard[1], "Bash", "command", signature, code=hard[0]
            )
        ruled = self._rule_decision("Bash", "command", signature)
        if ruled is not None:
            return ruled
        if self.mode == "strict":
            return SecurityDecision(DENY, "strict mode requires an allow rule", "Bash", "command", signature)
        if self.mode == "allow" or is_read_only_command(signature):
            return SecurityDecision(ALLOW, "allowed by mode or read-only classifier", "Bash", "command", signature)
        return SecurityDecision(ASK, "command is not a simple project-local read-only operation", "Bash", "command", signature)

    def evaluate_file(self, tool: str, path: Path, *, exists: bool) -> SecurityDecision:
        signature = self.path_signature(path)
        if not self.is_path_allowed(path):
            return SecurityDecision(
                DENY,
                "path is outside allowed roots",
                tool,
                "path",
                signature,
                code="path_outside_root",
            )
        ruled = self._rule_decision(tool, "path", signature)
        if ruled is not None:
            return ruled
        if self.mode == "strict":
            return SecurityDecision(DENY, "strict mode requires an allow rule", tool, "path", signature)
        if self.mode == "allow" or (tool == "WriteFile" and not exists):
            return SecurityDecision(ALLOW, "allowed by mode or new-file policy", tool, "path", signature)
        return SecurityDecision(ASK, f"{tool} will modify an existing file", tool, "path", signature)

    async def guard_call(
        self, tool: str, arguments: Mapping[str, Any], context: ToolContext, *, prompt: bool = True
    ) -> "ToolResult | None":
        if tool == "Bash":
            command = arguments.get("command")
            if not isinstance(command, str):
                return _error("invalid_arguments", "invalid arguments: command must be non-empty")
            return await self.guard_shell(command, context, prompt=prompt)
        if tool in {"WriteFile", "EditFile"}:
            path = resolve_project_path(context.working_directory, arguments.get("path"))
            if not isinstance(path, Path):
                return path
            return await self.guard_file(tool, path, context, exists=path.exists(), prompt=prompt)
        return None

    async def guard_shell(
        self, command: str, context: ToolContext, *, prompt: bool = True
    ) -> "ToolResult | None":
        return await self._guard(self.evaluate_shell(command, context.working_directory), context, prompt)

    async def guard_file(
        self, tool: str, path: Path, context: ToolContext, *, exists: bool, prompt: bool = True
    ) -> "ToolResult | None":
        return await self._guard(self.evaluate_file(tool, path, exists=exists), context, prompt)

    def is_path_allowed(self, path: Path) -> bool:
        try:
            candidate = path.resolve(strict=False)
        except (OSError, RuntimeError):
            return False
        return any(candidate.is_relative_to(root) for root in self.allowed_roots)

    def path_signature(self, path: Path) -> str:
        try:
            return path.resolve(strict=False).relative_to(self.root).as_posix()
        except (OSError, RuntimeError, ValueError):
            return str(path)

    def save(self) -> None:
        if self.config_path is None:
            return
        self.config_path.write_text(_dump_toml(self), encoding="utf-8")

    async def _guard(
        self, decision: SecurityDecision, context: ToolContext, prompt: bool
    ) -> "ToolResult | None":
        if decision.action == ALLOW:
            return None
        if decision.action == DENY:
            return _error(decision.code, decision.reason, tool=decision.tool, signature=decision.signature)
        if not prompt:
            return None
        if context.confirm is None:
            return _error("permission_denied", "security check requires confirmation", tool=decision.tool, signature=decision.signature)
        choice = _choice(await context.confirm(_title(decision), _detail(decision)))
        if choice == "deny":
            return _error("permission_denied", "permission denied by user", tool=decision.tool, signature=decision.signature)
        if choice == "session":
            self.allow_session(decision.tool, decision.match, decision.signature)
        elif choice == "permanent":
            try:
                self.allow_permanent(decision.tool, decision.match, decision.signature)
            except OSError:
                return _error("security_config_error", "unable to write security config")
        return None

    def _rule_decision(self, tool: str, match: str, signature: str) -> SecurityDecision | None:
        session = self.session_rules.get((tool, match, signature))
        if session in _ACTIONS:
            return SecurityDecision(session, "session rule", tool, match, signature)
        for rule in self.rules:
            if rule.tool not in {tool, "*"}:
                continue
            if rule.match == match and rule.signature == signature and rule.kind in _ACTIONS:
                return SecurityDecision(rule.kind, rule.reason or "project rule", tool, match, signature)
        return None


def load_policy(root: Path, default_mode: str = "default") -> SecurityPolicy:
    root = Path(root).resolve()
    path = root / CONFIG_NAME
    if not path.exists():
        return SecurityPolicy(root, default_mode, (root,), [], path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return SecurityPolicy(root, default_mode, (root,), [], path)
    mode = data.get("mode") if isinstance(data, Mapping) else default_mode
    allowed = _allowed_roots(root, data.get("allowed_roots", ["."]))
    return SecurityPolicy(
        root,
        mode if isinstance(mode, str) else default_mode,
        allowed,
        _rules(data.get("rules", [])),
        path,
    )


def resolve_project_path(root: Path, raw_path: Any) -> "Path | ToolResult":
    if not isinstance(raw_path, str) or not raw_path.strip():
        return _error("invalid_arguments", "invalid arguments: path must be a string")
    try:
        project = root.resolve(strict=True)
        supplied = Path(raw_path)
        candidate = (supplied if supplied.is_absolute() else project / supplied).resolve(
            strict=False
        )
    except (OSError, RuntimeError):
        return _error("invalid_arguments", "invalid arguments: invalid path")
    if not candidate.is_relative_to(project):
        return _error("path_outside_root", "path is outside working directory")
    return candidate


def normalize_command(command: str) -> str:
    return command.strip()


def shell_hard_block(command: str, root: Path) -> tuple[str, str] | None:
    if _BACKGROUND.search(command):
        return "security_blocked", "persistent background processes are not allowed"
    policy = command_path_policy(command, root)
    if policy == "outside":
        return "path_outside_root", "path is outside working directory"
    if _REMOTE_SCRIPT.search(command):
        return "security_blocked", "remote script download-and-execute is blocked"
    for pattern in _DANGEROUS_SHELL:
        if pattern.search(command):
            return "security_blocked", "dangerous shell command is blocked"
    return None


def is_read_only_command(command: str) -> bool:
    if _DYNAMIC.search(command):
        return False
    words = command.split()
    if not words:
        return False
    name = words[0].casefold()
    if name in _READ_ONLY:
        return True
    if name != "git" or len(words) < 2:
        return False
    subcommand = words[1].casefold()
    if subcommand not in _READ_ONLY_GIT or any(
        word.casefold().startswith("--output") for word in words[2:]
    ):
        return False
    if subcommand == "branch":
        return len(words) == 2 or words[2:] == ["--show-current"]
    return True


def command_path_policy(command: str, root: Path) -> str | None:
    if _PARENT.search(command) or _HOME.search(command) or _PROVIDER.search(command):
        return "outside"
    absolute = False
    project = root.resolve()
    for match in _ABSOLUTE.finditer(command):
        absolute = True
        raw = match.group(0).rstrip(",)")
        try:
            if not Path(raw).resolve(strict=False).is_relative_to(project):
                return "outside"
        except (OSError, RuntimeError):
            return "outside"
    for word in _WORDS.findall(command)[1:]:
        raw = word.strip("'\"")
        candidate = project / raw
        if not candidate.exists() and "/" not in raw and "\\" not in raw:
            continue
        try:
            if not candidate.resolve(strict=False).is_relative_to(project):
                return "outside"
        except (OSError, RuntimeError):
            return "outside"
    return "absolute" if absolute else None


def _allowed_roots(root: Path, raw: Any) -> tuple[Path, ...]:
    if not isinstance(raw, list):
        return (root,)
    allowed: list[Path] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        try:
            candidate = (Path(item) if Path(item).is_absolute() else root / item).resolve(
                strict=False
            )
        except (OSError, RuntimeError):
            continue
        if candidate.is_relative_to(root):
            allowed.append(candidate)
    return tuple(allowed) or (root,)


def _rules(raw: Any) -> list[SecurityRule]:
    if not isinstance(raw, list):
        return []
    rules: list[SecurityRule] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        tool = item.get("tool")
        kind = item.get("kind", item.get("decision"))
        match = item.get("match")
        signature = item.get("signature")
        if match is None:
            match = "command" if "command" in item else "path" if "path" in item else None
            signature = item.get(match) if isinstance(match, str) else signature
        if (
            isinstance(tool, str)
            and isinstance(kind, str)
            and isinstance(match, str)
            and isinstance(signature, str)
            and kind in _ACTIONS
        ):
            reason = item.get("reason")
            rules.append(SecurityRule(tool, kind, match, signature, reason if isinstance(reason, str) else ""))
    return rules


def _dump_toml(policy: SecurityPolicy) -> str:
    relative_roots = []
    for item in policy.allowed_roots:
        try:
            relative_roots.append(item.relative_to(policy.root).as_posix() or ".")
        except ValueError:
            continue
    lines = [
        f"mode = {_q(policy.mode)}",
        f"allowed_roots = [{', '.join(_q(item) for item in (relative_roots or ['.']))}]",
        "",
    ]
    for rule in policy.rules:
        lines.extend(
            [
                "[[rules]]",
                f"tool = {_q(rule.tool)}",
                f"kind = {_q(rule.kind)}",
                f"match = {_q(rule.match)}",
                f"signature = {_q(rule.signature)}",
            ]
        )
        if rule.reason:
            lines.append(f"reason = {_q(rule.reason)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _q(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _choice(value: bool | str | None) -> str:
    if value is True:
        return "once"
    if value is False or value is None:
        return "deny"
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"approve", "once", "allow_once"}:
            return "once"
        if normalized in {"session", "allow_session"}:
            return "session"
        if normalized in {"permanent", "always", "allow_permanent"}:
            return "permanent"
    return "deny"


def _title(decision: SecurityDecision) -> str:
    return f"Security check: {decision.tool}"


def _detail(decision: SecurityDecision) -> str:
    return (
        f"{decision.reason}\n\n"
        f"Tool: {decision.tool}\n"
        f"Signature type: {decision.match}\n"
        f"Exact signature: {decision.signature}"
    )


def _error(code: str, message: str, **metadata: Any) -> "ToolResult":
    from .tools.base import ToolResult

    return ToolResult(False, error={"code": code, "message": message}, metadata=metadata)
