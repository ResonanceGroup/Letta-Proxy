# Technical Context

## Technologies used
- **Python 3.8+**: Core programming language
- **FastAPI**: Web framework for building the API endpoints
- **Uvicorn**: ASGI server for running the FastAPI application
- **letta-client**: Official Letta Python SDK for API communication
- **Pydantic**: Data validation and serialization
- **AsyncIO**: Asynchronous programming for better performance

## Development setup
### Installation
```bash
pip install -r requirements.txt
```

### Running the application
```bash
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Testing
```bash
# Non-streaming test
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"Milo","messages":[{"role":"user","content":"What'\''s two plus two?"}]}'

# Streaming test
curl -N -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"Milo","messages":[{"role":"user","content":"Hello"}],"stream":true}'
```

## Technical constraints
1. **Hardcoded Configuration**: Letta server URL is hardcoded in the application
2. **Global State**: Client and agent mapping stored as global variables
3. **No Authentication**: Currently no authentication mechanism implemented
4. **Limited Error Recovery**: Basic error handling without retry logic
5. **Single Server**: Designed to work with one Letta server instance

## Letta SDK Integration
### Client Configuration
- **Local Server**: `AsyncLetta(base_url="http://localhost:8283")`
- **Letta Cloud**: `AsyncLetta(token="LETTA_API_KEY", project="default-project")`
- **Async Support**: Uses `AsyncLetta` for non-blocking operations

### Key SDK Features Used
- `client.agents.list()` - Retrieve available agents
- `client.agents.messages.create()` - Send messages to agents
- `client.agents.messages.create_stream()` - Streaming responses
- Message types: `MessageCreate`, `AssistantMessage`, `ToolCallMessage`, `ToolReturnMessage`

## Performance Considerations
- Async/await patterns for concurrent request handling
- Streaming support for real-time responses
- Connection pooling through HTTP client
- Efficient message format conversion

## Current Debugging Session - Technical Implementation

### 🎯 **Newline Handling Implementation**

#### **Core Challenge**
Open WebUI displays literal `\n` characters instead of line breaks in markdown tables when using streaming responses.

#### **Root Cause**
JSON serialization converts actual newlines (`\n`) to escaped newlines (`\\n`) in JSON strings, breaking markdown rendering.

#### **Technical Solution**

**Pydantic Model Approach**:
```python
from pydantic import BaseModel

class Delta(BaseModel):
    content: str
    reasoning: None

class Choice(BaseModel):
    index: int
    delta: Delta
    logprobs: None
    finish_reason: None

class StreamingChunk(BaseModel):
    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: list[Choice]

# Usage
chunk = StreamingChunk(...)
yield f"data: {chunk.model_dump_json()}\n\n"
```

**Content Processing**:
```python
def unescape_content(content: str) -> str:
    \"\"\"Convert Letta's double-escaped newlines to actual newlines\"\"\"
    if not content:
        return content
    return content.replace('\\n', '\n')  # \\n → \n
```

#### **Data Flow**
1. **Letta Agent** → sends `\\n` (escaped newlines)
2. **`unescape_content()`** → converts `\\n` → `\n` (correct)
3. **Pydantic Model** → preserves actual newlines in content field
4. **`model_dump_json()`** → clean JSON with proper newlines
5. **Open WebUI** → receives actual newlines for proper markdown rendering

#### **Key Technical Decisions**

1. **✅ Pydantic Models**: Replaced manual JSON dicts with typed models
2. **✅ Model JSON Serialization**: Used `model_dump_json()` instead of `json.dumps()`
3. **✅ Content Unescaping**: Preserved `unescape_content()` function (it was correct)
4. **✅ Simplified Approach**: Removed complex regex post-processing
5. **❌ Error Handling Bug**: Missing function reference causing crashes

#### **Current Status**
- **✅ Core Implementation**: Pydantic model approach working correctly
- **✅ Multiple Locations**: Applied to all streaming JSON serialization points
- **❌ Critical Bug**: `NameError` in error handling section
- **❌ Stream Termination**: Still terminates after first character

#### **Debugging Infrastructure**
- **Enhanced Logging**: Clean debug output with `RAW_DELTA_CONTENT` entries
- **Pipeline Tracing**: Complete visibility into data transformation steps
- **Error Analysis**: Detailed error logging and stack traces
- **Comparative Analysis**: Working implementation reference for validation

#### **Technical Debt Identified**
- **Function Management**: Poor function lifecycle management leading to missing references
- **Error Handling**: Insufficient error boundary management
- **Testing**: Limited testing of error conditions and edge cases
- **Complexity**: Over-engineering with regex-based solutions

#### **Next Steps**
1. **Fix Missing Function**: Add missing `fix_json_newlines()` or update error handling
2. **Root Cause Investigation**: Determine why streaming terminates early
3. **Alternative Implementation**: Consider adopting wsargent/letta-openai-proxy pattern
4. **Final Testing**: Verify complete solution works end-to-end
5. **Documentation**: Update technical documentation with solution details

#### **Risk Assessment**
- **High Risk**: Current implementation crashes on errors
- **Medium Risk**: Streaming reliability issues affecting user experience
- **Low Risk**: Core functionality works (non-streaming mode operational)