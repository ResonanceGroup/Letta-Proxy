#!/usr/bin/env python3
"""Live validation for reasoning token formatting through the OpenAI-compatible proxy.

This script talks to the proxy endpoint and verifies that:
1. reasoning tokens stream in `delta.reasoning`
2. final assistant text streams in `delta.content`
3. reasoning text does not leak into assistant content

Usage:
    python3 tests/test_live_reasoning_format.py \
        --proxy-base-url http://127.0.0.1:8100 \
        --model Proxy-Test
"""

import argparse
import json
import sys
from typing import List

import requests


def collect_stream(proxy_base_url: str, model: str, prompt: str):
    response = requests.post(
        f"{proxy_base_url}/v1/chat/completions",
        json={
            "model": model,
            "stream": True,
            "messages": [{"role": "user", "content": prompt}],
        },
        stream=True,
        timeout=60,
    )
    response.raise_for_status()

    content_parts: List[str] = []
    reasoning_parts: List[str] = []
    finish_reasons: List[str] = []

    for line in response.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue

        data = line[6:]
        if data == "[DONE]":
            break

        chunk = json.loads(data)
        choice = chunk["choices"][0]
        delta = choice.get("delta", {})

        if delta.get("content"):
            content_parts.append(delta["content"])
        if delta.get("reasoning"):
            reasoning_parts.append(delta["reasoning"])
        if choice.get("finish_reason"):
            finish_reasons.append(choice["finish_reason"])

    return "".join(content_parts), "".join(reasoning_parts), finish_reasons


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proxy-base-url", default="http://127.0.0.1:8100")
    parser.add_argument("--model", default="Proxy-Test")
    parser.add_argument(
        "--prompt",
        default="Reply with exactly: REASONING_FORMAT_OK",
    )
    args = parser.parse_args()

    content, reasoning, finish_reasons = collect_stream(
        args.proxy_base_url,
        args.model,
        args.prompt,
    )

    print(f"content={content!r}")
    print(f"reasoning_prefix={reasoning[:120]!r}")
    print(f"reasoning_len={len(reasoning)}")
    print(f"finish_reasons={finish_reasons}")

    errors = []
    if "REASONING_FORMAT_OK" not in content:
        errors.append("final assistant content missing expected answer")
    if not reasoning:
        errors.append("no reasoning chunks were captured")
    if reasoning and reasoning in content:
        errors.append("reasoning leaked into assistant content")
    if any(marker in content for marker in ["The user is asking", "I should", "This is a simple"]):
        errors.append("assistant content still contains reasoning-style text")

    if errors:
        print("FAIL")
        for error in errors:
            print(f" - {error}")
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
