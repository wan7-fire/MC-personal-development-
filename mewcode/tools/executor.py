"""Execution boundary for registered tools."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

from .base import ToolCall, ToolContext, ToolRegistry, ToolResult


class ToolExecutor:
    OUTPUT_LIMIT = 65_536

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    async def execute_batch(
        self,
        calls: Sequence[ToolCall],
        context: ToolContext | None = None,
    ) -> list[ToolResult]:
        results: list[ToolResult | None] = [None] * len(calls)
        reads: list[tuple[int, ToolCall]] = []
        writes: list[tuple[int, ToolCall]] = []
        for index, call in enumerate(calls):
            tool = self.registry.get(call.name)
            (writes if tool is not None and not tool.read_only else reads).append(
                (index, call)
            )

        if reads:
            read_results = await asyncio.gather(
                *(
                    self.execute(call.id, call.name, call.arguments, context)
                    for _, call in reads
                )
            )
            for (index, _), result in zip(reads, read_results):
                results[index] = result

        for index, call in writes:
            results[index] = await self.execute(
                call.id, call.name, call.arguments, context
            )

        return [result for result in results if result is not None]

    async def execute(
        self,
        call_id: str,
        name: str,
        arguments: Mapping[str, Any],
        context: ToolContext | None = None,
    ) -> ToolResult:
        tool = self.registry.get(name)
        if tool is None:
            return ToolResult(
                False,
                error={"code": "unknown_tool", "message": f"unknown tool: {name}"},
                metadata={"call_id": call_id},
            )
        if not _matches_schema(arguments, tool.input_schema):
            return ToolResult(
                False,
                error={"code": "invalid_arguments", "message": "invalid tool arguments"},
                metadata={"call_id": call_id},
            )
        try:
            async with asyncio.timeout(tool.timeout_seconds):
                result = await tool.execute(arguments, context or ToolContext())
        except TimeoutError:
            result = ToolResult(
                False,
                error={
                    "code": "timeout",
                    "message": f"tool timed out after {tool.timeout_seconds:g}s",
                },
            )
        except Exception:
            result = ToolResult(
                False,
                error={"code": "execution_error", "message": "execution failed"},
            )
        result.metadata.setdefault("call_id", call_id)
        result.output = _truncate(result.output, self.OUTPUT_LIMIT, result.metadata)
        return result


def _truncate(text: str, limit: int, metadata: dict[str, Any]) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    metadata.update(truncated=True, original_bytes=len(encoded))
    return encoded[:limit].decode("utf-8", errors="ignore")


def _matches_schema(value: Any, schema: Mapping[str, Any]) -> bool:
    declared = schema.get("type")
    types = [declared] if isinstance(declared, str) else declared
    if types and not any(
        kind == "null" and value is None
        or kind == "object" and isinstance(value, Mapping)
        or kind == "array" and isinstance(value, list)
        or kind == "string" and isinstance(value, str)
        or kind == "integer" and isinstance(value, int) and not isinstance(value, bool)
        for kind in types
    ):
        return False
    if value is None:
        return True
    if isinstance(value, Mapping):
        properties = schema.get("properties", {})
        if any(key not in value for key in schema.get("required", ())):
            return False
        if schema.get("additionalProperties") is False and any(
            key not in properties for key in value
        ):
            return False
        return all(
            key not in properties or _matches_schema(item, properties[key])
            for key, item in value.items()
        )
    if isinstance(value, str):
        return len(value) >= schema.get("minLength", 0)
    if isinstance(value, int) and not isinstance(value, bool):
        return value >= schema.get("minimum", value)
    if isinstance(value, list):
        return len(value) >= schema.get("minItems", 0) and all(
            _matches_schema(item, schema.get("items", {})) for item in value
        )
    return True
