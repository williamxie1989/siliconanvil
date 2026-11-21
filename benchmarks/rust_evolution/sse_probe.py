# SPDX-License-Identifier: Apache-2.0
"""Measure first visible output and validate chat SSE termination."""

import argparse
import json
import time
from http.client import HTTPException
from pathlib import Path
from urllib.request import Request, urlopen


class ProbeError(ValueError):
    """The response did not satisfy the streaming contract."""


def data_events(response):
    lines = []
    for raw in response:
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line and lines:
            yield "\n".join(lines)
            lines.clear()
        field, _, value = line.partition(":")
        if field == "data":
            lines.append(value.removeprefix(" "))
    if lines:
        raise ProbeError("stream ended inside an SSE event")


def measure(base_url, payload, *, timeout=10):
    request = Request(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        data=json.dumps({**payload, "stream": True}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )
    start = time.perf_counter()
    first_output = None
    done_at = None
    chunks = []
    finish_reason = None
    with urlopen(request, timeout=timeout) as response:
        if response.headers.get_content_type() != "text/event-stream":
            raise ProbeError("expected text/event-stream")
        for event in data_events(response):
            if done_at is not None:
                raise ProbeError("SSE data arrived after [DONE]")
            if event == "[DONE]":
                done_at = time.perf_counter()
                continue
            try:
                frame = json.loads(event)
                if "error" in frame:
                    raise ProbeError("server reported a streaming error")
                choices = frame["choices"]
                if not choices:
                    continue
                (choice,) = choices
                if choice["index"] != 0:
                    raise ProbeError("expected a single choice at index 0")
                content = choice["delta"].get("content")
                reason = choice.get("finish_reason")
            except (TypeError, KeyError, AttributeError, ValueError) as exc:
                if isinstance(exc, ProbeError):
                    raise
                raise ProbeError("invalid chat completion event") from exc
            if content is not None and not isinstance(content, str):
                raise ProbeError("visible content must be a string")
            if content:
                if finish_reason is not None:
                    raise ProbeError("visible content arrived after finish_reason")
                if first_output is None:
                    first_output = time.perf_counter()
                chunks.append(content)
            if reason is not None:
                if (
                    finish_reason is not None
                    or not isinstance(reason, str)
                    or not reason
                ):
                    raise ProbeError("expected one non-empty finish_reason")
                finish_reason = reason
    if done_at is None:
        raise ProbeError("stream ended before [DONE]")
    if finish_reason is None:
        raise ProbeError("[DONE] arrived without a finish_reason")
    if first_output is None:
        raise ProbeError("[DONE] arrived without visible text")
    return {
        "ttft_s": first_output - start,
        "elapsed_s": done_at - start,
        "finish_reason": finish_reason,
        "content": "".join(chunks),
        "content_chunks": len(chunks),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url")
    parser.add_argument("request", type=Path, help="path to a JSON chat request")
    parser.add_argument("--timeout", type=float, default=10)
    args = parser.parse_args()
    try:
        payload = json.loads(args.request.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ProbeError("request must be a JSON object")
        result = measure(args.base_url, payload, timeout=args.timeout)
    except (ValueError, OSError, HTTPException) as exc:
        parser.exit(2, f"SSE probe failed: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
