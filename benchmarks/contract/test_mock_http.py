# SPDX-License-Identifier: Apache-2.0
import json
import os
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from protocol_contract import mock_server
from sse_probe import measure

MODEL = "siliconanvil-mock"
REQUEST = {"model": MODEL, "messages": [{"role": "user", "content": "hello"}]}


@unittest.skipUnless(
    os.environ.get("SILICONANVIL_TEST_BINARY"), "set the Rust integration binary"
)
class MockHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = cls.enterClassContext(mock_server(os.environ["SILICONANVIL_TEST_BINARY"]))

    def post(self, payload):
        return urlopen(
            Request(
                f"{self.url}/v1/chat/completions",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            ),
            timeout=3,
        )

    def test_models_and_non_streaming_completion(self):
        with urlopen(f"{self.url}/v1/models", timeout=3) as response:
            models = json.load(response)
        self.assertEqual(models["object"], "list")
        self.assertEqual([model["id"] for model in models["data"]], [MODEL])
        with self.post(REQUEST) as response:
            completion = json.load(response)
        self.assertEqual(completion["object"], "chat.completion")
        self.assertEqual(
            completion["choices"][0]["message"]["content"], "Hello from SiliconAnvil."
        )
        self.assertEqual(completion["choices"][0]["finish_reason"], "stop")

    def test_full_sse_order_and_exact_termination(self):
        with self.post({**REQUEST, "stream": True}) as response:
            self.assertEqual(response.headers.get_content_type(), "text/event-stream")
            frames = [
                line.decode().strip().removeprefix("data: ")
                for line in response
                if line.startswith(b"data: ")
            ]
        self.assertEqual(frames.count("[DONE]"), 1)
        self.assertEqual(frames[-1], "[DONE]")
        events = [json.loads(frame) for frame in frames[:-1]]
        self.assertEqual(len({event["id"] for event in events}), 1)
        self.assertEqual(len({event["created"] for event in events}), 1)
        self.assertEqual({event["model"] for event in events}, {MODEL})
        self.assertEqual(
            {event["object"] for event in events}, {"chat.completion.chunk"}
        )
        choices = [event["choices"][0] for event in events]
        self.assertEqual(choices[0]["delta"], {"role": "assistant"})
        self.assertEqual(
            "".join(c["delta"].get("content", "") for c in choices),
            "Hello from SiliconAnvil.",
        )
        self.assertEqual(
            [c["finish_reason"] for c in choices], [None, None, None, "stop"]
        )
        self.assertEqual(measure(self.url, REQUEST, timeout=3)["finish_reason"], "stop")

    def test_rejects_unknown_model_and_out_of_scope_fields(self):
        for payload, status, code in (
            ({**REQUEST, "model": "missing"}, 404, "model_not_found"),
            ({**REQUEST, "messages": []}, 400, "invalid_request"),
            ({**REQUEST, "tools": []}, 400, "invalid_request"),
            ([MODEL, REQUEST["messages"], False], 400, "invalid_request"),
            ({**REQUEST, "messages": [["user", "hello"]]}, 400, "invalid_request"),
        ):
            with self.subTest(code=code, payload=payload):
                with self.assertRaises(HTTPError) as raised:
                    self.post(payload)
                with raised.exception as response:
                    self.assertEqual(response.code, status)
                    self.assertEqual(json.load(response)["error"]["code"], code)

    def test_client_disconnect_allows_a_following_request(self):
        with self.post({**REQUEST, "stream": True}) as response:
            role_frame = json.loads(response.readline().removeprefix(b"data: "))
        self.assertEqual(role_frame["choices"][0]["delta"], {"role": "assistant"})
        self.assertEqual(
            measure(self.url, REQUEST, timeout=3)["content"], "Hello from SiliconAnvil."
        )


if __name__ == "__main__":
    unittest.main()
