"""Server configuration parsed from the project ``zxcode-servers.toml``."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

CONFIG_NAME = "zxcode-servers.toml"

_SERVER_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_CALL_TIMEOUT = 60.0
DEFAULT_IDLE_TIMEOUT = 300.0

SUPPORTED_TRANSPORTS = ("stdio", "http")


class ConfigError(ValueError):
    """Invalid or incomplete MCP server configuration."""


@dataclass(frozen=True)
class ServerConfig:
    name: str
    transport: str
    command: tuple[str, ...] = ()
    url: str = ""
    env: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    cwd: Path = field(default_factory=Path.cwd)
    connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT
    call_timeout_seconds: float = DEFAULT_CALL_TIMEOUT
    idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT
    trusted: bool = False
    read_only_tools: tuple[str, ...] = ()
    disabled_tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class McpConfig:
    servers: tuple[ServerConfig, ...] = ()


def load_config(root: Path, environ: Mapping[str, str] | None = None) -> McpConfig:
    """Load ``zxcode-servers.toml`` from ``root``; missing file means none."""
    root = Path(root).resolve()
    path = root / CONFIG_NAME
    if not path.exists():
        return McpConfig()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise ConfigError(f"无法解析 {CONFIG_NAME}: {error}") from error
    raw_servers = data.get("servers")
    if raw_servers is None:
        return McpConfig()
    if not isinstance(raw_servers, list):
        raise ConfigError(f"{CONFIG_NAME} 中 servers 必须是数组")
    values = os.environ if environ is None else environ
    servers = tuple(
        _parse_server(raw, root, values) for raw in raw_servers
    )
    _reject_duplicate_names(servers)
    return McpConfig(servers)


def _parse_server(
    raw: Any, root: Path, environ: Mapping[str, str]
) -> ServerConfig:
    if not isinstance(raw, dict):
        raise ConfigError("每个 server 必须是一个 TOML 表")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError("server 缺少 name 字段")
    name = name.strip()
    if not _SERVER_NAME.fullmatch(name):
        raise ConfigError(
            f"server {name!r}: name 仅允许字母、数字、下划线和连字符"
        )
    transport = raw.get("transport")
    if transport not in SUPPORTED_TRANSPORTS:
        raise ConfigError(
            f"server {name!r}: 未知传输类型 {transport!r}（支持 stdio/http）"
        )

    command = _parse_command(raw.get("command"), name)
    url = _parse_url(raw.get("url"), name)
    if transport == "stdio" and not command:
        raise ConfigError(f"server {name!r}: stdio 传输必须提供 command")
    if transport == "http" and not url:
        raise ConfigError(f"server {name!r}: http 传输必须提供 url")

    env = _expand_mapping(raw.get("env"), name, "env", environ)
    headers = _expand_mapping(raw.get("headers"), name, "headers", environ)
    return ServerConfig(
        name=name,
        transport=transport,
        command=command,
        url=url,
        env=env,
        headers=headers,
        cwd=root,
        connect_timeout_seconds=_parse_timeout(
            raw.get("connect_timeout_seconds"), name, DEFAULT_CONNECT_TIMEOUT
        ),
        call_timeout_seconds=_parse_timeout(
            raw.get("call_timeout_seconds"), name, DEFAULT_CALL_TIMEOUT
        ),
        idle_timeout_seconds=_parse_timeout(
            raw.get("idle_timeout_seconds"), name, DEFAULT_IDLE_TIMEOUT
        ),
        trusted=bool(raw.get("trusted", False)),
        read_only_tools=_string_list(raw.get("read_only_tools"), name, "read_only_tools"),
        disabled_tools=_string_list(raw.get("disabled_tools"), name, "disabled_tools"),
    )


def _parse_command(value: Any, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    if isinstance(value, list) and all(isinstance(item, str) and item for item in value):
        return tuple(value)
    raise ConfigError(f"server {name!r}: command 必须是字符串或字符串数组")


def _parse_url(value: Any, name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"server {name!r}: url 必须是字符串")
    url = value.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ConfigError(f"server {name!r}: url 必须以 http:// 或 https:// 开头")
    return url


def _expand_mapping(
    value: Any, name: str, field_name: str, environ: Mapping[str, str]
) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ConfigError(f"server {name!r}: {field_name} 必须是字符串到字符串的映射")
    return {key: _expand_env(item, name, environ) for key, item in value.items()}


def _expand_env(value: str, name: str, environ: Mapping[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        variable = match.group(1)
        if variable not in environ:
            raise ConfigError(
                f"server {name!r}: 环境变量 {variable} 未定义（{CONFIG_NAME} 中通过 ${{{variable}}} 引用）"
            )
        return environ[variable]

    return _ENV_REF.sub(replace, value)


def _parse_timeout(value: Any, name: str, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ConfigError(f"server {name!r}: 超时必须为正数")
    return float(value)


def _string_list(value: Any, name: str, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ConfigError(f"server {name!r}: {field_name} 必须是字符串数组")
    return tuple(value)


def _reject_duplicate_names(servers: tuple[ServerConfig, ...]) -> None:
    seen: set[str] = set()
    for server in servers:
        if server.name in seen:
            raise ConfigError(f"重复的 server 名称: {server.name!r}")
        seen.add(server.name)
