#!/usr/bin/env python3
"""
Test script to verify the newline handling fix for Letta Proxy streaming.

This script demonstrates that the fix correctly handles newline characters
in streaming content for proper markdown rendering.
"""

import json
from streaming_models import (
    create_streaming_chunk,
    unescape_content
)


def test_newline_handling():
    """Test the newline handling fix."""

    # Simulate what Letta sends (double-escaped newlines)
    letta_content = "\\n| Name | Age |\\n|------|-----|\\n| John | 25  |\\n| Jane | 30  |\\n"

    print("=== NEWLINE HANDLING TEST ===")
    print(f"Original Letta content: {repr(letta_content)}")

    # Step 1: Unescape content (\\n -> \\n)
    unescaped = unescape_content(letta_content)
    print(f"After unescaping: {repr(unescaped)}")

    # Step 2: Create streaming chunk using Pydantic model
    chunk = create_streaming_chunk("test-123", "Milo", unescaped)
    print(f"Chunk created successfully: {chunk.id}")

    # Step 3: Serialize to JSON (this is where the fix applies)
    json_str = chunk.model_dump_json()
    print(f"JSON serialized: {json_str}")

    # Step 4: Parse back to verify newlines are preserved
    parsed = json.loads(json_str)
    content = parsed["choices"][0]["delta"]["content"]
    print(f"Parsed content: {repr(content)}")

    # Verify the content contains actual newlines, not escaped ones
    has_actual_newlines = '\n' in content
    has_escaped_newlines = '\\n' in content

    print("")
    print("=== RESULTS ===")
    print(f"Contains actual newlines (\\n): {has_actual_newlines}")
    print(f"Contains escaped newlines (\\\\n): {has_escaped_newlines}")

    if has_actual_newlines and not has_escaped_newlines:
        print("✅ SUCCESS: Newlines are properly preserved!")
        print("✅ This should render correctly in Open WebUI")
        return True
    else:
        print("❌ FAILURE: Newlines are not properly handled")
        return False


if __name__ == "__main__":
    test_newline_handling()