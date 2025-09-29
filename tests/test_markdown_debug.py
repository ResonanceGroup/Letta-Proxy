#!/usr/bin/env python3
"""
Test script to debug markdown table formatting issues in Letta Proxy
Prints raw responses to debug_output.txt for analysis
"""
import os
import sys
import json
import asyncio
import httpx
import requests
from typing import Dict, Any

# Add the current directory to Python path to import main
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Output file for debugging
DEBUG_OUTPUT_FILE = "debug_output.txt"

def write_to_debug_file(content: str):
    """Write content to debug output file"""
    with open(DEBUG_OUTPUT_FILE, "a", encoding="utf-8") as f:
        f.write(content + "\n")

# Simple test content - Milo will respond with a markdown table
TEST_CONTENT = "Go"

def print_debug_info(stage: str, data: Any, description: str = ""):
    """Print debug information with clear formatting"""
    print(f"\n{'='*60}")
    print(f"STAGE: {stage}")
    if description:
        print(f"DESCRIPTION: {description}")
    print(f"{'='*60}")

    if isinstance(data, str):
        print("STRING DATA:")
        print(repr(data))  # Show actual string representation
        print("\nFORMATTED DATA:")
        print(data)  # Show formatted version
    else:
        print("DATA:")
        print(json.dumps(data, indent=2, ensure_ascii=False))

    print(f"{'='*60}")

async def test_proxy_with_httpx(agent_name: str):
    """Test using httpx to simulate OpenAI client requests"""
    print("\n🧪 Testing with HTTPX (simulating OpenAI client)")

    # Clear the debug file at the start
    with open(DEBUG_OUTPUT_FILE, "w") as f:
        f.write("=== NEW DEBUG SESSION ===\n")

    # Test content - simple "Go" to get Milo's markdown table response
    test_messages = [
        {"role": "user", "content": TEST_CONTENT}
    ]

    # Simulate OpenAI API request
    request_data = {
        "model": agent_name,
        "messages": test_messages,
        "stream": True,
        "max_tokens": 1000
    }

    print_debug_info("ORIGINAL REQUEST", request_data, "What we're sending to the proxy")

    try:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                "http://localhost:8284/v1/chat/completions",
                json=request_data,
                headers={"Content-Type": "application/json"}
            ) as response:
                print_debug_info("RESPONSE STATUS", {
                    "status_code": response.status_code,
                    "headers": dict(response.headers)
                }, "Response headers and status")

                print("\n📡 STREAMING RESPONSE:")
                print("-" * 40)

                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]  # Remove "data: " prefix
                        if data_str.strip() == "[DONE]":
                            break

                        try:
                            chunk_data = json.loads(data_str)
                            print_debug_info("STREAM CHUNK", chunk_data, "Individual streaming chunk")

                            if "choices" in chunk_data and len(chunk_data["choices"]) > 0:
                                delta = chunk_data["choices"][0].get("delta", {})
                                if "content" in delta:
                                    content = delta["content"]
                                    print_debug_info("CONTENT DELTA", content, "Extracted content from chunk")

                        except json.JSONDecodeError as e:
                            print(f"❌ JSON decode error: {e}")
                            print(f"Raw data: {repr(data_str)}")

    except Exception as e:
        print(f"❌ Error during httpx test: {e}")
        import traceback
        traceback.print_exc()

def test_proxy_with_requests(agent_name: str):
    """Test using requests for non-streaming responses"""
    print("\n🧪 Testing with Requests (non-streaming)")

    test_messages = [
        {"role": "user", "content": TEST_CONTENT}
    ]

    request_data = {
        "model": agent_name,
        "messages": test_messages,
        "stream": False,
        "max_tokens": 1000
    }

    print_debug_info("ORIGINAL REQUEST", request_data, "Non-streaming request")

    try:
        response = requests.post(
            "http://localhost:8284/v1/chat/completions",
            json=request_data,
            headers={"Content-Type": "application/json"}
        )

        print_debug_info("RESPONSE STATUS", {
            "status_code": response.status_code,
            "headers": dict(response.headers)
        }, "Non-streaming response info")

        if response.status_code == 200:
            response_data = response.json()
            print_debug_info("FULL RESPONSE", response_data, "Complete non-streaming response")

            if "choices" in response_data and len(response_data["choices"]) > 0:
                message = response_data["choices"][0].get("message", {})
                if "content" in message:
                    content = message["content"]
                    print_debug_info("RESPONSE CONTENT", content, "Final content from non-streaming response")

    except Exception as e:
        print(f"❌ Error during requests test: {e}")
        import traceback
        traceback.print_exc()

async def get_available_agents():
    """Get list of available agents from the proxy server"""
    try:
        response = requests.get("http://localhost:8284/v1/models")
        if response.status_code == 200:
            data = response.json()
            agents = [model["id"] for model in data.get("data", [])]
            return agents
        else:
            print(f"❌ Failed to get agents: {response.status_code}")
            return []
    except Exception as e:
        print(f"❌ Error getting agents: {e}")
        return []

async def main():
    """Run all tests"""
    print("🚀 Starting Markdown Table Debug Test")
    print(f"Sending message: {TEST_CONTENT}")
    print("\n" + "="*80)

    # First, discover available agents
    print("\n🔍 Discovering available agents...")
    available_agents = await get_available_agents()

    if not available_agents:
        print("❌ No agents available for testing!")
        print("💡 Make sure your Letta server is running and has agents configured.")
        return

    print(f"✅ Found agents: {available_agents}")

    # Use the first available agent for testing
    test_agent = available_agents[0]
    print(f"🎯 Using agent: {test_agent}")

    # Test non-streaming first (easier to debug)
    test_proxy_with_requests(test_agent)

    # Test streaming
    await test_proxy_with_httpx(test_agent)

    print("\n✅ Debug test completed!")
    print("\n💡 Look for where the markdown formatting gets corrupted:")
    print("   - Check if newlines (\\n) become (\\\\n)")
    print("   - Check if table formatting is preserved")
    print("   - Look for any JSON encoding issues")

if __name__ == "__main__":
    asyncio.run(main())