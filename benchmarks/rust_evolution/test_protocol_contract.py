# SPDX-License-Identifier: Apache-2.0
import json
import os
import unittest
from pathlib import Path

from protocol_contract import (
    MODEL,
    ProtocolError,
    capture_candidate,
    compare,
    observe,
)


def event(payload):
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return f"data: {data}\n\n".encode()


def chunk(delta, reason=None, response_id="chat-1"):
    return event(
        {
            "id": response_id,
            "created": 42,
            "model": MODEL,
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": delta, "finish_reason": reason}],
        }
    )


def stream(text, *, repeated_role=False):
    role = chunk({"role": "assistant"})
    return (
        role
        + (role if repeated_role else b"")
        + chunk({"content": text})
        + chunk({}, "stop")
        + event("[DONE]")
    )


class ProtocolContractTests(unittest.TestCase):
    def test_preserves_role_count_and_generated_text_outside_shared_contract(self):
        upstream = observe(
            "chat_sse",
            200,
            "text/event-stream; charset=utf-8",
            stream("upstream", repeated_role=True),
        )
        rust = observe("chat_sse", 200, "text/event-stream", stream("mock"))
        result = compare({"chat_sse": upstream}, {"chat_sse": rust})
        self.assertTrue(result["shared_contract_matches"])
        self.assertEqual(
            result["cases"]["chat_sse"]["observed_differences"]["role_frames"],
            {"reference": 2, "candidate": 1},
        )

    def test_missing_done_cannot_match_and_error_is_distinct_from_success(self):
        err = chunk({"role": "assistant"}) + event(
            {"error": {"type": "server_error", "message": "fixture failure"}}
        )
        with self.assertRaisesRegex(ProtocolError, r"\[DONE\]"):
            observe("chat_sse_error", 200, "text/event-stream", err)
        error = observe(
            "chat_sse_error", 200, "text/event-stream", err + event("[DONE]")
        )
        self.assertEqual(error["shared"]["outcome"], "server_error")
        with self.assertRaisesRegex(ProtocolError, "finish"):
            observe("chat_sse", 200, "text/event-stream", err + event("[DONE]"))

    def test_rejects_changed_stream_id_and_detects_changed_finish_reason(self):
        bad_id = (
            chunk({"role": "assistant"})
            + chunk({"content": "text"}, response_id="chat-2")
            + chunk({}, "stop")
            + event("[DONE]")
        )
        with self.assertRaisesRegex(ProtocolError, "id changed"):
            observe("chat_sse", 200, "text/event-stream", bad_id)
        same = observe("chat_sse", 200, "text/event-stream", stream("text"))
        different = observe(
            "chat_sse",
            200,
            "text/event-stream",
            stream("text").replace(b'"stop"', b'"length"'),
        )
        result = compare({"chat_sse": same}, {"chat_sse": different})
        self.assertFalse(result["shared_contract_matches"])
        self.assertIn(
            "finish_reason", result["cases"]["chat_sse"]["shared_differences"]
        )

    def test_model_list_validates_the_returned_identifier(self):
        reply = {
            "object": "list",
            "data": [{"id": "different-model", "object": "model"}],
        }
        with self.assertRaisesRegex(ProtocolError, MODEL):
            observe("models", 200, "application/json", json.dumps(reply).encode())

    def test_rejects_a_tool_delta_in_a_plain_text_fixture(self):
        mixed = (
            chunk({"role": "assistant"})
            + chunk({"content": "text", "tool_calls": [{"index": 0}]})
            + chunk({}, "stop")
            + event("[DONE]")
        )
        with self.assertRaisesRegex(ProtocolError, "non-text"):
            observe("chat_sse", 200, "text/event-stream", mixed)

    def test_omitting_a_shared_case_does_not_pass_the_comparison(self):
        sse = observe("chat_sse", 200, "text/event-stream", stream("text"))
        with self.assertRaisesRegex(ProtocolError, "missing shared case chat_json"):
            compare({"chat_sse": sse, "chat_json": {}}, {"chat_sse": sse})

    @unittest.skipUnless(
        os.environ.get("OMLX_R_TEST_BINARY"), "set the Rust integration binary"
    )
    def test_compares_the_rust_binary_against_the_pinned_upstream_fixture(self):
        fixture = Path(__file__).with_name("fixtures") / "omlx-v0.8.2-contract.json"
        reference = json.loads(fixture.read_text(encoding="utf-8"))
        comparison = compare(
            reference["cases"], capture_candidate(os.environ["OMLX_R_TEST_BINARY"])
        )
        self.assertTrue(comparison["shared_contract_matches"])
        self.assertEqual(set(comparison["cases"]), {"models", "chat_json", "chat_sse"})
        error = comparison["reference_only"]["chat_sse_error"]
        self.assertTrue(error["shared"]["done"])
        self.assertEqual(error["shared"]["outcome"], "server_error")
        self.assertEqual(error["observed"]["text"], "Partial output")


if __name__ == "__main__":
    unittest.main()
