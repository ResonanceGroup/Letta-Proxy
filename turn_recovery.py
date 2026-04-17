"""
Turn Recovery for Letta Proxy

Self-contained module for detecting and recovering from stuck approval states,
conversation-busy conflicts, and transient provider errors when communicating
with a Letta server.

This module has no FastAPI or streaming dependencies -- it only uses the Letta
SDK (AsyncLetta) and standard library.

Key capabilities:
- Classify 409 CONFLICT errors into actionable recovery types
- Fetch pending approvals from agent state or message history
- Deny stale/orphaned approvals so the conversation can proceed
- Cancel stuck runs
- Pre-send drain: clear all stale approvals before forwarding a new message

Architecture:
    All recovery intelligence lives here. The letta_send.py wrapper calls
    into this module when errors occur. main.py never imports this directly.

Author: Milo (generated for Jason Owens / Resonance Group)
"""

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from letta_client import AsyncLetta

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

class ConflictKind(Enum):
    """Classification of a 409 CONFLICT error from the Letta server."""
    APPROVAL_PENDING = "approval_pending"
    CONVERSATION_BUSY = "conversation_busy"
    UNKNOWN = "unknown"


APPROVAL_PENDING_FRAGMENTS = [
    "waiting for approval",
    "pending approval",
    "approve or deny",
]

CONVERSATION_BUSY_FRAGMENTS = [
    "is currently being processed",
    "conversation is busy",
]

RETRYABLE_PROVIDER_FRAGMENTS = [
    "anthropic api error",
    "openai api error",
    "connection error",
    "network error",
    "request timed out",
    "overloaded",
    "upstream connect error",
    "incomplete chunked read",
]


def classify_conflict(status: Optional[int], detail: str) -> ConflictKind:
    """Classify a Letta API error into a recovery-actionable kind.

    Args:
        status: HTTP status code (409, 500, etc.) or None if unavailable.
        detail: The error detail string from the Letta response body.

    Returns:
        ConflictKind indicating what type of recovery is needed.
    """
    if status != 409:
        return ConflictKind.UNKNOWN

    lower = detail.lower()
    for fragment in APPROVAL_PENDING_FRAGMENTS:
        if fragment in lower:
            return ConflictKind.APPROVAL_PENDING
    for fragment in CONVERSATION_BUSY_FRAGMENTS:
        if fragment in lower:
            return ConflictKind.CONVERSATION_BUSY
    return ConflictKind.UNKNOWN


def is_retryable_provider_error(status: Optional[int], detail: str) -> bool:
    """Check if an error is a transient provider/network error worth retrying.

    Args:
        status: HTTP status code.
        detail: Error detail string.

    Returns:
        True if the error looks transient and retryable.
    """
    if status is not None and status >= 500:
        return True
    if status == 429:
        return True
    lower = detail.lower()
    return any(frag in lower for frag in RETRYABLE_PROVIDER_FRAGMENTS)


def extract_error_detail(exc: Exception) -> tuple[Optional[int], str]:
    """Extract HTTP status and detail string from a Letta SDK exception.

    The SDK raises various exception types with different shapes.
    This function normalizes them into (status, detail) for classification.

    Args:
        exc: The exception raised by the Letta SDK.

    Returns:
        Tuple of (status_code_or_None, detail_string).
    """
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)

    # Try nested error shapes: exc.body.error.detail, exc.body.detail, exc.body
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        # Nested: body.error.detail
        inner_error = body.get("error", {})
        if isinstance(inner_error, dict):
            detail = inner_error.get("detail") or inner_error.get("message", "")
            if detail:
                return status, str(detail)
        # Direct: body.detail
        detail = body.get("detail") or body.get("message", "")
        if detail:
            return status, str(detail)

    # Fall back to str(exc)
    return status, str(exc)


# ---------------------------------------------------------------------------
# Pending approval data
# ---------------------------------------------------------------------------

@dataclass
class PendingApproval:
    """A single pending tool approval from a Letta agent."""
    run_id: str
    tool_call_id: str
    tool_name: str
    message_id: str


# ---------------------------------------------------------------------------
# Core recovery operations
# ---------------------------------------------------------------------------

async def get_pending_approvals(
    client: AsyncLetta,
    agent_id: str,
) -> List[PendingApproval]:
    """Fetch pending approvals for an agent.

    Strategy (mirrors official LettaBot):
    1. Check agent.pending_approval field (fast path, single API call).
    2. Fall back to scanning runs with stop_reason=requires_approval,
       then scanning conversation messages for unresolved approval_request_messages.

    Args:
        client: AsyncLetta client instance.
        agent_id: The Letta agent ID.

    Returns:
        List of PendingApproval objects. Empty list if none found.
    """
    # --- Fast path: agent.pending_approval field ---
    try:
        agent_state = await client.agents.retrieve(agent_id)
        pending = getattr(agent_state, "pending_approval", None)
        if pending is not None:
            approvals = _extract_approvals_from_pending(pending)
            if approvals:
                logger.info(
                    "Found %d pending approval(s) via agent state: %s",
                    len(approvals),
                    [a.tool_name for a in approvals],
                )
                return approvals
    except Exception as e:
        logger.warning("Failed to read agent.pending_approval, falling back to run scan: %s", e)

    # --- Fallback: scan runs + messages ---
    try:
        return await _scan_runs_for_approvals(client, agent_id)
    except Exception as e:
        logger.error("Failed to scan runs for pending approvals: %s", e)
        return []


def _extract_approvals_from_pending(pending: object) -> List[PendingApproval]:
    """Extract PendingApproval objects from an agent's pending_approval field.

    Handles both list-of-tool-calls and single-tool-call shapes.
    """
    if pending is None:
        return []

    run_id = getattr(pending, "run_id", "unknown") or "unknown"
    message_id = getattr(pending, "id", "unknown") or "unknown"

    # Try tool_calls (list) first, then singular tool_call
    raw_tool_calls = getattr(pending, "tool_calls", None)
    tool_calls_list = []

    if isinstance(raw_tool_calls, list):
        for tc in raw_tool_calls:
            tc_id = getattr(tc, "tool_call_id", None)
            tc_name = getattr(tc, "name", "unknown")
            if tc_id:
                tool_calls_list.append((tc_id, tc_name))
    elif raw_tool_calls is not None:
        tc_id = getattr(raw_tool_calls, "tool_call_id", None)
        tc_name = getattr(raw_tool_calls, "name", "unknown")
        if tc_id:
            tool_calls_list.append((tc_id, tc_name))

    # Fallback to singular tool_call field
    if not tool_calls_list:
        tc = getattr(pending, "tool_call", None)
        if tc is not None:
            tc_id = getattr(tc, "tool_call_id", None)
            tc_name = getattr(tc, "name", "unknown")
            if tc_id:
                tool_calls_list.append((tc_id, tc_name))

    seen = set()
    approvals = []
    for tc_id, tc_name in tool_calls_list:
        if tc_id in seen:
            continue
        seen.add(tc_id)
        approvals.append(PendingApproval(
            run_id=run_id,
            tool_call_id=tc_id,
            tool_name=tc_name,
            message_id=message_id,
        ))
    return approvals


async def _scan_runs_for_approvals(
    client: AsyncLetta,
    agent_id: str,
) -> List[PendingApproval]:
    """Scan agent runs and messages for unresolved approval requests.

    This is the fallback path when agent.pending_approval is unavailable.
    """
    # Find runs stuck on approval
    try:
        runs = await client.runs.list(agent_id=agent_id, stop_reason="requires_approval", limit=10)
        qualifying_run_ids = []
        async for run in runs:
            run_id = getattr(run, "id", None)
            if run_id:
                qualifying_run_ids.append(run_id)
            if len(qualifying_run_ids) >= 10:
                break
    except Exception:
        qualifying_run_ids = []

    if not qualifying_run_ids:
        return []

    # Scan messages for approval_request / approval_response messages
    try:
        messages_page = await client.agents.messages.list(agent_id, limit=100)
        messages = []
        async for msg in messages_page:
            messages.append(msg)
    except Exception as e:
        logger.warning("Failed to list agent messages for approval scan: %s", e)
        return []

    # Build set of already-resolved tool_call_ids
    resolved_tool_calls = set()
    for msg in messages:
        msg_type = getattr(msg, "message_type", None)
        if msg_type == "approval_response_message":
            for approval in getattr(msg, "approvals", []):
                tc_id = getattr(approval, "tool_call_id", None)
                if tc_id:
                    resolved_tool_calls.add(tc_id)

    # Collect unresolved approval requests
    pending = []
    seen = set()
    for msg in messages:
        msg_type = getattr(msg, "message_type", None)
        if msg_type != "approval_request_message":
            continue
        tool_calls = getattr(msg, "tool_calls", None) or []
        if not tool_calls:
            tc = getattr(msg, "tool_call", None)
            if tc:
                tool_calls = [tc]
        for tc in tool_calls:
            tc_id = getattr(tc, "tool_call_id", None)
            if not tc_id or tc_id in resolved_tool_calls or tc_id in seen:
                continue
            seen.add(tc_id)
            pending.append(PendingApproval(
                run_id=getattr(msg, "run_id", qualifying_run_ids[0]),
                tool_call_id=tc_id,
                tool_name=getattr(tc, "name", "unknown"),
                message_id=getattr(msg, "id", "unknown"),
            ))

    return pending


async def reject_approval(
    client: AsyncLetta,
    agent_id: str,
    tool_call_id: str,
    reason: str = "Auto-denied by proxy: stale approval from interrupted session",
) -> bool:
    """Reject a single pending tool approval.

    Sends an approval_response_message with approve=False for the given
    tool_call_id. The Letta server will then unblock the conversation.

    Args:
        client: AsyncLetta client instance.
        agent_id: The Letta agent ID.
        tool_call_id: The tool_call_id to deny.
        reason: Human-readable denial reason.

    Returns:
        True if the denial was accepted, False on failure.
    """
    try:
        await client.agents.messages.create(
            agent_id=agent_id,
            messages=[{
                "type": "approval",
                "approvals": [{
                    "approve": False,
                    "tool_call_id": tool_call_id,
                    "type": "approval",
                    "reason": reason,
                }],
            }],
            streaming=False,
        )
        logger.info("Rejected approval for tool_call_id=%s", tool_call_id)
        return True
    except Exception as e:
        err_str = str(e).lower()
        if "no tool call is currently awaiting approval" in err_str:
            logger.warning("Approval already resolved for tool_call_id=%s", tool_call_id)
            return True
        logger.error("Failed to reject approval for tool_call_id=%s: %s", tool_call_id, e)
        return False


async def cancel_runs(
    client: AsyncLetta,
    agent_id: str,
    run_ids: Optional[List[str]] = None,
) -> bool:
    """Cancel active runs for an agent.

    Args:
        client: AsyncLetta client instance.
        agent_id: The Letta agent ID.
        run_ids: Optional specific run IDs to cancel. If None, cancels all.

    Returns:
        True if cancellation succeeded.
    """
    try:
        await client.agents.messages.cancel(agent_id, run_ids=run_ids)
        logger.info(
            "Cancelled runs for agent %s%s",
            agent_id,
            f" ({', '.join(run_ids)})" if run_ids else "",
        )
        return True
    except Exception as e:
        err_str = str(e).lower()
        if "no active runs" in err_str:
            logger.info("No active runs to cancel for agent %s", agent_id)
            return True
        logger.error("Failed to cancel runs for agent %s: %s", agent_id, e)
        return False


# ---------------------------------------------------------------------------
# High-level recovery flows
# ---------------------------------------------------------------------------

async def drain_stale_approvals(
    client: AsyncLetta,
    agent_id: str,
) -> int:
    """Pre-send drain: fetch and deny all stale pending approvals.

    Call this before every message send to ensure the conversation is clean.
    This is cheap when there are no pending approvals (single API call).

    Args:
        client: AsyncLetta client instance.
        agent_id: The Letta agent ID.

    Returns:
        Number of approvals that were drained (0 if conversation was clean).
    """
    approvals = await get_pending_approvals(client, agent_id)
    if not approvals:
        return 0

    logger.warning(
        "Pre-send drain: found %d stale approval(s), denying...",
        len(approvals),
    )

    denied_count = 0
    run_ids = set()

    for approval in approvals:
        ok = await reject_approval(
            client,
            agent_id,
            approval.tool_call_id,
            reason="Auto-denied by proxy: pre-send drain of stale approval",
        )
        if ok:
            denied_count += 1
            if approval.run_id and approval.run_id != "unknown":
                run_ids.add(approval.run_id)

        # Small delay between denials — Letta processes them sequentially
        if len(approvals) > 1:
            await asyncio.sleep(1.0)

    # Cancel the runs that had approvals
    if run_ids:
        await cancel_runs(client, agent_id, list(run_ids))

    # Wait for the server to settle after denials + cancellations
    if denied_count > 0:
        await asyncio.sleep(2.0)

    logger.info("Pre-send drain complete: denied %d approval(s)", denied_count)
    return denied_count


async def recover_from_conflict(
    client: AsyncLetta,
    agent_id: str,
    detail: str,
) -> bool:
    """Attempt to recover from a 409 CONFLICT error.

    This is the main recovery entry point, called by letta_send.py when
    a send/stream fails with a 409.

    Strategy:
    1. Classify the conflict (approval_pending vs conversation_busy).
    2. For approval_pending: fetch approvals, deny them, cancel runs.
    3. For conversation_busy: just wait and let the caller retry.
    4. For unknown 409s: attempt approval drain as best-effort.

    Args:
        client: AsyncLetta client instance.
        agent_id: The Letta agent ID.
        detail: The error detail string from the 409 response.

    Returns:
        True if recovery succeeded and a retry is likely to work.
    """
    kind = classify_conflict(409, detail)

    if kind == ConflictKind.APPROVAL_PENDING:
        logger.info("Recovering from approval_pending conflict...")
        drained = await drain_stale_approvals(client, agent_id)
        if drained > 0:
            logger.info("Approval recovery succeeded: drained %d approval(s)", drained)
            return True
        # Drain found nothing — try cancelling any approval-blocked runs directly
        logger.warning("No approvals found to drain; trying direct run cancellation...")
        cancelled = await cancel_runs(client, agent_id)
        if cancelled:
            await asyncio.sleep(2.0)
            return True
        return False

    if kind == ConflictKind.CONVERSATION_BUSY:
        logger.info("Conversation is busy, will wait for caller to retry...")
        # Don't do anything here — the caller (letta_send) handles the wait+retry
        return True

    # Unknown 409 — try approval drain as best-effort
    logger.warning("Unknown 409 conflict, attempting best-effort approval drain...")
    drained = await drain_stale_approvals(client, agent_id)
    return drained > 0
