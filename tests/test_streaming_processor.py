#!/usr/bin/env python3
"""
Test script for the Stateful Content Processor streaming functionality.

This script demonstrates and tests the streaming-aware escape sequence 
reconstruction logic for handling split sequences across chunk boundaries.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from streaming_content_processor import (
    StatefulContentProcessor,
    process_streaming_chunk,
    cleanup_streaming_session,
    flush_streaming_session_buffer
)


def test_basic_reconstruction():
    """Test basic escape sequence reconstruction across chunks."""
    print("=== Basic Reconstruction Test ===")
    
    processor = StatefulContentProcessor()
    session_id = "test-basic"
    
    # Simulate chunks that split a \\n sequence
    chunk1 = "Hello |\\"
    chunk2 = "n World"
    
    print(f"Chunk 1: {repr(chunk1)}")
    print(f"Chunk 2: {repr(chunk2)}")
    
    result1 = processor.process_chunk(session_id, chunk1)
    result2 = processor.process_chunk(session_id, chunk2)
    
    print(f"Result 1: {repr(result1)}")
    print(f"Result 2: {repr(result2)}")
    
    # Combined result should have actual newline
    combined = result1 + result2
    print(f"Combined: {repr(combined)}")
    
    expected = "Hello |\n World"
    success = combined == expected
    print(f"Expected: {repr(expected)}")
    print(f"✅ PASS" if success else f"❌ FAIL")
    print()
    
    return success


def test_multiple_sequences():
    """Test multiple escape sequences in one chunk."""
    print("=== Multiple Sequences Test ===")
    
    processor = StatefulContentProcessor()
    session_id = "test-multiple"
    
    # Chunk with multiple escape sequences
    chunk = "Line 1\\nLine 2\\tTab\\nLine 3"
    
    print(f"Input: {repr(chunk)}")
    
    result = processor.process_chunk(session_id, chunk)
    
    print(f"Result: {repr(result)}")
    
    expected = "Line 1\nLine 2\tTab\nLine 3"
    success = result == expected
    print(f"Expected: {repr(expected)}")
    print(f"✅ PASS" if success else f"❌ FAIL")
    print()
    
    return success


def test_split_multiple_sequences():
    """Test multiple sequences split across multiple chunks."""
    print("=== Split Multiple Sequences Test ===")
    
    processor = StatefulContentProcessor()
    session_id = "test-split-multi"
    
    # Simulate complex splitting
    chunks = [
        "Start\\",
        "nMiddle\\t",
        "End\\",
        "rDone"
    ]
    
    print("Processing chunks:")
    results = []
    for i, chunk in enumerate(chunks):
        print(f"  Chunk {i+1}: {repr(chunk)}")
        result = processor.process_chunk(session_id, chunk)
        results.append(result)
        print(f"    Result: {repr(result)}")
    
    # Combined result
    combined = "".join(results)
    print(f"Combined: {repr(combined)}")
    
    expected = "Start\nMiddle\tEnd\rDone"
    success = combined == expected
    print(f"Expected: {repr(expected)}")
    print(f"✅ PASS" if success else f"❌ FAIL")
    print()
    
    return success


def test_fast_path():
    """Test fast path for content with real newlines."""
    print("=== Fast Path Test ===")
    
    processor = StatefulContentProcessor()
    session_id = "test-fast"
    
    # Content with real newlines (should use fast path)
    chunk = "Line 1\nLine 2\nLine 3"
    
    print(f"Input: {repr(chunk)}")
    
    result = processor.process_chunk(session_id, chunk)
    
    print(f"Result: {repr(result)}")
    
    success = result == chunk  # Should be unchanged
    print(f"✅ PASS (fast path)" if success else f"❌ FAIL")
    print()
    
    return success


def test_buffer_flush():
    """Test buffer flushing at end of stream."""
    print("=== Buffer Flush Test ===")
    
    processor = StatefulContentProcessor()
    session_id = "test-flush"
    
    # Chunk ending with incomplete sequence
    chunk = "Hello \\"
    
    print(f"Chunk: {repr(chunk)}")
    
    result = processor.process_chunk(session_id, chunk)
    print(f"Result: {repr(result)}")
    
    # Flush the buffer
    flushed = processor.flush_session_buffer(session_id)
    print(f"Flushed: {repr(flushed)}")
    
    # Combined should be "Hello \\" (no change since incomplete)
    combined = result + flushed
    expected = "Hello \\"
    success = combined == expected
    print(f"Expected: {repr(expected)}")
    print(f"✅ PASS" if success else f"❌ FAIL")
    print()
    
    return success


def test_convenience_functions():
    """Test the convenience functions."""
    print("=== Convenience Functions Test ===")
    
    session_id = "test-convenience"
    
    # Test process_streaming_chunk function
    chunk1 = "Hello |\\"
    chunk2 = "n World"
    
    print(f"Chunk 1: {repr(chunk1)}")
    print(f"Chunk 2: {repr(chunk2)}")
    
    result1 = process_streaming_chunk(session_id, chunk1)
    result2 = process_streaming_chunk(session_id, chunk2)
    
    print(f"Result 1: {repr(result1)}")
    print(f"Result 2: {repr(result2)}")
    
    # Flush remaining buffer
    flushed = flush_streaming_session_buffer(session_id)
    print(f"Flushed: {repr(flushed)}")
    
    combined = result1 + result2 + flushed
    expected = "Hello |\n World"
    success = combined == expected
    print(f"Expected: {repr(expected)}")
    print(f"✅ PASS" if success else f"❌ FAIL")
    print()
    
    # Cleanup
    cleanup_streaming_session(session_id)
    
    return success


def main():
    """Run all tests."""
    print("🚀 Testing Stateful Content Processor")
    print("=" * 50)
    print()
    
    tests = [
        test_basic_reconstruction,
        test_multiple_sequences,
        test_split_multiple_sequences,
        test_fast_path,
        test_buffer_flush,
        test_convenience_functions,
    ]
    
    results = []
    for test_func in tests:
        try:
            result = test_func()
            results.append(result)
        except Exception as e:
            print(f"❌ Test {test_func.__name__} failed with exception: {e}")
            results.append(False)
        print()
    
    print("=" * 50)
    passed = sum(results)
    total = len(results)
    print(f"📊 Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed!")
        return 0
    else:
        print("⚠️  Some tests failed!")
        return 1


if __name__ == "__main__":
    sys.exit(main())