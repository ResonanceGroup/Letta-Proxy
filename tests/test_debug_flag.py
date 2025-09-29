#!/usr/bin/env python3
"""
Simple test to verify the DEBUG_RAW_OUTPUT flag is working correctly
"""
import os
import sys

# Add the current directory to Python path to import main
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_debug_flag():
    """Test that the debug flag is properly read from environment"""
    # Import after setting up path
    from main import DEBUG_RAW_OUTPUT, DEBUG_OUTPUT_FILE, write_debug_output
    
    print(f"DEBUG_RAW_OUTPUT: {DEBUG_RAW_OUTPUT}")
    print(f"DEBUG_OUTPUT_FILE: {DEBUG_OUTPUT_FILE}")
    
    # Test the debug function
    if DEBUG_RAW_OUTPUT:
        print("✅ Debug mode is ENABLED - testing write function...")
        write_debug_output("This is a test debug message", "TEST")
        print(f"✅ Debug message written to {DEBUG_OUTPUT_FILE}")
        
        # Check if file exists and has content
        if os.path.exists(DEBUG_OUTPUT_FILE):
            with open(DEBUG_OUTPUT_FILE, 'r', encoding='utf-8') as f:
                content = f.read()
                if "This is a test debug message" in content:
                    print("✅ Debug file contains test message - functionality working!")
                else:
                    print("❌ Debug file exists but doesn't contain test message")
        else:
            print("❌ Debug file was not created")
    else:
        print("ℹ️  Debug mode is DISABLED - no debug output will be written")
        write_debug_output("This should not be written", "TEST")
        print("✅ Debug function called but no file should be created (as expected)")

if __name__ == "__main__":
    print("🧪 Testing DEBUG_RAW_OUTPUT flag functionality...")
    test_debug_flag()
    print("🏁 Test completed!")