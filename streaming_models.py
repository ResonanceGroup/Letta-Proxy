"""
Streaming Models and Helper Functions for Letta Proxy

This module contains Pydantic models and utility functions for handling
streaming responses in the Letta Proxy server.
"""

from typing import Optional, List, Dict
from pydantic import BaseModel


class Delta(BaseModel):
    """Delta model for streaming response chunks."""
    content: Optional[str] = ""
    reasoning: Optional[str] = None
    # Allow OpenAI-style tool_calls on deltas during streaming
    tool_calls: Optional[List[Dict]] = None


class Choice(BaseModel):
    """Choice model for streaming response chunks."""
    index: int
    delta: Delta
    logprobs: Optional[None] = None
    finish_reason: Optional[str] = None


class StreamingChunk(BaseModel):
    """Streaming chunk model for OpenAI-compatible responses."""
    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: list[Choice]


class ErrorChunk(BaseModel):
    """Error chunk model for streaming error responses."""
    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: list[Choice]


def create_streaming_chunk(stream_id: str, model: str, content: str = "", reasoning: Optional[str] = None, finish_reason: Optional[str] = None) -> StreamingChunk:
    """Create a streaming chunk with proper Pydantic model structure.

    Args:
        stream_id: Unique identifier for the streaming session
        model: Model/agent name being used
        content: Content to include in the chunk
        reasoning: Optional reasoning content
        finish_reason: Optional finish reason (e.g., "stop", "end_turn")

    Returns:
        StreamingChunk: Properly structured streaming chunk
    """
    return StreamingChunk(
        id=stream_id,
        object="chat.completion.chunk",
        created=int(__import__('time').time()),
        model=model,
        choices=[
            Choice(
                index=0,
                delta=Delta(content=content, reasoning=reasoning),
                logprobs=None,
                finish_reason=finish_reason
            )
        ]
    )


def create_error_chunk(stream_id: str, model: str, error_message: str) -> StreamingChunk:
    """Create an error chunk for streaming responses.

    Args:
        stream_id: Unique identifier for the streaming session
        model: Model/agent name being used
        error_message: Error message to include in the chunk

    Returns:
        StreamingChunk: Error chunk with stop finish_reason
    """
    return StreamingChunk(
        id=stream_id,
        object="chat.completion.chunk",
        created=int(__import__('time').time()),
        model=model,
        choices=[
            Choice(
                index=0,
                delta=Delta(content=f"Error processing stream: {error_message}", reasoning=None),
                logprobs=None,
                finish_reason="stop"
            )
        ]
    )


def unescape_content(content: str) -> str:
    """
    Safely unescape common escape sequences in assistant text.
    Only handles actual escape sequences, doesn't try to parse JSON structure.
    """
    if not content:
        return content

    # Fast path: if content has real newlines and no escape sequences, return as-is
    if "\n" in content and "\\n" not in content and "\\t" not in content and "\\r" not in content:
        return content

    # Conservative approach: only replace clear escape sequences
    # Don't try to JSON-decode as it can break valid JSON structure
    result = content

    # Handle common escape sequences one by one
    result = result.replace("\\n", "\n")      # \\n -> newline
    result = result.replace("\\t", "\t")      # \\t -> tab
    result = result.replace("\\r", "\r")      # \\r -> carriage return
    result = result.replace("\\\\", "\\")     # \\\\ -> backslash
    result = result.replace('\\"', '"')       # \\" -> quote

    return result