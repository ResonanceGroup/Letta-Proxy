# Letta OpenAI Proxy

A FastAPI server that provides an OpenAI-compatible API interface for [Letta](https://docs.letta.com/) V1 agents, enabling seamless integration with existing OpenAI-based applications while leveraging Letta's advanced memory and tool capabilities.

## ✨ Features

### Core Functionality
- **OpenAI API Compatibility**: Full compatibility with OpenAI chat completions API
- **Letta V1 Support**: Native support for latest Letta V1 agent architecture
- **Streaming & Non-Streaming**: Both real-time streaming and standard response modes
- **Tool Calling**: Dynamic tool synchronization between OpenAI and Letta formats
- **Agent Discovery**: Automatic discovery and mapping of Letta agents as OpenAI models

### Advanced Features
- **System Prompt Management**: Intelligent system prompt overlay via Letta memory blocks
- **Session Management**: Per-session state isolation for concurrent users
- **Error Handling**: Comprehensive error handling with graceful fallbacks
- **Health Monitoring**: Built-in health check and debugging endpoints
- **Environment Flexibility**: Support for local servers, Letta Cloud, and custom deployments

## 🚀 Quick Start

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd Letta-Proxy

# Install dependencies
pip install -r requirements.txt
```

### Configuration

Create a `.env` file in the project root:

```bash
# Copy the example configuration
cp env.example .env
```

Edit `.env` with your settings (see [Configuration](#🛠️-configuration) section for details).

### Running the Server

```bash
# Option 1: Run directly with Python (uses environment variables)
python main.py

# Option 2: Run with uvicorn (manual host/port configuration)
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# The server will be available at:
# http://localhost:8000 (or your configured PROXY_HOST:PROXY_PORT)
```

## 📖 API Documentation

### Endpoints

#### Chat Completions
```
POST /v1/chat/completions
```

OpenAI-compatible chat completions endpoint supporting both streaming and non-streaming responses.

**Request Body:**
```json
{
  "model": "YourLettaAgentName",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello!"}
  ],
  "stream": false,
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Get current weather",
        "parameters": {
          "type": "object",
          "properties": {
            "location": {"type": "string"}
          }
        }
      }
    }
  ]
}
```

#### Model Listing
```
GET /v1/models
```

Lists all available Letta agents as OpenAI-compatible models.

**Response:**
```json
{
  "object": "list",
  "data": [
    {
      "id": "YourAgentName",
      "object": "model",
      "created": 1647895200,
      "owned_by": "letta"
    }
  ]
}
```

#### Health Check
```
GET /health
```

Returns server health status and configuration information.

## 🛠️ Configuration

### Environment Variables

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `LETTA_BASE_URL` | Letta server URL | `http://localhost:8283` | Yes |
| `LETTA_API_KEY` | Letta API key | - | For cloud/auth |
| `LETTA_PROJECT` | Letta project name | `default-project` | For cloud |
| `PROXY_HOST` | Proxy server bind address | `0.0.0.0` | No |
| `PROXY_PORT` | Proxy server port | `8000` | No |
| `REMOVE_SYSTEM_PROMPT` | Omit system prompts | `false` | No |
| `DEBUG_RAW_OUTPUT` | Enable debug logging | `false` | No |
| `PROXY_DEBUG_SESSIONS` | Enable session debugging | `0` | No |

### Server Types

#### Example Configurations

**Letta V1 Cloud:**
```env
LETTA_BASE_URL=https://api.letta.com
LETTA_API_KEY=sk-let-your-api-key-here
LETTA_PROJECT=your-project-name
PROXY_HOST=0.0.0.0
PROXY_PORT=8000
```

**Local Letta V1 Server:**
```env
LETTA_BASE_URL=http://localhost:8283
PROXY_HOST=127.0.0.1
PROXY_PORT=8080
# LETTA_API_KEY not required for local servers
```

**Custom Server with Debug:**
```env
LETTA_BASE_URL=https://your-custom-letta-server.com
LETTA_API_KEY=your-api-key-if-required
PROXY_HOST=0.0.0.0
PROXY_PORT=9000
DEBUG_RAW_OUTPUT=true
```

## 🧪 Testing

### Basic Testing

```bash
# Health check
curl http://localhost:8000/health

# List available models/agents
curl http://localhost:8000/v1/models

# Non-streaming chat completion
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "YourAgentName",
    "messages": [
      {"role": "user", "content": "What is 2+2?"}
    ]
  }'

# Streaming chat completion
curl -N -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "YourAgentName", 
    "messages": [
      {"role": "user", "content": "Tell me a story"}
    ],
    "stream": true
  }'
```

### Tool Calling Test

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "YourAgentName",
    "messages": [
      {"role": "user", "content": "What is the weather in San Francisco?"}
    ],
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "get_weather",
          "description": "Get current weather for a location",
          "parameters": {
            "type": "object",
            "properties": {
              "location": {
                "type": "string",
                "description": "The city and state, e.g. San Francisco, CA"
              }
            },
            "required": ["location"]
          }
        }
      }
    ]
  }'
```

## 🏗️ Architecture

### System Overview
```
OpenAI Client → FastAPI Proxy → Letta V1 SDK → Letta V1 Agent
                      ↓
OpenAI Client ← JSON Response ← V1 Translation ← V1 Events
```

### Key Components

- **API Layer**: FastAPI endpoints with OpenAI compatibility
- **Translation Layer**: Converts between OpenAI and Letta V1 formats  
- **Event Processor**: Handles Letta V1 structured events and TextContent arrays
- **Tool Bridge**: Dynamic tool synchronization between formats
- **Proxy Overlay**: System prompt management via Letta memory blocks

### V1 Compatibility

The proxy is specifically designed for Letta V1 agents and includes:

- **V1 Event Processing**: Handles `LettaMessageUnion` structured events
- **TextContent Arrays**: Processes V1 content format properly
- **Tool Call Updates**: Compatible with V1 tool calling architecture
- **Agent Discovery**: Works with V1 agent listing format

## 🔧 Integration Examples

### Open WebUI Integration

1. In Open WebUI settings, add a new OpenAI-compatible endpoint:
   - **API URL**: `http://localhost:8000/v1`
   - **API Key**: Any value (not validated by proxy)

2. Select your Letta agent from the model dropdown

3. Start chatting - the proxy will handle all translation automatically

### Python Client Integration

```python
import openai

# Configure OpenAI client to use the proxy
client = openai.OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="any-value"  # Not validated by proxy
)

# Use standard OpenAI API calls
response = client.chat.completions.create(
    model="YourLettaAgentName",
    messages=[
        {"role": "user", "content": "Hello, Letta agent!"}
    ],
    stream=True
)

for chunk in response:
    print(chunk.choices[0].delta.content, end="")
```

## 🐛 Troubleshooting

### Common Issues

#### "Unknown model" Error
- **Cause**: Agent name doesn't match exactly
- **Solution**: Check available agents with `GET /v1/models`

#### Connection Errors
- **Cause**: Incorrect `LETTA_BASE_URL` or server not running
- **Solution**: Verify Letta server is accessible at configured URL

#### Authentication Errors  
- **Cause**: Invalid or missing `LETTA_API_KEY`
- **Solution**: Check API key validity in Letta dashboard

#### Streaming Issues
- **Cause**: Client not handling Server-Sent Events properly
- **Solution**: Use `-N` flag with curl or proper SSE handling in client

### Debug Mode

Enable detailed logging by setting:
```env
DEBUG_RAW_OUTPUT=true
```

This will log all Letta responses to `letta_proxy_debug.txt` for analysis.

### Health Monitoring

Check server status:
```bash
curl http://localhost:8000/health
```

Response includes:
- Server health status
- Letta server connectivity  
- Number of loaded agents
- Configuration details

## 📋 Requirements

- Python 3.8+
- Letta V1 server (local or cloud)
- FastAPI and dependencies (see `requirements.txt`)

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## 📄 License

See `LICENSE` file for details.

## 🔗 Links

- [Letta Documentation](https://docs.letta.com/)
- [OpenAI API Reference](https://platform.openai.com/docs/api-reference)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
