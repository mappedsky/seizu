"""Tagging for chat messages.

A tag marks a message for special handling at the persistence / LLM-context
boundary. Tags live in ``additional_kwargs`` (which round-trips through the
LangGraph checkpoint serializer) under a single namespaced key, so the set of
tags on a message survives a save/load cycle.

To treat a new class of message specially, add a ``MessageTag`` member and
apply it where the message is produced; the enforcement points
(``chat_graph.load_thread_messages`` and, in future, the LLM-context builder)
filter on the tag rather than on message content.
"""

from collections.abc import Iterable
from enum import StrEnum
from typing import Any

from langchain_core.messages import BaseMessage


def message_text(content: Any) -> str:
    """Flatten LangChain message content (str | content blocks) to plain text.

    LangChain providers return ``content`` as either a string (OpenAI-style) or
    a list of content-block dicts (Anthropic, Gemini). Non-text blocks
    (``thinking``, ``image``, etc.) are dropped — only ``text`` parts are
    concatenated, so callers always see what the user can read.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


_TAGS_KEY = "seizu_tags"


class MessageTag(StrEnum):
    # Streamed to the UI but never persisted to the thread checkpoint and never
    # replayed into LLM context.
    EPHEMERAL = "ephemeral"
    # Persisted for UI/history visibility, but excluded from future LLM context
    # because it represents a failed/partial model turn rather than useful
    # conversation evidence.
    BROKEN = "broken"


def tag_message(message: BaseMessage, *tags: MessageTag) -> BaseMessage:
    """Add *tags* to *message* (idempotent), returning the same message."""
    current = set(message.additional_kwargs.get(_TAGS_KEY, []))
    current.update(tag.value for tag in tags)
    message.additional_kwargs[_TAGS_KEY] = sorted(current)
    return message


def has_tag(message: Any, tag: MessageTag) -> bool:
    additional_kwargs = getattr(message, "additional_kwargs", None)
    if not isinstance(additional_kwargs, dict):
        return False
    tags = additional_kwargs.get(_TAGS_KEY, [])
    return isinstance(tags, list) and tag.value in tags


def drop_tagged(messages: Iterable[Any], *tags: MessageTag) -> list[Any]:
    """Return *messages* without any carrying one of *tags*."""
    return [message for message in messages if not any(has_tag(message, tag) for tag in tags)]
