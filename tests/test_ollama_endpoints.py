#!/usr/bin/env python3
"""
Test script to verify Ollama endpoints are working on port 11433
"""
import json
import requests
import sys

def test_ollama_models_endpoint():
    """Test the models endpoint"""
    print("🧪 Testing Ollama models endpoint on port 11433...")

    url = "http://localhost:11433/api/tags"
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 200:
            models_data = response.json()
            print("✅ Models endpoint is working!"            print(f"📋 Status: {response.status_code}")

            if 'models' in models_data:
                models = models_data['models']
                print(f"🔍 Found {len(models)} models:")
                for model in models:
                    name = model.get('name', 'Unknown')
                    size = model.get('size', 0)
                    modified = model.get('modified_at', 'Unknown')
                    print(f"   • {name} ({size} bytes, modified: {modified})")
            else:
                print("⚠️  No 'models' key in response")
                print(f"Response: {json.dumps(models_data, indent=2)}")

            return True
        else:
            print(f"❌ Models endpoint failed with status {response.status_code}")
            print(f"Response: {response.text}")
            return False

    except requests.RequestException as e:
        print(f"❌ Models endpoint request error: {e}")
        return False
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON response from models endpoint: {e}")
        return False

def test_ollama_openai_endpoint():
    """Test the OpenAI compatible endpoint"""
    print("\n🧪 Testing Ollama OpenAI compatible endpoint on port 11433...")

    url = "http://localhost:11433/v1/chat/completions"
    headers = {"Content-Type": "application/json"}

    # Simple test request
    data = {
        "model": "llama2",  # Common default model
        "messages": [
            {"role": "user", "content": "Hello, just testing if you're working. Please respond with 'Ollama is working!'"}
        ],
        "max_tokens": 50,
        "stream": False
    }

    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)

        if response.status_code == 200:
            response_data = response.json()
            print("✅ OpenAI compatible endpoint is working!"            print(f"📋 Status: {response.status_code}")

            if 'choices' in response_data and len(response_data['choices']) > 0:
                message = response_data['choices'][0]['message']['content']
                print(f"🤖 Response: {message}")

                # Check if we got a reasonable response
                if 'working' in message.lower() or len(message) > 0:
                    print("✅ Got valid response content")
                    return True
                else:
                    print("⚠️  Got empty or unexpected response")
                    return False
            else:
                print("⚠️  No choices in response")
                print(f"Response: {json.dumps(response_data, indent=2)}")
                return False

        elif response.status_code == 404:
            print("❌ OpenAI compatible endpoint not found (404)")
            print("This might mean the OpenAI compatibility mode isn't enabled")
            return False
        elif response.status_code == 400:
            error_data = response.json()
            print("❌ Bad request (400) - possibly model not found")
            print(f"Error: {error_data.get('error', {}).get('message', 'Unknown error')}")
            return False
        else:
            print(f"❌ OpenAI endpoint failed with status {response.status_code}")
            print(f"Response: {response.text}")
            return False

    except requests.RequestException as e:
        print(f"❌ OpenAI endpoint request error: {e}")
        return False
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON response from OpenAI endpoint: {e}")
        return False

def test_ollama_generate_endpoint():
    """Test the basic generate endpoint as a fallback"""
    print("\n🧪 Testing Ollama generate endpoint (fallback test)...")

    url = "http://localhost:11433/api/generate"
    headers = {"Content-Type": "application/json"}

    data = {
        "model": "llama2",
        "prompt": "Hello, just testing if you're working. Please respond with 'Ollama is working!'",
        "stream": False
    }

    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)

        if response.status_code == 200:
            response_data = response.json()
            print("✅ Generate endpoint is working!"            print(f"📋 Status: {response.status_code}")

            if 'response' in response_data:
                message = response_data['response']
                print(f"🤖 Response: {message}")

                if 'working' in message.lower() or len(message) > 0:
                    print("✅ Got valid response content")
                    return True
                else:
                    print("⚠️  Got empty or unexpected response")
                    return False
            else:
                print("⚠️  No 'response' key in generate response")
                return False

        else:
            print(f"❌ Generate endpoint failed with status {response.status_code}")
            return False

    except requests.RequestException as e:
        print(f"❌ Generate endpoint request error: {e}")
        return False
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON response from generate endpoint: {e}")
        return False

def main():
    """Run all tests"""
    print("🚀 Starting Ollama endpoint tests on port 11433\n")

    # Test models endpoint first
    models_ok = test_ollama_models_endpoint()

    # Test OpenAI compatible endpoint
    openai_ok = test_ollama_openai_endpoint()

    # If OpenAI endpoint fails, try the basic generate endpoint
    generate_ok = False
    if not openai_ok:
        generate_ok = test_ollama_generate_endpoint()

    # Summary
    print("\n" + "="*50)
    print("📊 TEST SUMMARY:")
    print(f"   Models endpoint: {'✅ PASS' if models_ok else '❌ FAIL'}")
    print(f"   OpenAI endpoint: {'✅ PASS' if openai_ok else '❌ FAIL'}")
    print(f"   Generate endpoint: {'✅ PASS' if generate_ok else '❌ FAIL'}")

    if models_ok and (openai_ok or generate_ok):
        print("\n🎉 Ollama is working! You can proceed with your setup.")
        return True
    else:
        print("\n❌ Some tests failed. Please check your Ollama configuration.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)