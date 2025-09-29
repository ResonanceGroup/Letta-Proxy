#!/usr/bin/env python3
"""
Test to verify our streaming format matches OpenAI specification exactly
"""
import json
import time
import requests

def test_streaming_format():
    """Test that our streaming format matches OpenAI spec"""
    print("Testing Letta Proxy streaming format compliance...")
    
    # Test request with markdown table
    url = "http://localhost:8000/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    data = {
        "model": "Milo",
        "messages": [
            {"role": "user", "content": "Create a simple markdown table with columns Name, Age, City"}
        ],
        "stream": True
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, stream=True)
        
        if response.status_code != 200:
            print(f"❌ Request failed with status {response.status_code}")
            return False
            
        chunks_received = 0
        first_chunk = None
        final_chunk = None
        
        for line in response.iter_lines():
            if line:
                line = line.decode('utf-8')
                if line.startswith('data: '):
                    json_str = line[6:]  # Remove 'data: ' prefix
                    if json_str.strip() == '[DONE]':
                        break
                        
                    try:
                        chunk = json.loads(json_str)
                        chunks_received += 1
                        
                        if chunks_received == 1:
                            first_chunk = chunk
                        final_chunk = chunk
                        
                        # Verify OpenAI format compliance
                        required_fields = ['id', 'object', 'created', 'model', 'choices']
                        for field in required_fields:
                            if field not in chunk:
                                print(f"❌ Missing required field: {field}")
                                return False
                        
                        # Verify object type
                        if chunk['object'] != 'chat.completion.chunk':
                            print(f"❌ Wrong object type: {chunk['object']}")
                            return False
                        
                        # Verify choices structure
                        for choice in chunk['choices']:
                            choice_required = ['index', 'delta', 'logprobs', 'finish_reason']
                            for field in choice_required:
                                if field not in choice:
                                    print(f"❌ Missing choice field: {field}")
                                    return False
                        
                        print(f"✅ Chunk {chunks_received}: Valid format")
                        
                    except json.JSONDecodeError as e:
                        print(f"❌ Invalid JSON in chunk: {e}")
                        return False
        
        print(f"\n✅ Received {chunks_received} valid chunks")
        print(f"✅ Format matches OpenAI specification")
        
        # Show first chunk structure
        if first_chunk:
            print(f"\n📋 First chunk structure:")
            print(json.dumps(first_chunk, indent=2))
        
        return True
        
    except requests.RequestException as e:
        print(f"❌ Request error: {e}")
        return False

if __name__ == "__main__":
    success = test_streaming_format()
    if success:
        print("\n🎉 All tests passed! Format is OpenAI compliant.")
    else:
        print("\n❌ Tests failed. Format needs fixes.")