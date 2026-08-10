"""Condition matching for rule engine hooks."""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Mapping

from .model import ConditionGroup


def resolve_field(field: str, context: Mapping) -> object:
    node: object = context
    for part in field.split("."):
        if isinstance(node, Mapping) and part in node:
            node = node[part]
        else:
            return None
    return node


def _match_one(condition, context: Mapping) -> bool:
    value = resolve_field(condition.field, context)
    if condition.op == "exact":
        return value is not None and value == condition.value
    if condition.op == "ne":
        return value is not None and value != condition.value
    if condition.op == "regex":
        if value is None:
            return False
        try:
            return re.search(str(condition.value), str(value)) is not None
        except re.error:
            return False
    if condition.op == "glob":
        if value is None:
            return False
        return fnmatch.fnmatch(str(value), str(condition.value))
    return False


def match_group(group: ConditionGroup | None, context: Mapping) -> bool:
    if group is None:
        return True
    if group.combinator == "all":
        return all(_match_one(condition, context) for condition in group.conditions)
    return any(_match_one(condition, context) for condition in group.conditions)
