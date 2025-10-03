# Letta Proxy - Product Context

## Why This Project Exists

The Letta Proxy Server provides an OpenAI-compatible API interface for Letta agents, enabling seamless integration with existing OpenAI-based applications while leveraging Letta's advanced memory and tool capabilities. This allows users to use Letta agents with applications like Open WebUI without modifying the client applications.

## Problems It Solves

1. **Compatibility Gap**: OpenAI-based applications cannot directly use Letta agents due to API differences
2. **Memory Management**: Provides system prompt overlay management through Letta memory blocks for unlimited prompt lengths
3. **Tool Integration**: Synchronizes and executes tools between OpenAI-compatible clients and Letta agents
4. **Streaming Support**: Enables real-time streaming responses compatible with OpenAI's API format

## How It Should Work

1. **Agent Discovery**: Automatically discover available Letta agents and present them as OpenAI models
2. **Request Translation**: Convert OpenAI API requests to Letta agent interactions
3. **Memory Management**: Apply system prompts via persistent Letta memory blocks
4. **Tool Handling**: Process tool definitions and tool call results
5. **Response Streaming**: Provide OpenAI-compatible streaming responses with proper markdown rendering (including tables, formatting, and newlines)
6. **Error Handling**: Comprehensive error handling with graceful fallbacks