"""In-memory command registry with alias-conflict detection."""

from __future__ import annotations

from .model import CommandMeta


class CommandRegistrationError(ValueError):
    """Raised when a name or alias collides with an existing command."""


class CommandRegistry:
    """Central registry; lookups and completions are case-insensitive."""

    def __init__(self) -> None:
        self._commands: dict[str, CommandMeta] = {}
        self._lookup: dict[str, str] = {}
        self._order: list[str] = []

    def register(self, meta: CommandMeta) -> CommandMeta:
        for display in meta.display_names:
            key = display.lower()
            owner = self._lookup.get(key)
            if owner is not None:
                raise CommandRegistrationError(
                    f"命令或别名冲突：{display}（已由 {owner} 注册）"
                )
        self._commands[meta.name] = meta
        self._order.append(meta.name)
        for display in meta.display_names:
            self._lookup[display.lower()] = meta.name
        return meta

    def get(self, name: str) -> CommandMeta | None:
        """Look up by name or alias, case-insensitively."""
        canonical = self._lookup.get(name.strip().lower())
        if canonical is None:
            return None
        return self._commands[canonical]

    def all_commands(self) -> list[CommandMeta]:
        return [self._commands[name] for name in self._order]

    def visible_commands(self) -> list[CommandMeta]:
        return [meta for meta in self.all_commands() if not meta.hidden]

    def complete(self, prefix: str) -> list[CommandMeta]:
        """Prefix-match visible commands; hidden commands are excluded."""
        key = prefix.strip().lower()
        if not key:
            return []
        return [
            meta
            for meta in self.visible_commands()
            if meta.name.startswith(key)
            or any(alias.startswith(key) for alias in meta.aliases)
        ]
