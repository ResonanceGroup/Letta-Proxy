"""
Letta Proxy Server - OpenAI-Compatible API for Letta Agents

This module provides an OpenAI-compatible API server that acts as a proxy to Letta agents,
enabling seamless integration with existing OpenAI-based applications while leveraging
Letta's advanced memory and tool capabilities.

The proxy supports:
- System prompt overlay management through Letta memory blocks
- Tool synchronization and execution
- Streaming responses compatible with OpenAI's API
- Dynamic agent discovery and mapping
- Comprehensive error handling and fallback mechanisms

Environment Variables (set in .env file):
    LETTA_BASE_URL: Base URL for the Letta server (default: http://localhost:8283)
    LETTA_API_KEY: API key for Letta authentication (required for Letta Cloud)
    LETTA_PROJECT: Project name for Letta Cloud (default: default-project)
    PROXY_HOST: IP address for the proxy server to bind to (default: 0.0.0.0)
    PROXY_PORT: Port for the proxy server to listen on (default: 8000)
    REMOVE_SYSTEM_PROMPT: When set to 'true', omit system prompts from data sent to Letta agent (default: false)
    PROXY_DEBUG_SESSIONS: Enable debug endpoint for session inspection (default: disabled)
    DEBUG_RAW_OUTPUT: Write raw response text to debug file for analysis (default: false)

Author: Jason Owens
Version: 1.0.0
"""

import ast
import asyncio
import json
import os
import time
import uuid
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel

from letta_client import AsyncLetta
from letta_compat import (
    MessageCreate,
    TextContent,
    AssistantMessage,
    ToolCallMessage,
    ToolReturnMessage,
)

from proxy_tool_bridge import ProxyToolBridge, initialize_proxy_bridge, get_proxy_bridge
from proxy_overlay import ProxyOverlayManager
from streaming_models import (
    StreamingChunk,
    Delta,
    Choice,
    create_streaming_chunk,
    create_error_chunk,
    unescape_content
)
from streaming_content_processor import process_streaming_chunk, cleanup_streaming_session
from letta_send import send_messages, stream_messages

# Load environment variables from .env file
load_dotenv()

# Configuration from environment variables
LETTA_BASE_URL = os.getenv("LETTA_BASE_URL", "http://localhost:8283")
LETTA_API_KEY = os.getenv("LETTA_API_KEY")
PROXY_HOST = os.getenv("PROXY_HOST", "0.0.0.0")
PROXY_PORT = int(os.getenv("PROXY_PORT", "8000"))
REMOVE_SYSTEM_PROMPT = os.getenv("REMOVE_SYSTEM_PROMPT", "false").lower() == "true"
DEBUG_RAW_OUTPUT = os.getenv("DEBUG_RAW_OUTPUT", "false").lower() == "true"
DEBUG_OUTPUT_FILE = "letta_proxy_debug.txt"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Letta Proxy Server",
    description="OpenAI-compatible API proxy for Letta agents with advanced memory management",
    version="1.0.0"
)

client: Optional[AsyncLetta] = None


@dataclass
class AgentDescriptor:
    """Descriptor for Letta agents with ID and project mapping."""
    agent_id: str
    project_id: Optional[str] = None


agent_map: Dict[str, AgentDescriptor] = {}
overlay_manager: Optional[ProxyOverlayManager] = None


def write_debug_output(content: str, stage: str = "RESPONSE") -> None:
    """Write debug content to file if DEBUG_RAW_OUTPUT is enabled.
    
    Args:
        content: The content to write to the debug file
        stage: A label describing what stage this content represents
    """
    if not DEBUG_RAW_OUTPUT:
        return
    
    try:
        import datetime
        timestamp = datetime.datetime.now().isoformat()
        debug_entry = f"\n{'='*60}\n[{timestamp}] {stage}\n{'='*60}\n{content}\n"
        
        with open(DEBUG_OUTPUT_FILE, "a", encoding="utf-8") as f:
            f.write(debug_entry)
    except Exception as e:
        logger.warning(f"Failed to write debug output: {e}")



class ChatCompletionRequest(BaseModel):
    """Request model for OpenAI-compatible chat completions endpoint.

    Attributes:
        model: The model/agent name to use for the request
        messages: List of messages in OpenAI chat format
        stream: Whether to stream the response (default: False)
        tools: Optional list of tools available to the agent
        tool_results: Optional list of tool execution results
    """
    model: str
    messages: List[Dict[str, Any]]
    stream: Optional[bool] = False
    tools: Optional[List[Dict[str, Any]]] = None
    tool_results: Optional[List[Dict[str, Any]]] = None


def _normalize_content(content: Any) -> str:
    """Normalize message content to string format.

    Handles various content formats including strings, lists of content blocks,
    and nested content structures commonly used in OpenAI API requests.

    Args:
        content: The content to normalize (str, list, dict, or None)

    Returns:
        Normalized string content, empty string if content is None
    """
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and "text" in block:
                    parts.append(str(block.get("text", "")))
                elif "content" in block and isinstance(block["content"], str):
                    parts.append(block["content"])
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    if content is None:
        return ""
    return str(content)


def _openai_tools_to_client_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert OpenAI tool definitions to Letta client_tools format.

    OpenAI format:
        {"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}
    Letta client_tools format:
        {"name": "...", "description": "...", "parameters": {...}}
    """
    client_tools = []
    for tool in tools:
        func = tool.get("function", {})
        client_tool: Dict[str, Any] = {"name": func.get("name", "")}
        if func.get("description"):
            client_tool["description"] = func["description"]
        if func.get("parameters"):
            client_tool["parameters"] = func["parameters"]
        client_tools.append(client_tool)
    return client_tools


def _build_tool_return_messages(
    tool_results_parts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build Letta tool_return messages from OpenAI-style tool result messages.

    Uses the MessageToolReturnCreate format — the preferred way to send tool results
    back after client-side tool execution.

    Args:
        tool_results_parts: List of dicts with keys:
            tool_call_id, content (the tool result payload)
    """
    tool_returns = []
    for part in tool_results_parts:
        tool_returns.append({
            "type": "tool",
            "status": "success",
            "tool_call_id": part.get("tool_call_id", ""),
            "tool_return": part.get("content", ""),
        })
    return [{"type": "tool_return", "tool_returns": tool_returns}]


def _parse_proxy_tool_return(tool_return: Any) -> Optional[Dict[str, Any]]:
    """Parse a proxy tool return payload back into a dict if present."""
    data = tool_return
    if isinstance(data, str):
        for parser in (json.loads, ast.literal_eval):
            try:
                data = parser(data)
                break
            except Exception:
                continue
    if isinstance(data, dict) and data.get("type") == "proxy_tool_call":
        return data
    return None


def _collect_system_content(messages: List[Dict[str, Any]]) -> Optional[str]:
    """Extract and combine all system messages from the request.

    Collects all system messages from the messages list and combines them
    into a single system prompt string. Filters out empty messages.

    Args:
        messages: List of messages in OpenAI chat format

    Returns:
        Combined system content or None if no system messages found
    """
    system_chunks: List[str] = []
    for msg in messages:
        if msg.get("role") == "system":
            system_chunks.append(_normalize_content(msg.get("content")))
    if not system_chunks:
        return None
    filtered = [chunk for chunk in system_chunks if chunk]
    if not filtered:
        return None
    return "\n\n".join(filtered)


def _extract_latest_user_message(messages: List[Dict[str, Any]]) -> Optional[str]:
    """Extract the most recent user message content.

    Searches through messages in reverse order to find the latest user message.
    This is used for non-streaming responses where only the final user message
    needs to be sent to the agent.

    Args:
        messages: List of messages in OpenAI chat format

    Returns:
        Latest user message content or None if no user messages found
    """
    for msg in reversed(messages):
        if msg.get("role") == "user":
            text = _normalize_content(msg.get("content"))
            if text:
                return text
            return ""
    return None


def _extract_trailing_tool_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract tool messages that appear after the last assistant message.

    Tool messages that come after the assistant's response (e.g., tool calls)
    need to be forwarded to the agent for proper context and execution.

    Args:
        messages: List of messages in OpenAI chat format

    Returns:
        List of trailing tool messages to forward to the agent
    """
    if not messages:
        return []
    last_assistant_idx = -1
    for idx, msg in enumerate(messages):
        if msg.get("role") == "assistant":
            last_assistant_idx = idx
    trailing = messages[last_assistant_idx + 1 :] if last_assistant_idx >= 0 else messages
    return [msg for msg in trailing if msg.get("role") == "tool"]


def validate_configuration() -> None:
    """Validate that required environment variables are set.

    Logs warnings for configuration issues that might affect functionality:
    - Default LETTA_BASE_URL being used
    - Missing LETTA_API_KEY which may cause authentication failures

    This function helps users identify configuration issues early.
    """
    if LETTA_BASE_URL == "http://localhost:8283":
        logger.warning("LETTA_BASE_URL is using default value. Please set LETTA_BASE_URL environment variable.")
    if not LETTA_API_KEY:
        logger.warning("LETTA_API_KEY not set. Authentication may fail for Letta Cloud.")


@app.on_event("startup")
async def startup_event() -> None:
    """Initialize the proxy server on startup.

    This function performs the following initialization steps:
    1. Validates environment configuration
    2. Creates and configures the Letta client
    3. Discovers available agents and builds the agent mapping
    4. Initializes the proxy overlay manager for system prompt management
    5. Sets up the proxy tool bridge for tool synchronization

    If agent discovery fails, the system will still start but agent
    information will be populated on the first request.
    """
    global client, agent_map, overlay_manager

    # Validate configuration
    validate_configuration()

    # Log configuration values for debugging
    logger.info(f"Configuration loaded - LETTA_BASE_URL: {LETTA_BASE_URL}")
    logger.info(f"Configuration loaded - LETTA_API_KEY: {'***' + LETTA_API_KEY[-4:] if LETTA_API_KEY else 'None'}")
    logger.info(f"Configuration loaded - REMOVE_SYSTEM_PROMPT: {REMOVE_SYSTEM_PROMPT}")
    logger.info(f"Configuration loaded - LETTA_PROJECT: {os.getenv('LETTA_PROJECT', 'default-project')}")

    # Configure client - choose based on base_url setting
    if LETTA_BASE_URL and LETTA_BASE_URL != "http://localhost:8283":
        # Custom base_url specified - use it (could be custom server or specific cloud URL)
        logger.info(f"Configuring for custom server - base_url: {LETTA_BASE_URL}")
        client_kwargs = {"base_url": LETTA_BASE_URL}
        if LETTA_API_KEY:
            client_kwargs["api_key"] = LETTA_API_KEY
    else:
        # No custom base_url - use cloud mode if API key present, otherwise local default
        if LETTA_API_KEY:
            project_name = os.getenv("LETTA_PROJECT")
            logger.info(f"Configuring for Letta Cloud (no base_url) - project: {project_name or 'default'}")
            client_kwargs = {
                "api_key": LETTA_API_KEY,
                "project": project_name  # None means default project
            }
        else:
            logger.info(f"Configuring for local server - base_url: {LETTA_BASE_URL}")
            client_kwargs = {"base_url": LETTA_BASE_URL}

    # Debug: Check if the URL scheme is causing issues
    print(f"Creating Letta client with base_url: {LETTA_BASE_URL}")
    print(f"URL scheme: {LETTA_BASE_URL.split('://')[0] if '://' in LETTA_BASE_URL else 'No scheme'}")


    # Add timeout configuration to prevent ReadTimeout errors
    client = AsyncLetta(**client_kwargs)

    try:
        agents_page = await client.agents.list()
        agents = [agent async for agent in agents_page]
        agent_names = [agent.name for agent in agents]
        agent_map = {
            agent.name: AgentDescriptor(agent_id=agent.id, project_id=getattr(agent, "project_id", None))
            for agent in agents
        }
        logger.info(f"Connected to Letta server. Found {len(agents)} agents: {agent_names}")
    except Exception as e:
        logger.warning(f"Could not connect to Letta server on startup: {e}")
        logger.warning("Agent list will be populated on first request")
        agent_map = {}

    overlay_manager = ProxyOverlayManager(client)

    # Initialize proxy tool bridge (only if client is working)
    if agent_map:
        initialize_proxy_bridge(client)
        logger.info("Proxy tool bridge initialized successfully.")
    
    # Log debug mode status
    if DEBUG_RAW_OUTPUT:
        logger.info(f"DEBUG_RAW_OUTPUT enabled - responses will be logged to {DEBUG_OUTPUT_FILE}")
        write_debug_output("=== LETTA PROXY DEBUG SESSION STARTED ===", "STARTUP")
    else:
        logger.info("DEBUG_RAW_OUTPUT disabled - no response logging")


@app.get("/v1/models")
async def list_models() -> Dict[str, Any]:
    """List all available Letta agents as OpenAI-compatible models.

    This endpoint provides a list of all available Letta agents in the format
    expected by OpenAI-compatible clients. Each agent is presented as a model
    that can be used in chat completion requests.

    Returns:
        OpenAI-compatible response with list of available models/agents
    """
    agents_page = await client.agents.list()
    agents = [agent async for agent in agents_page]
    data = [
        {
            "id": agent.name,
            "object": "model",
            "created": int(time.time()),
            "owned_by": "letta",
        }
        for agent in agents
    ]
    return {"object": "list", "data": data}


@app.get("/health")
async def health_check() -> Dict[str, Any]:
    """Health check endpoint for monitoring and diagnostics.

    Provides comprehensive health information including:
    - Overall system status
    - Letta server connection status
    - Configuration details
    - Number of loaded agents

    Returns:
        Health status information for monitoring systems
    """
    return {
        "status": "healthy",
        "letta_base_url": LETTA_BASE_URL,
        "letta_connected": client is not None,
        "agents_loaded": len(agent_map) if agent_map else 0
    }


if os.getenv("PROXY_DEBUG_SESSIONS") == "1":

    @app.get("/debug/sessions")
    async def debug_sessions() -> Dict[str, Any]:
        """Debug endpoint to inspect active proxy overlay sessions.

        This endpoint is only available when PROXY_DEBUG_SESSIONS=1 is set.
        It provides detailed information about active system prompt overlay
        sessions, including block IDs, content hashes, and session states.

        Returns:
            Debug information about active overlay sessions

        Raises:
            HTTPException: If overlay manager is unavailable (503)
        """
        if overlay_manager is None:
            raise HTTPException(status_code=503, detail="Overlay manager unavailable")
        return overlay_manager.debug_dump()


@app.post("/v1/chat/completions")
async def chat_completions(body: ChatCompletionRequest, request: Request) -> Any:
    """Main chat completions endpoint - OpenAI-compatible API for Letta agents.

    This is the core endpoint that handles all chat completion requests. It provides
    an OpenAI-compatible interface while leveraging Letta's advanced memory and tool
    capabilities through the proxy overlay system.

    The function performs the following key operations:
    1. **Agent Resolution**: Maps OpenAI model names to Letta agent IDs
    2. **System Prompt Management**: Applies system prompts via proxy overlay system
    3. **Tool Handling**: Processes tool definitions and tool call results
    4. **Message Processing**: Converts OpenAI format to Letta format
    5. **Response Generation**: Handles both streaming and non-streaming responses
    6. **Error Handling**: Comprehensive error handling with graceful fallbacks

    Args:
        body: OpenAI-compatible chat completion request
        request: FastAPI request object for header access

    Returns:
        OpenAI-compatible response (streaming or non-streaming)

    Raises:
        HTTPException: For various error conditions (agent not found, missing messages, etc.)

    The proxy overlay system ensures:
    - System prompts are stored in persistent Letta memory blocks
    - Unlimited system prompt lengths (50K+ characters supported)
    - Read-only protection to prevent agents from modifying system prompts
    - Smart block reuse to prevent database constraint violations
    - Efficient session-based caching and state management
    """
    # Handle model mapping - require exact agent name match
    agent_id = None
    agent_info = agent_map.get(body.model)
    if agent_info is None:
        # No fallbacks allowed - require exact match
        available_agents = list(agent_map.keys())
        logger.error(f"Model '{body.model}' not found. Available agents: {available_agents}")
        raise HTTPException(status_code=404, detail=f"Unknown model: {body.model}. Available models: {available_agents}")

    agent_id = agent_info.agent_id
    logger.info(f"Using agent: {body.model} for model: {body.model}")

    # Log request information
    if body.tools:
        logger.info(f"Request includes {len(body.tools)} tools: {[tool['function']['name'] for tool in body.tools]}")
    if not body.messages:
        raise HTTPException(status_code=400, detail="messages required")
    
    # Debug: Log the incoming request
    if DEBUG_RAW_OUTPUT:
        write_debug_output(f"INCOMING REQUEST:\nModel: {body.model}\nStream: {body.stream}\nMessages: {json.dumps(body.messages, ensure_ascii=False, indent=2)}", "REQUEST")

    system_content = _collect_system_content(body.messages)
    if REMOVE_SYSTEM_PROMPT:
        system_content = None

    if overlay_manager is None:
        raise HTTPException(status_code=500, detail="Overlay manager unavailable")

    headers_map = {k.lower(): v for k, v in request.headers.items()}
    session_id = overlay_manager.derive_session_id(agent_id, system_content, headers_map)
    overlay_changed, fallback_messages = await overlay_manager.apply_overlay(
        agent_id, session_id, system_content, project_id=agent_info.project_id
    )

    # Collect tool results from explicit tool_results field and trailing tool messages.
    # Tool results are sent back to Letta as approval messages (the proper SDK mechanism).
    tool_result_items: List[Dict[str, Any]] = []
    seen_tool_call_ids: set = set()
    if body.tool_results:
        for tool_result in body.tool_results:
            tool_call_id = tool_result.get("tool_call_id", "")
            seen_tool_call_ids.add(tool_call_id)
            tool_payload = tool_result.get("result", "")
            payload_str = json.dumps(tool_payload) if not isinstance(tool_payload, str) else tool_payload
            tool_result_items.append({"tool_call_id": tool_call_id, "content": payload_str})

    for tool_msg in _extract_trailing_tool_messages(body.messages):
        tool_call_id = tool_msg.get("tool_call_id", "")
        if tool_call_id and tool_call_id in seen_tool_call_ids:
            continue
        tool_content = _normalize_content(tool_msg.get("content"))
        tool_result_items.append({"tool_call_id": tool_call_id, "content": tool_content})
        if tool_call_id:
            seen_tool_call_ids.add(tool_call_id)

    latest_user_text = _extract_latest_user_message(body.messages)
    outbound_messages: list = [*fallback_messages]

    if tool_result_items:
        # Send tool results back using the SDK's MessageToolReturnCreate format.
        # The server had paused waiting for client-side tool results; this resumes it.
        tool_return_messages = _build_tool_return_messages(tool_result_items)
        outbound_messages.extend(tool_return_messages)
        # If there's also a user message, append it after the approvals
        if latest_user_text:
            outbound_messages.append(MessageCreate(role="user", content=latest_user_text))
    elif latest_user_text is not None:
        outbound_messages.append(MessageCreate(role="user", content=latest_user_text))

    # When sending approval messages (tool results), skip the pre-send drain.
    # The pending approval on the server IS the one we're about to respond to.
    has_tool_results = bool(tool_result_items)

    # Convert OpenAI tools to Letta client_tools format.
    # Client tools are passed per-request; the server pauses the agent run when
    # a client tool is called, and the client provides the result via an approval.
    letta_client_tools: Optional[List[Dict[str, Any]]] = None
    if body.tools:
        letta_client_tools = _openai_tools_to_client_tools(body.tools)
        logger.info(f"Passing {len(letta_client_tools)} client tools to agent {agent_id}")

    logger.info(
        "Forwarding to Letta agent=%s session=%s overlay_changed=%s new_outbound=%d stream=%s",
        body.model,
        session_id,
        overlay_changed,
        len(outbound_messages),
        body.stream,
    )

    if body.stream and not outbound_messages:
        async def empty_stream():
            stream_id = f"chatcmpl-{uuid.uuid4().hex}"
            primer = {
                "id": stream_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": body.model,
                "choices": [
                    {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}
                ],
            }
            # Fix: Preserve actual newlines in primer message content
            # Use Pydantic model for consistent JSON formatting
            primer_chunk = create_streaming_chunk(
                stream_id=primer["id"],
                model=primer["model"],
                content="",
                finish_reason=None
            )
            yield f"data: {primer_chunk.model_dump_json()}\n\n"
            yield "data: [DONE]\n\n"
            # Cleanup session state for empty stream
            cleanup_streaming_session(session_id)

        return StreamingResponse(
            empty_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    if body.stream:
        # EXACT implementation like reference
        resp_id = f"chatcmpl-{uuid.uuid4()}"  # OpenAI compatible ID

        async def stream_chunks():
            """Convert Letta streaming events to string chunks like reference implementation"""
            # Buffer to accumulate tool_call_message deltas so we can reconstruct
            # full arguments when the approval_request_message arrives.
            # Key: tool_call_id, Value: {"name": str, "arguments": str}
            pending_tool_calls: Dict[str, Dict[str, str]] = {}
            try:
                async for event in stream_messages(
                    client,
                    agent_id,
                    outbound_messages,
                    stream_tokens=True,
                    client_tools=letta_client_tools,
                    pre_drain=not has_tool_results,
                ):
                    # Handle tool calls - preserve our tool functionality
                    # V1 compatibility: Check for both legacy and structured events
                    event_type = None
                    if hasattr(event, 'message_type') and isinstance(event.message_type, str):
                        event_type = event.message_type
                    elif hasattr(event, 'tool_call'):
                        event_type = 'tool_call_message'
                    elif hasattr(event, 'tool_return'):
                        event_type = 'tool_return_message'
                    elif hasattr(event, 'content'):
                        event_type = 'assistant_message'
                    elif hasattr(event, 'reasoning'):
                        event_type = 'reasoning_message'

                    # Client tools: the server pauses and sends an approval_request_message
                    # containing the tool call(s) the agent wants to execute.  We translate
                    # this into an OpenAI tool_calls delta for the client.
                    if event_type == 'approval_request_message':
                        tool_calls_out = []
                        # May have a single tool_call or a list in tool_calls
                        tc_list = getattr(event, 'tool_calls', None)
                        tc_single = getattr(event, 'tool_call', None)
                        if tc_list and isinstance(tc_list, list):
                            items = tc_list
                        elif tc_single:
                            items = [tc_single]
                        else:
                            items = []

                        # Check if any tool call has null arguments (streaming ToolCallDelta).
                        # If so, fetch the full message from the server to get real arguments.
                        has_null_args = any(getattr(tc, 'arguments', None) is None for tc in items)
                        if has_null_args:
                            event_msg_id = getattr(event, 'id', None)
                            # Fetch the full approval_request from the REST API to get
                            # real arguments (streaming ToolCallDelta has arguments=None).
                            # Retry with brief delays since there can be a small race
                            # between the streaming event and REST API availability.
                            for attempt in range(3):
                                try:
                                    if attempt > 0:
                                        await asyncio.sleep(0.3 * attempt)
                                    recent_msgs = await client.agents.messages.list(
                                        agent_id=agent_id, limit=20
                                    )
                                    found = False
                                    async for msg in recent_msgs:
                                        if getattr(msg, 'message_type', None) != 'approval_request_message':
                                            continue
                                        msg_id = getattr(msg, 'id', None)
                                        if event_msg_id and msg_id != event_msg_id:
                                            continue
                                        full_tc_list = getattr(msg, 'tool_calls', None)
                                        full_tc_single = getattr(msg, 'tool_call', None)
                                        if full_tc_list and isinstance(full_tc_list, list):
                                            items = full_tc_list
                                        elif full_tc_single:
                                            items = [full_tc_single]
                                        logger.info("Fetched full tool call args from server (msg_id=%s, attempt=%d)", msg_id, attempt+1)
                                        found = True
                                        break
                                    if found:
                                        break
                                except Exception as fetch_err:
                                    logger.warning("Failed to fetch tool call args (attempt=%d): %s", attempt+1, fetch_err)
                            else:
                                logger.warning("Could not find approval_request msg_id=%s after retries", event_msg_id)

                        for idx, tc in enumerate(items):
                            tc_id = getattr(tc, 'tool_call_id', None) or getattr(tc, 'id', f"call_{idx}")
                            tc_name = getattr(tc, 'name', None) or ''
                            tc_args_raw = getattr(tc, 'arguments', None)
                            # In streaming, arguments may be None on the ToolCallDelta.
                            # Fall back to buffered tool_call_message deltas, then to '{}'.
                            if tc_args_raw is not None:
                                tc_args = tc_args_raw
                            elif tc_id in pending_tool_calls:
                                tc_args = pending_tool_calls[tc_id].get("arguments", "{}")
                                if not tc_name:
                                    tc_name = pending_tool_calls[tc_id].get("name", "")
                            else:
                                tc_args = '{}'
                            if not tc_name:
                                tc_name = ''
                            tool_calls_out.append({
                                "index": idx,
                                "id": tc_id,
                                "type": "function",
                                "function": {"name": tc_name, "arguments": tc_args},
                            })
                        if tool_calls_out:
                            chunk_resp = StreamingChunk(
                                id=resp_id,
                                object="chat.completion.chunk",
                                created=int(time.time()),
                                model=body.model,
                                choices=[Choice(
                                    index=0,
                                    delta=Delta(content="", tool_calls=tool_calls_out),
                                )]
                            )
                            yield f"data: {chunk_resp.model_dump_json()}\n\n"
                            final_chunk = StreamingChunk(
                                id=resp_id,
                                object="chat.completion.chunk",
                                created=int(time.time()),
                                model=body.model,
                                choices=[Choice(index=0, delta=Delta(content=""), finish_reason="tool_calls")]
                            )
                            yield f"data: {final_chunk.model_dump_json()}\n\n"
                            return
                        continue

                    # Buffer tool_call_message deltas to reconstruct full arguments.
                    # In streaming mode, the approval_request_message may arrive with
                    # arguments=None; the actual arguments are in prior tool_call_message events.
                    if event_type == 'tool_call_message':
                        tc_obj = getattr(event, 'tool_call', None)
                        if tc_obj:
                            tc_id = getattr(tc_obj, 'tool_call_id', None) or ''
                            tc_name = getattr(tc_obj, 'name', None) or ''
                            tc_args = getattr(tc_obj, 'arguments', None) or ''
                            if tc_id not in pending_tool_calls:
                                pending_tool_calls[tc_id] = {"name": tc_name, "arguments": tc_args}
                            else:
                                # Accumulate streaming argument deltas
                                if tc_name:
                                    pending_tool_calls[tc_id]["name"] = tc_name
                                pending_tool_calls[tc_id]["arguments"] += tc_args
                        continue

                    if event_type == 'tool_return_message':
                        # Legacy proxy tool path — still check for proxy_tool_call payloads
                        # for backward compatibility with any server-side proxy tools.
                        parsed_tool_call = _parse_proxy_tool_return(getattr(event, 'tool_return', None))
                        if parsed_tool_call:
                            chunk_resp = StreamingChunk(
                                id=resp_id,
                                object="chat.completion.chunk",
                                created=int(time.time()),
                                model=body.model,
                                choices=[Choice(
                                    index=0,
                                    delta=Delta(
                                        content="",
                                        tool_calls=[{
                                            "index": 0,
                                            "id": parsed_tool_call.get("tool_call_id"),
                                            "type": "function",
                                            "function": {
                                                "name": parsed_tool_call.get("function", {}).get("name"),
                                                "arguments": parsed_tool_call.get("function", {}).get("arguments", "{}"),
                                            },
                                        }]
                                    )
                                )]
                            )
                            yield f"data: {chunk_resp.model_dump_json()}\n\n"

                            final_chunk = StreamingChunk(
                                id=resp_id,
                                object="chat.completion.chunk",
                                created=int(time.time()),
                                model=body.model,
                                choices=[Choice(index=0, delta=Delta(content=""), finish_reason="tool_calls")]
                            )
                            yield f"data: {final_chunk.model_dump_json()}\n\n"
                            return
                        continue
                        
                    # Extract content from various event types and keep reasoning separate
                    chunk_content = ""
                    chunk_reasoning = ""
                    if event_type == 'assistant_message':
                        content = getattr(event, 'content', '') or ""
                        # V1 compatibility: Extract text from TextContent objects
                        if isinstance(content, list):
                            chunk_content = "".join(item.text for item in content if hasattr(item, 'text'))
                        else:
                            chunk_content = content  # Fallback for older format
                    elif event_type == 'reasoning_message':
                        chunk_reasoning = getattr(event, 'reasoning', '') or ""
                        if DEBUG_RAW_OUTPUT:
                            write_debug_output(f"RAW LETTA REASONING: {repr(chunk_reasoning)}", "LETTA_REASONING_RAW")
                            write_debug_output(f"AFTER UNESCAPE: {repr(unescape_content(chunk_reasoning))}", "AFTER_UNESCAPE")
                        # Use stateful processor for streaming-aware newline reconstruction
                        chunk_reasoning = process_streaming_chunk(session_id, chunk_reasoning)
                    elif event_type == 'stop_reason':
                        # Send final chunk
                        final_chunk = StreamingChunk(
                            id=resp_id,
                            object="chat.completion.chunk",
                            created=int(time.time()),
                            model=body.model,
                            choices=[Choice(index=0, delta=Delta(content=""), finish_reason=event.stop_reason)]
                        )
                        yield f"data: {final_chunk.model_dump_json()}\n\n"
                        return
                    else:
                        continue

                    if chunk_content:
                        if not isinstance(chunk_content, str):
                            logger.warning(f"Letta returned non-string assistant chunk: {type(chunk_content)}. Converting to str.")
                            chunk_content = str(chunk_content)

                        if not chunk_content.strip():
                            chunk_content = ""

                        if chunk_content:
                            chunk_resp = StreamingChunk(
                                id=resp_id,
                                object="chat.completion.chunk",
                                created=int(time.time()),
                                model=body.model,
                                choices=[Choice(index=0, delta=Delta(content=chunk_content))]
                            )
                            yield f"data: {chunk_resp.model_dump_json()}\n\n"

                    if chunk_reasoning:
                        if not isinstance(chunk_reasoning, str):
                            logger.warning(f"Letta returned non-string reasoning chunk: {type(chunk_reasoning)}. Converting to str.")
                            chunk_reasoning = str(chunk_reasoning)

                        chunk_resp = StreamingChunk(
                            id=resp_id,
                            object="chat.completion.chunk",
                            created=int(time.time()),
                            model=body.model,
                            choices=[Choice(index=0, delta=Delta(content="", reasoning=chunk_reasoning))]
                        )
                        yield f"data: {chunk_resp.model_dump_json()}\n\n"

                # Send final chunk if not already sent
                final_chunk = StreamingChunk(
                    id=resp_id,
                    object="chat.completion.chunk",
                    created=int(time.time()),
                    model=body.model,
                    choices=[Choice(index=0, delta=Delta(content=""), finish_reason="stop")]
                )
                yield f"data: {final_chunk.model_dump_json()}\n\n"
                
            except Exception as e:
                logger.error(f"Error during streaming: {e}", exc_info=True)
                error_chunk_content = f"Error processing stream: {e}"
                error_resp = StreamingChunk(
                    id=resp_id,
                    object="chat.completion.chunk",
                    created=int(time.time()),
                    model=body.model,
                    choices=[Choice(index=0, delta=Delta(content=error_chunk_content), finish_reason="stop")]
                )
                yield f"data: {error_resp.model_dump_json()}\n\n"

        async def event_stream():
            assert client is not None
            async for chunk in stream_chunks():
                yield chunk
            yield "data: [DONE]\n\n"
            # Cleanup session state after streaming completes
            cleanup_streaming_session(session_id)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )

    assert client is not None

    if not outbound_messages:
        openai_resp = {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": ""},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        return Response(content=json.dumps(openai_resp, ensure_ascii=False), media_type="application/json")

    resp = await send_messages(client, agent_id, outbound_messages, client_tools=letta_client_tools, pre_drain=not has_tool_results)
    assistant_messages: List[AssistantMessage] = []
    tool_calls: List[ToolCallMessage] = []
    tool_returns: List[ToolReturnMessage] = []
    approval_requests: list = []
    for m in resp.messages:
        if isinstance(m, AssistantMessage):
            assistant_messages.append(m)
        elif isinstance(m, ToolCallMessage):
            tool_calls.append(m)
        elif isinstance(m, ToolReturnMessage):
            tool_returns.append(m)
        elif hasattr(m, 'message_type') and getattr(m, 'message_type', None) == 'approval_request_message':
            approval_requests.append(m)

    response_message: Dict[str, Any]
    finish_reason = "stop"

    # Client tools: server pauses and returns an ApprovalRequestMessage with the
    # tool call(s) the agent wants to execute.  Translate to OpenAI tool_calls.
    if approval_requests:
        openai_tool_calls = []
        idx = 0
        for ar in approval_requests:
            tc_list = getattr(ar, 'tool_calls', None)
            tc_single = getattr(ar, 'tool_call', None)
            items = tc_list if (tc_list and isinstance(tc_list, list)) else ([tc_single] if tc_single else [])
            for tc in items:
                tc_id = getattr(tc, 'tool_call_id', None) or getattr(tc, 'id', f"call_{idx}")
                tc_name = getattr(tc, 'name', None) or getattr(tc, 'function', {}).get('name', '')
                tc_args_raw = getattr(tc, 'arguments', None)
                tc_args = tc_args_raw if tc_args_raw is not None else '{}'
                openai_tool_calls.append({
                    "index": idx,
                    "id": tc_id,
                    "type": "function",
                    "function": {"name": tc_name, "arguments": tc_args},
                })
                idx += 1
        response_message = {"role": "assistant", "content": "", "tool_calls": openai_tool_calls}
        finish_reason = "tool_calls"
    elif tool_calls and not tool_returns and not assistant_messages:
        response_message = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "index": i,
                    "id": tc.tool_call.tool_call_id,
                    "type": "function",
                    "function": {
                        "name": tc.tool_call.name,
                        "arguments": tc.tool_call.arguments,
                    },
                }
                for i, tc in enumerate(tool_calls)
            ],
        }
        finish_reason = "tool_calls"
    else:
        # Handle regular assistant messages - combine all content
        combined_content = ""
        for msg in assistant_messages:
            if msg.content:
                # Debug: Log each individual message content
                if DEBUG_RAW_OUTPUT:
                    write_debug_output(f"RAW ASSISTANT MESSAGE: {repr(msg.content)}", "NON_STREAMING_ASSISTANT")
                
                # For non-streaming responses, we can use the old unescape_content since there's no chunking
                text = unescape_content(msg.content or "")
                if DEBUG_RAW_OUTPUT:
                    write_debug_output(f"NON-STREAMING RAW CONTENT: {repr(msg.content or '')}", "NON_STREAMING_RAW")
                    write_debug_output(f"NON-STREAMING AFTER UNESCAPE: {repr(text)}", "NON_STREAMING_UNESCAPED")
                if combined_content:
                    combined_content += "\n\n"
                combined_content += text
        
        # Include reasoning from the first message that has it
        reasoning_content = ""
        for msg in assistant_messages:
            if hasattr(msg, 'reasoning') and msg.reasoning:
                reasoning_content = msg.reasoning
                break
        
        response_message = {
            "role": "assistant",
            "content": combined_content
        }
        
        # Keep reasoning separate so clients can render it distinctly
        if reasoning_content:
            response_message["reasoning"] = reasoning_content
        
        # Debug: Log the final combined content
        if DEBUG_RAW_OUTPUT:
            write_debug_output(f"FINAL COMBINED CONTENT: {repr(response_message['content'])}", "NON_STREAMING_FINAL")

    usage = resp.usage
    openai_resp = {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.model,
        "choices": [
            {
                "index": 0,
                "message": response_message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
        },
    }
    
    # Debug: Log the complete response being sent to client
    if DEBUG_RAW_OUTPUT:
        write_debug_output(f"COMPLETE NON-STREAMING RESPONSE: {json.dumps(openai_resp, ensure_ascii=False, indent=2)}", "NON_STREAMING_OUTPUT")
    
    # Preserve raw "<" and ">" in non-stream JSON too
    return Response(content=json.dumps(openai_resp, ensure_ascii=False), media_type="application/json")



if __name__ == "__main__":
    import uvicorn
    
    logger.info(f"Starting Letta Proxy Server on {PROXY_HOST}:{PROXY_PORT}")
    logger.info(f"Connecting to Letta server at {LETTA_BASE_URL}")
    
    uvicorn.run(
        app,
        host=PROXY_HOST,
        port=PROXY_PORT,
        log_level="info"
    )
