#!/usr/bin/env python3

import json

# Simulate our current response format
our_streaming_chunk = {
    "id": "chatcmpl-test",
    "object": "chat.completion.chunk",
    "created": 1700000000,
    "model": "Milo",
    "choices": [
        {
            "index": 0,
            "delta": {
                "content": "| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n",
                "reasoning": None
            },
            "finish_reason": None,
        }
    ],
}

print("=== OUR STREAMING CHUNK ===")
print(f"data: {json.dumps(our_streaming_chunk, ensure_ascii=False)}")
print()

# Standard OpenAI response (what Ollama might be mimicking)
standard_chunk = {
    "id": "chatcmpl-test",
    "object": "chat.completion.chunk", 
    "created": 1700000000,
    "model": "Milo",
    "choices": [
        {
            "index": 0,
            "delta": {
                "content": "| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n"
            },
            "finish_reason": None,
        }
    ],
}

print("=== STANDARD OPENAI CHUNK ===")
print(f"data: {json.dumps(standard_chunk, ensure_ascii=False)}")
print()

# Check if there are differences in JSON serialization
print("=== CONTENT COMPARISON ===")
print(f"Our content: {repr(our_streaming_chunk['choices'][0]['delta']['content'])}")
print(f"Standard content: {repr(standard_chunk['choices'][0]['delta']['content'])}")