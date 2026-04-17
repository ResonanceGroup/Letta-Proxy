"""
Letta Send — Resilient message send/stream wrapper for Letta Proxy.

This module provides two functions that replace direct Letta SDK calls:
- send_messages()   — non-streaming, returns the full response
- stream_messages()  — streaming, yields events as an async generator

Both functions handle:
1. Pre-send drain of stale approvals
2. 409 CONFLICT recovery (approval pending, conversation busy)
3. Transient provider error retry with exponential backoff
4. Clean error surfacing for unrecoverable failures

Integration:
    main.py imports send_messages / stream_messages instead of calling
    client.agents.messages.create / create_stream directly.

Dependencies:
    turn_recovery.py — all recovery logic lives there.
    letta_client      — AsyncLetta SDK.

Author: Milo (generated for Jason Owens / Resonance Group)
"""

import asyncio
import logging
from typing import Any, AsyncGenerator, List, Optional

from letta_client import AsyncLetta

# MessageCreate type varies across letta-client versions.
# We accept Any for the messages list since main.py constructs the objects.
MessageCreate = object  # type alias for annotation only

from turn_recovery import (
    ConflictKind,
    classify_conflict,
    drain_stale_approvals,
    extract_error_detail,
    is_retryable_provider_error,
    recover_from_conflict,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_APPROVAL_RETRIES = 2          # Max times to attempt approval recovery per request
MAX_BUSY_RETRIES = 3              # Max times to wait-and-retry for conversation_busy
MAX_TRANSIENT_RETRIES = 2         # Max times to retry transient provider errors
BUSY_BASE_DELAY_S = 5.0           # Base delay for conversation_busy backoff
TRANSIENT_BASE_DELAY_S = 1.0      # Base delay for transient error backoff


# ---------------------------------------------------------------------------
# Non-streaming send
# ---------------------------------------------------------------------------

async def send_messages(
    client: AsyncLetta,
    agent_id: str,
    messages: List[MessageCreate],
    *,
    pre_drain: bool = True,
    client_tools: Optional[List[Any]] = None,
) -> Any:
    """Send messages to a Letta agent with automatic recovery.

    Replaces direct calls to client.agents.messages.create().

    Args:
        client: AsyncLetta client instance.
        agent_id: The Letta agent ID.
        messages: List of MessageCreate objects to send.
        pre_drain: Whether to drain stale approvals before sending (default True).
        client_tools: Optional list of client-side tool definitions (ClientTool dicts).

    Returns:
        The response from client.agents.messages.create().

    Raises:
        Exception: Re-raises unrecoverable errors after exhausting retries.
    """
    # Step 1: Pre-send drain
    if pre_drain:
        try:
            await drain_stale_approvals(client, agent_id)
        except Exception as e:
            logger.warning("Pre-send drain failed (non-fatal): %s", e)

    # Step 2: Send with recovery loop
    approval_retries = 0
    busy_retries = 0
    transient_retries = 0

    extra_kwargs: dict = {}
    if client_tools:
        extra_kwargs["client_tools"] = client_tools

    while True:
        try:
            resp = await client.agents.messages.create(
                agent_id=agent_id,
                messages=messages,
                **extra_kwargs,
            )
            return resp

        except Exception as exc:
            status, detail = extract_error_detail(exc)
            kind = classify_conflict(status, detail)

            # --- Approval pending ---
            if kind == ConflictKind.APPROVAL_PENDING and approval_retries < MAX_APPROVAL_RETRIES:
                approval_retries += 1
                logger.warning(
                    "409 approval_pending on send (attempt %d/%d), recovering...",
                    approval_retries, MAX_APPROVAL_RETRIES,
                )
                recovered = await recover_from_conflict(client, agent_id, detail)
                if recovered:
                    continue  # retry the send
                logger.error("Approval recovery failed, re-raising")
                raise

            # --- Conversation busy ---
            if kind == ConflictKind.CONVERSATION_BUSY and busy_retries < MAX_BUSY_RETRIES:
                busy_retries += 1
                delay = BUSY_BASE_DELAY_S * (2 ** (busy_retries - 1))
                logger.warning(
                    "409 conversation_busy on send (attempt %d/%d), waiting %.1fs...",
                    busy_retries, MAX_BUSY_RETRIES, delay,
                )
                await asyncio.sleep(delay)
                continue  # retry the send

            # --- Transient provider error ---
            if is_retryable_provider_error(status, detail) and transient_retries < MAX_TRANSIENT_RETRIES:
                transient_retries += 1
                delay = TRANSIENT_BASE_DELAY_S * (2 ** (transient_retries - 1))
                logger.warning(
                    "Transient error on send (status=%s, attempt %d/%d), waiting %.1fs...",
                    status, transient_retries, MAX_TRANSIENT_RETRIES, delay,
                )
                await asyncio.sleep(delay)
                continue  # retry the send

            # --- Unrecoverable ---
            logger.error(
                "Unrecoverable error on send (status=%s, kind=%s): %s",
                status, kind.value if kind else "none", detail,
            )
            raise


# ---------------------------------------------------------------------------
# Streaming send
# ---------------------------------------------------------------------------

async def stream_messages(
    client: AsyncLetta,
    agent_id: str,
    messages: List[MessageCreate],
    *,
    pre_drain: bool = True,
    stream_tokens: bool = True,
    client_tools: Optional[List[Any]] = None,
) -> AsyncGenerator:
    """Stream messages from a Letta agent with automatic recovery.

    Replaces direct calls to client.agents.messages.create_stream().

    This function handles recovery at the *connection* level — if the initial
    stream setup fails with a 409 or transient error, it retries before
    yielding any events. Once streaming has started and events are flowing,
    errors are yielded as-is (mid-stream recovery is not safe).

    Args:
        client: AsyncLetta client instance.
        agent_id: The Letta agent ID.
        messages: List of MessageCreate objects to send.
        pre_drain: Whether to drain stale approvals before sending (default True).
        stream_tokens: Whether to stream individual tokens (default True).
        client_tools: Optional list of client-side tool definitions (ClientTool dicts).

    Yields:
        Letta streaming events (same as create_stream).

    Raises:
        Exception: Re-raises unrecoverable errors after exhausting retries.
    """
    # Step 1: Pre-send drain
    if pre_drain:
        try:
            await drain_stale_approvals(client, agent_id)
        except Exception as e:
            logger.warning("Pre-send drain failed (non-fatal): %s", e)

    # Step 2: Attempt to establish the stream with recovery
    approval_retries = 0
    busy_retries = 0
    transient_retries = 0

    extra_kwargs: dict = {}
    if client_tools:
        extra_kwargs["client_tools"] = client_tools

    while True:
        try:
            # Support both SDK versions: .create_stream() (older) and .stream() (newer)
            # .create_stream() returns an async iterable directly
            # .stream() returns a coroutine that resolves to an async iterable
            stream_fn = getattr(client.agents.messages, "create_stream", None) or \
                        getattr(client.agents.messages, "stream", None)
            if stream_fn is None:
                raise RuntimeError("Letta SDK has no streaming method (tried create_stream, stream)")
            stream = stream_fn(
                agent_id=agent_id,
                messages=messages,
                stream_tokens=stream_tokens,
                **extra_kwargs,
            )

            # If stream_fn returned a coroutine, await it to get the iterator
            if hasattr(stream, "__await__") or asyncio.iscoroutine(stream):
                stream = await stream

            # If we get here, the stream connection was established.
            # Yield events to the caller. Mid-stream errors are surfaced
            # to the caller directly — we don't attempt mid-stream recovery.
            async for event in stream:
                yield event

            # Stream completed successfully
            return

        except Exception as exc:
            status, detail = extract_error_detail(exc)
            kind = classify_conflict(status, detail)

            # --- Approval pending ---
            if kind == ConflictKind.APPROVAL_PENDING and approval_retries < MAX_APPROVAL_RETRIES:
                approval_retries += 1
                logger.warning(
                    "409 approval_pending on stream (attempt %d/%d), recovering...",
                    approval_retries, MAX_APPROVAL_RETRIES,
                )
                recovered = await recover_from_conflict(client, agent_id, detail)
                if recovered:
                    continue  # retry the stream
                logger.error("Approval recovery failed, re-raising")
                raise

            # --- Conversation busy ---
            if kind == ConflictKind.CONVERSATION_BUSY and busy_retries < MAX_BUSY_RETRIES:
                busy_retries += 1
                delay = BUSY_BASE_DELAY_S * (2 ** (busy_retries - 1))
                logger.warning(
                    "409 conversation_busy on stream (attempt %d/%d), waiting %.1fs...",
                    busy_retries, MAX_BUSY_RETRIES, delay,
                )
                await asyncio.sleep(delay)
                continue  # retry the stream

            # --- Transient provider error ---
            if is_retryable_provider_error(status, detail) and transient_retries < MAX_TRANSIENT_RETRIES:
                transient_retries += 1
                delay = TRANSIENT_BASE_DELAY_S * (2 ** (transient_retries - 1))
                logger.warning(
                    "Transient error on stream (status=%s, attempt %d/%d), waiting %.1fs...",
                    status, transient_retries, MAX_TRANSIENT_RETRIES, delay,
                )
                await asyncio.sleep(delay)
                continue  # retry the stream

            # --- Unrecoverable ---
            logger.error(
                "Unrecoverable error on stream (status=%s, kind=%s): %s",
                status, kind.value if kind else "none", detail,
            )
            raise
