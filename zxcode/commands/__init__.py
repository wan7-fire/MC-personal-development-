"""Centralized command registry, parser, dispatcher and UI abstraction."""

from .dispatcher import CommandContext, dispatch_command
from .model import AIPrompt, CommandInvocation, CommandMeta, CommandType
from .parser import parse
from .registry import CommandRegistrationError, CommandRegistry
from .ui import TextualUI, UIControl

__all__ = [
    "AIPrompt",
    "CommandContext",
    "CommandInvocation",
    "CommandMeta",
    "CommandRegistrationError",
    "CommandRegistry",
    "CommandType",
    "TextualUI",
    "UIControl",
    "dispatch_command",
    "parse",
]
