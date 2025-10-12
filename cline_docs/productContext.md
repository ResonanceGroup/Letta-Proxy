# Product Context

## Why This Project Exists

The Letta OpenAI Proxy provides a compatibility layer between OpenAI API clients and Letta agent servers. It allows existing applications and tools that expect OpenAI API endpoints to seamlessly work with Letta agents without requiring code changes.

## What Problems It Solves

1. **API Compatibility**: Many existing tools and applications are built for OpenAI's API format. This proxy allows them to work with Letta agents without modification.

2. **Agent Integration**: Provides a simple way to expose Letta agents as OpenAI-compatible models that can be used by any OpenAI client.

3. **Streaming Support**: Enables real-time streaming responses from Letta agents through the familiar OpenAI streaming API format.

4. **Tool Calling**: Maintains compatibility for function calling and tool usage patterns expected by OpenAI clients.

## How It Should Work

1. **Request Flow**: Client sends OpenAI-formatted request to proxy → proxy translates to Letta API call → Letta agent processes → proxy converts response back to OpenAI format → client receives expected response.

2. **Model Listing**: GET /v1/models returns available Letta agents as OpenAI model objects.

3. **Chat Completions**: POST /v1/chat/completions forwards messages to specified Letta agent and returns assistant responses in OpenAI format.

4. **Streaming**: When stream=true, responses are sent as Server-Sent Events with OpenAI-compatible formatting.

5. **Tool Calls**: Function calling requests from clients are properly routed through Letta's tool system and responses formatted back to OpenAI expectations.