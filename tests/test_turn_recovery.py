"""
Tests for turn_recovery.py and letta_send.py

Tests are organized into:
1. Unit tests for error classifiers and detail extraction
2. Unit tests for approval data extraction
3. Integration tests using mock Letta client for recovery flows
4. Integration tests for letta_send.py wrapper retry logic

Run: python -m pytest tests/test_turn_recovery.py -v
"""

import asyncio
import sys
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# Add project root to path so we can import our modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from letta_client._exceptions import (
    APIStatusError,
    BadRequestError,
    ConflictError,
    InternalServerError,
    RateLimitError,
)

from turn_recovery import (
    ConflictKind,
    PendingApproval,
    classify_conflict,
    extract_error_detail,
    is_retryable_provider_error,
    get_pending_approvals,
    reject_approval,
    cancel_runs,
    drain_stale_approvals,
    recover_from_conflict,
    _extract_approvals_from_pending,
)

from letta_send import send_messages, stream_messages


# ---------------------------------------------------------------------------
# Helpers to create realistic Letta SDK exceptions
# ---------------------------------------------------------------------------

def make_conflict_error(detail: str, body: Optional[dict] = None) -> ConflictError:
    """Create a ConflictError matching the shape the Letta SDK raises."""
    if body is None:
        body = {"detail": detail}
    resp = httpx.Response(
        status_code=409,
        request=httpx.Request("POST", "http://localhost:8283/api/v1/agents/test/messages"),
        text=str(body),
    )
    return ConflictError(message=detail, response=resp, body=body)


def make_server_error(detail: str = "Internal Server Error") -> InternalServerError:
    resp = httpx.Response(
        status_code=500,
        request=httpx.Request("POST", "http://localhost:8283/api/v1/agents/test/messages"),
        text=detail,
    )
    return InternalServerError(message=detail, response=resp, body={"detail": detail})


def make_rate_limit_error(detail: str = "Rate limited") -> RateLimitError:
    resp = httpx.Response(
        status_code=429,
        request=httpx.Request("POST", "http://localhost:8283/api/v1/agents/test/messages"),
        text=detail,
    )
    return RateLimitError(message=detail, response=resp, body={"detail": detail})


def make_bad_request_error(detail: str = "Bad request") -> BadRequestError:
    resp = httpx.Response(
        status_code=400,
        request=httpx.Request("POST", "http://localhost:8283/api/v1/agents/test/messages"),
        text=detail,
    )
    return BadRequestError(message=detail, response=resp, body={"detail": detail})


# ---------------------------------------------------------------------------
# 1. Error classifiers
# ---------------------------------------------------------------------------

class TestClassifyConflict:
    """Test classify_conflict with various error detail strings."""

    def test_approval_pending_waiting_for_approval(self):
        assert classify_conflict(409, "Agent is waiting for approval on tool call") == ConflictKind.APPROVAL_PENDING

    def test_approval_pending_pending_approval(self):
        assert classify_conflict(409, "There is a pending approval for this agent") == ConflictKind.APPROVAL_PENDING

    def test_approval_pending_approve_or_deny(self):
        assert classify_conflict(409, "Please approve or deny the tool call") == ConflictKind.APPROVAL_PENDING

    def test_approval_pending_case_insensitive(self):
        assert classify_conflict(409, "AGENT IS WAITING FOR APPROVAL") == ConflictKind.APPROVAL_PENDING

    def test_conversation_busy_being_processed(self):
        assert classify_conflict(409, "Conversation is currently being processed") == ConflictKind.CONVERSATION_BUSY

    def test_conversation_busy_exact(self):
        assert classify_conflict(409, "The conversation is busy") == ConflictKind.CONVERSATION_BUSY

    def test_unknown_409(self):
        assert classify_conflict(409, "Some completely unknown conflict") == ConflictKind.UNKNOWN

    def test_non_409_with_approval_text(self):
        """Status must be 409 for any classification."""
        assert classify_conflict(500, "waiting for approval") == ConflictKind.UNKNOWN

    def test_non_409_200(self):
        assert classify_conflict(200, "waiting for approval") == ConflictKind.UNKNOWN

    def test_none_status(self):
        assert classify_conflict(None, "waiting for approval") == ConflictKind.UNKNOWN


class TestIsRetryableProviderError:
    """Test transient error detection."""

    def test_500_is_retryable(self):
        assert is_retryable_provider_error(500, "internal server error") is True

    def test_502_is_retryable(self):
        assert is_retryable_provider_error(502, "bad gateway") is True

    def test_503_is_retryable(self):
        assert is_retryable_provider_error(503, "service unavailable") is True

    def test_429_is_retryable(self):
        assert is_retryable_provider_error(429, "rate limited") is True

    def test_400_not_retryable(self):
        assert is_retryable_provider_error(400, "bad request") is False

    def test_404_not_retryable(self):
        assert is_retryable_provider_error(404, "not found") is False

    def test_409_not_retryable_by_status(self):
        """409 is not a transient error; it's handled by conflict recovery."""
        assert is_retryable_provider_error(409, "conflict") is False

    def test_anthropic_api_error_text(self):
        assert is_retryable_provider_error(None, "Anthropic API Error: overloaded") is True

    def test_openai_api_error_text(self):
        assert is_retryable_provider_error(None, "OpenAI API Error: connection reset") is True

    def test_connection_error_text(self):
        assert is_retryable_provider_error(None, "Connection error to upstream") is True

    def test_timeout_text(self):
        assert is_retryable_provider_error(None, "Request timed out after 30s") is True

    def test_normal_error_text(self):
        assert is_retryable_provider_error(None, "Invalid parameter value") is False


class TestExtractErrorDetail:
    """Test error detail extraction from Letta SDK exceptions."""

    def test_conflict_error_extraction(self):
        exc = make_conflict_error("Agent is waiting for approval on tool call")
        status, detail = extract_error_detail(exc)
        assert status == 409
        assert "waiting for approval" in detail.lower()

    def test_server_error_extraction(self):
        exc = make_server_error("Internal Server Error")
        status, detail = extract_error_detail(exc)
        assert status == 500
        assert "internal server error" in detail.lower()

    def test_rate_limit_extraction(self):
        exc = make_rate_limit_error("Rate limited")
        status, detail = extract_error_detail(exc)
        assert status == 429

    def test_nested_body_error_detail(self):
        """Test extraction from body.error.detail shape."""
        exc = make_conflict_error(
            "conflict",
            body={"error": {"detail": "Agent is waiting for approval on tool call abc123"}}
        )
        status, detail = extract_error_detail(exc)
        assert status == 409
        assert "waiting for approval" in detail.lower()

    def test_nested_body_error_message(self):
        """Test extraction from body.error.message shape."""
        exc = make_conflict_error(
            "conflict",
            body={"error": {"message": "Pending approval exists"}}
        )
        status, detail = extract_error_detail(exc)
        assert status == 409
        assert "pending approval" in detail.lower()

    def test_plain_exception_fallback(self):
        """Regular exceptions without status_code fall back to str()."""
        exc = RuntimeError("Something went wrong")
        status, detail = extract_error_detail(exc)
        assert status is None
        assert "something went wrong" in detail.lower()


# ---------------------------------------------------------------------------
# 2. Approval data extraction
# ---------------------------------------------------------------------------

class TestExtractApprovalsFromPending:
    """Test extraction of PendingApproval from agent state shapes."""

    def test_none_returns_empty(self):
        assert _extract_approvals_from_pending(None) == []

    def test_single_tool_call(self):
        """Agent state with a single pending tool call."""
        pending = MagicMock()
        pending.run_id = "run-001"
        pending.id = "msg-001"
        pending.tool_calls = None
        tc = MagicMock()
        tc.tool_call_id = "tc-001"
        tc.name = "send_message"
        pending.tool_call = tc

        result = _extract_approvals_from_pending(pending)
        assert len(result) == 1
        assert result[0].tool_call_id == "tc-001"
        assert result[0].tool_name == "send_message"
        assert result[0].run_id == "run-001"

    def test_multiple_tool_calls(self):
        """Agent state with multiple pending tool calls."""
        pending = MagicMock()
        pending.run_id = "run-002"
        pending.id = "msg-002"

        tc1 = MagicMock()
        tc1.tool_call_id = "tc-001"
        tc1.name = "archival_memory_insert"

        tc2 = MagicMock()
        tc2.tool_call_id = "tc-002"
        tc2.name = "send_message"

        pending.tool_calls = [tc1, tc2]
        pending.tool_call = None  # not used when tool_calls is present

        result = _extract_approvals_from_pending(pending)
        assert len(result) == 2
        assert result[0].tool_call_id == "tc-001"
        assert result[1].tool_call_id == "tc-002"

    def test_deduplication(self):
        """Duplicate tool_call_ids are deduplicated."""
        pending = MagicMock()
        pending.run_id = "run-003"
        pending.id = "msg-003"

        tc1 = MagicMock()
        tc1.tool_call_id = "tc-001"
        tc1.name = "send_message"

        tc2 = MagicMock()
        tc2.tool_call_id = "tc-001"  # duplicate
        tc2.name = "send_message"

        pending.tool_calls = [tc1, tc2]
        pending.tool_call = None

        result = _extract_approvals_from_pending(pending)
        assert len(result) == 1

    def test_no_tool_call_id_skipped(self):
        """Tool calls without tool_call_id are skipped."""
        pending = MagicMock()
        pending.run_id = "run-004"
        pending.id = "msg-004"
        pending.tool_calls = None

        tc = MagicMock()
        tc.tool_call_id = None
        tc.name = "broken_tool"
        pending.tool_call = tc

        result = _extract_approvals_from_pending(pending)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# 3. Mock Letta client for recovery flow tests
# ---------------------------------------------------------------------------

class MockAgentState:
    """Simulates agent state returned by client.agents.retrieve()."""
    def __init__(self, pending_approval=None):
        self.pending_approval = pending_approval


class MockLettaClient:
    """Lightweight mock of AsyncLetta with controllable behavior."""

    def __init__(self):
        self.agents = MockAgentsNamespace(self)
        self.runs = MockRunsNamespace()
        # Track calls for verification
        self.calls = []

    def record(self, method, **kwargs):
        self.calls.append({"method": method, **kwargs})


class MockAgentsNamespace:
    def __init__(self, client):
        self._client = client
        self.messages = MockMessagesNamespace(client)
        self._agent_state = MockAgentState()

    async def retrieve(self, agent_id):
        self._client.record("agents.retrieve", agent_id=agent_id)
        return self._agent_state

    def set_pending(self, pending):
        self._agent_state.pending_approval = pending


class MockMessagesNamespace:
    def __init__(self, client):
        self._client = client
        self._send_side_effect = None
        self._send_responses = []
        self._send_call_count = 0
        self._list_messages = []
        self._cancel_side_effect = None

    async def create(self, agent_id, messages=None, streaming=False):
        self._send_call_count += 1
        self._client.record(
            "agents.messages.create",
            agent_id=agent_id,
            call_number=self._send_call_count,
        )
        if self._send_side_effect:
            effect = self._send_side_effect
            # If it's a list, pop the first one (for sequencing: fail then succeed)
            if isinstance(effect, list):
                if effect:
                    e = effect.pop(0)
                    if e is not None:
                        raise e
                    # None means success
                else:
                    pass  # exhausted, succeed
            else:
                raise effect

        # Return canned response
        if self._send_responses:
            return self._send_responses.pop(0)
        return MagicMock(messages=[])

    async def create_stream(self, agent_id, messages=None, stream_tokens=True):
        """Returns an async iterator of events."""
        self._send_call_count += 1
        self._client.record(
            "agents.messages.create_stream",
            agent_id=agent_id,
            call_number=self._send_call_count,
        )
        if self._send_side_effect:
            effect = self._send_side_effect
            if isinstance(effect, list):
                if effect:
                    e = effect.pop(0)
                    if e is not None:
                        raise e
            else:
                raise effect

        # Yield some mock events
        for event in [MagicMock(text="hello"), MagicMock(text="world")]:
            yield event

    async def list(self, agent_id, limit=100):
        self._client.record("agents.messages.list", agent_id=agent_id, limit=limit)
        for msg in self._list_messages:
            yield msg

    async def cancel(self, agent_id, run_ids=None):
        self._client.record("agents.messages.cancel", agent_id=agent_id, run_ids=run_ids)
        if self._cancel_side_effect:
            raise self._cancel_side_effect


class MockRunsNamespace:
    def __init__(self):
        self._runs = []

    async def list(self, agent_id, stop_reason=None, limit=10):
        for run in self._runs:
            yield run


# ---------------------------------------------------------------------------
# 4. Recovery flow tests (using mock client)
# ---------------------------------------------------------------------------

class TestGetPendingApprovals:
    """Test the full get_pending_approvals flow."""

    @pytest.mark.asyncio
    async def test_fast_path_agent_state(self):
        """When agent.pending_approval is populated, use it directly."""
        client = MockLettaClient()
        pending = MagicMock()
        pending.run_id = "run-fast"
        pending.id = "msg-fast"
        tc = MagicMock()
        tc.tool_call_id = "tc-fast-001"
        tc.name = "send_message"
        pending.tool_calls = [tc]
        pending.tool_call = None
        client.agents.set_pending(pending)

        result = await get_pending_approvals(client, "agent-123")
        assert len(result) == 1
        assert result[0].tool_call_id == "tc-fast-001"
        assert result[0].tool_name == "send_message"

    @pytest.mark.asyncio
    async def test_no_pending_returns_empty(self):
        """When no approvals are pending, return empty list."""
        client = MockLettaClient()
        client.agents.set_pending(None)

        result = await get_pending_approvals(client, "agent-123")
        assert result == []


class TestRejectApproval:
    """Test approval denial."""

    @pytest.mark.asyncio
    async def test_successful_rejection(self):
        """Rejection succeeds and returns True."""
        client = MockLettaClient()
        result = await reject_approval(client, "agent-123", "tc-001")
        assert result is True

        # Verify the denial was sent
        create_calls = [c for c in client.calls if c["method"] == "agents.messages.create"]
        assert len(create_calls) == 1

    @pytest.mark.asyncio
    async def test_already_resolved_returns_true(self):
        """If the approval is already resolved, treat as success."""
        client = MockLettaClient()
        client.agents.messages._send_side_effect = RuntimeError(
            "No tool call is currently awaiting approval for this agent"
        )
        result = await reject_approval(client, "agent-123", "tc-001")
        assert result is True

    @pytest.mark.asyncio
    async def test_other_error_returns_false(self):
        """Unknown errors return False."""
        client = MockLettaClient()
        client.agents.messages._send_side_effect = RuntimeError("Network failure")
        result = await reject_approval(client, "agent-123", "tc-001")
        assert result is False


class TestCancelRuns:
    """Test run cancellation."""

    @pytest.mark.asyncio
    async def test_successful_cancel(self):
        client = MockLettaClient()
        result = await cancel_runs(client, "agent-123")
        assert result is True

    @pytest.mark.asyncio
    async def test_no_active_runs(self):
        """No active runs is treated as success."""
        client = MockLettaClient()
        client.agents.messages._cancel_side_effect = RuntimeError("No active runs found")
        result = await cancel_runs(client, "agent-123")
        assert result is True


class TestDrainStaleApprovals:
    """Test the pre-send drain flow."""

    @pytest.mark.asyncio
    async def test_no_approvals_to_drain(self):
        """When no approvals are pending, returns 0 quickly."""
        client = MockLettaClient()
        client.agents.set_pending(None)
        count = await drain_stale_approvals(client, "agent-123")
        assert count == 0

    @pytest.mark.asyncio
    async def test_drain_single_approval(self):
        """Drains a single stale approval."""
        client = MockLettaClient()
        pending = MagicMock()
        pending.run_id = "run-stale"
        pending.id = "msg-stale"
        tc = MagicMock()
        tc.tool_call_id = "tc-stale-001"
        tc.name = "archival_memory_insert"
        pending.tool_calls = [tc]
        pending.tool_call = None
        client.agents.set_pending(pending)

        count = await drain_stale_approvals(client, "agent-123")
        assert count == 1

        # Verify denial + cancellation calls
        create_calls = [c for c in client.calls if c["method"] == "agents.messages.create"]
        cancel_calls = [c for c in client.calls if c["method"] == "agents.messages.cancel"]
        assert len(create_calls) >= 1  # at least the denial
        assert len(cancel_calls) == 1


class TestRecoverFromConflict:
    """Test the high-level recovery orchestrator."""

    @pytest.mark.asyncio
    async def test_approval_pending_recovery(self):
        """Recovery from approval_pending: drain approvals."""
        client = MockLettaClient()
        pending = MagicMock()
        pending.run_id = "run-stuck"
        pending.id = "msg-stuck"
        tc = MagicMock()
        tc.tool_call_id = "tc-stuck-001"
        tc.name = "send_message"
        pending.tool_calls = [tc]
        pending.tool_call = None
        client.agents.set_pending(pending)

        result = await recover_from_conflict(
            client, "agent-123",
            "Agent is waiting for approval on tool call",
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_conversation_busy_recovery(self):
        """Recovery from conversation_busy: signal caller to retry."""
        client = MockLettaClient()
        result = await recover_from_conflict(
            client, "agent-123",
            "Conversation is currently being processed",
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_unknown_conflict_no_approvals(self):
        """Unknown 409 with no approvals to drain: returns False."""
        client = MockLettaClient()
        client.agents.set_pending(None)
        result = await recover_from_conflict(
            client, "agent-123",
            "Something unexpected happened",
        )
        assert result is False


# ---------------------------------------------------------------------------
# 5. letta_send wrapper tests
# ---------------------------------------------------------------------------

class TestSendMessages:
    """Test the non-streaming send wrapper."""

    @pytest.mark.asyncio
    async def test_successful_send(self):
        """Normal send succeeds on first try."""
        client = MockLettaClient()
        client.agents.set_pending(None)
        resp = await send_messages(client, "agent-123", [])
        assert resp is not None

    @pytest.mark.asyncio
    async def test_approval_recovery_then_success(self):
        """409 approval pending → recovery → successful retry."""
        client = MockLettaClient()
        client.agents.set_pending(None)

        # First call: raise 409 approval pending
        # Second call: succeed
        exc = make_conflict_error("Agent is waiting for approval on tool call")
        client.agents.messages._send_side_effect = [exc, None]

        # Set up pending approval for recovery to find
        pending = MagicMock()
        pending.run_id = "run-001"
        pending.id = "msg-001"
        tc = MagicMock()
        tc.tool_call_id = "tc-001"
        tc.name = "send_message"
        pending.tool_calls = [tc]
        pending.tool_call = None
        client.agents.set_pending(pending)

        resp = await send_messages(client, "agent-123", [], pre_drain=False)
        assert resp is not None

        # Verify: at least 2 send attempts (initial fail + retry)
        create_calls = [c for c in client.calls if c["method"] == "agents.messages.create"]
        assert len(create_calls) >= 2

    @pytest.mark.asyncio
    async def test_unrecoverable_error_raises(self):
        """Non-retryable errors are re-raised."""
        client = MockLettaClient()
        client.agents.set_pending(None)

        exc = make_bad_request_error("Invalid message format")
        client.agents.messages._send_side_effect = exc

        with pytest.raises(BadRequestError):
            await send_messages(client, "agent-123", [], pre_drain=False)

    @pytest.mark.asyncio
    async def test_server_error_retry(self):
        """500 error → retry → success."""
        client = MockLettaClient()
        client.agents.set_pending(None)

        exc = make_server_error("Internal Server Error")
        client.agents.messages._send_side_effect = [exc, None]

        resp = await send_messages(client, "agent-123", [], pre_drain=False)
        assert resp is not None


class TestStreamMessages:
    """Test the streaming send wrapper."""

    @pytest.mark.asyncio
    async def test_successful_stream(self):
        """Normal streaming succeeds on first try."""
        client = MockLettaClient()
        client.agents.set_pending(None)

        events = []
        async for event in stream_messages(client, "agent-123", []):
            events.append(event)
        assert len(events) == 2  # our mock yields 2 events

    @pytest.mark.asyncio
    async def test_approval_recovery_then_stream(self):
        """409 approval pending on stream → recovery → successful retry."""
        client = MockLettaClient()

        exc = make_conflict_error("Agent is waiting for approval on tool call")
        client.agents.messages._send_side_effect = [exc, None]

        pending = MagicMock()
        pending.run_id = "run-001"
        pending.id = "msg-001"
        tc = MagicMock()
        tc.tool_call_id = "tc-001"
        tc.name = "send_message"
        pending.tool_calls = [tc]
        pending.tool_call = None
        client.agents.set_pending(pending)

        events = []
        async for event in stream_messages(client, "agent-123", [], pre_drain=False):
            events.append(event)
        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_unrecoverable_stream_error_raises(self):
        """Non-retryable errors during stream setup are re-raised."""
        client = MockLettaClient()
        client.agents.set_pending(None)

        exc = make_bad_request_error("Invalid message format")
        client.agents.messages._send_side_effect = exc

        with pytest.raises(BadRequestError):
            async for event in stream_messages(client, "agent-123", [], pre_drain=False):
                pass


# ---------------------------------------------------------------------------
# 6. End-to-end scenario: simulated stuck tool approval
# ---------------------------------------------------------------------------

class TestStuckToolCallScenario:
    """
    Simulate the real-world scenario:
    1. Agent has a stuck tool approval from a previous interrupted session
    2. New request comes in through the proxy
    3. Pre-send drain detects and clears the stale approval
    4. Request succeeds on first try
    """

    @pytest.mark.asyncio
    async def test_presend_drain_clears_stuck_approval(self):
        """Full scenario: stuck approval cleared by pre-send drain."""
        client = MockLettaClient()

        # Set up stuck approval state
        pending = MagicMock()
        pending.run_id = "run-stuck-session"
        pending.id = "msg-stuck-session"
        tc = MagicMock()
        tc.tool_call_id = "tc-archival-insert-001"
        tc.name = "archival_memory_insert"
        pending.tool_calls = [tc]
        pending.tool_call = None
        client.agents.set_pending(pending)

        # Send message — pre-drain should clear the stale approval first
        resp = await send_messages(client, "agent-123", [{"role": "user", "content": "Hello"}])
        assert resp is not None

        # Verify: drain + denial + cancel happened before the actual send
        methods = [c["method"] for c in client.calls]
        assert "agents.retrieve" in methods  # drain checked agent state
        assert "agents.messages.create" in methods  # denial + actual send
        assert "agents.messages.cancel" in methods  # cancel stuck run

    @pytest.mark.asyncio
    async def test_409_then_recovery_then_success_streaming(self):
        """
        Full scenario: 409 on stream, recovery, then successful stream.
        This simulates the case where a stuck approval wasn't caught by
        pre-drain (race condition) but the 409 handler catches it.
        """
        client = MockLettaClient()

        # Pre-drain sees no pending (simulating a race)
        client.agents.set_pending(None)

        # First stream attempt: 409
        exc = make_conflict_error("Agent is waiting for approval on tool call")
        client.agents.messages._send_side_effect = [exc, None]

        # Patch recover_from_conflict to always succeed (simulating
        # the recovery finding and denying the stale approval)
        async def mock_recover(c, agent_id, detail):
            return True

        with patch("letta_send.recover_from_conflict", side_effect=mock_recover):
            events = []
            async for event in stream_messages(client, "agent-123", [], pre_drain=False):
                events.append(event)
            assert len(events) == 2


# ---------------------------------------------------------------------------
# Entry point for running tests directly
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
