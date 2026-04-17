"""
Compatibility shim for letta-client SDK version differences.

The proxy was originally built against an older letta-client SDK.
In letta-client 1.10.2+, some imports moved to deeper submodules
and types were renamed.

This module re-exports everything under the names main.py expects.
"""

# MessageCreate → MessageCreateParam (TypedDict, used as dict)
try:
    from letta_client import MessageCreate
except ImportError:
    # In 1.10.2+, it's a TypedDict in types
    from letta_client.types import MessageCreateParam as MessageCreate

# TextContent → TextContentParam
try:
    from letta_client.types import TextContent
except ImportError:
    from letta_client.types.agents.text_content_param import TextContentParam as TextContent

# AssistantMessage moved to agents submodule
try:
    from letta_client.types import AssistantMessage
except ImportError:
    from letta_client.types.agents.assistant_message import AssistantMessage

# ToolCallMessage moved to agents submodule
try:
    from letta_client.types import ToolCallMessage
except ImportError:
    from letta_client.types.agents.tool_call_message import ToolCallMessage

# ToolReturnMessage exists in both locations
try:
    from letta_client.types import ToolReturnMessage
except ImportError:
    from letta_client.types.agents.tool_return_message import ToolReturnMessage

__all__ = [
    "MessageCreate",
    "TextContent",
    "AssistantMessage",
    "ToolCallMessage",
    "ToolReturnMessage",
]
