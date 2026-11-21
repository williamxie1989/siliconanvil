# SPDX-License-Identifier: Apache-2.0
import json
import threading
import time
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sse_probe import ProbeError, measure


@contextmanager
def scripted_server(*, complete, delay=0, trailer=b""):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            time.sleep(delay)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()

            def send(delta, finish=None):
                payload = {
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]
                }
                self.wfile.write(b"data: " + json.dumps(payload).encode() + b"\n\n")
                self.wfile.flush()

            send({"role": "assistant"})
            time.sleep(delay)
            send({"content": "hello"})
            if complete:
                send({}, "stop")
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.write(trailer)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(
            target=server.serve_forever, args=(0.01,), daemon=True
        )
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join()


class SseProbeTests(unittest.TestCase):
    def test_ttft_includes_response_headers_and_first_visible_output(self):
        with scripted_server(complete=True, delay=0.1) as url:
            result = measure(url, {}, timeout=3)
        self.assertGreaterEqual(result["ttft_s"], 0.18)
        self.assertLessEqual(result["ttft_s"], result["elapsed_s"])
        self.assertEqual(result["content"], "hello")
        self.assertEqual(result["finish_reason"], "stop")

    def test_rejects_eof_before_done(self):
        with (
            scripted_server(complete=False) as url,
            self.assertRaisesRegex(ProbeError, r"\[DONE\]"),
        ):
            measure(url, {}, timeout=3)

    def test_rejects_data_after_done(self):
        for trailer in (b"data: [DONE]\n\n", b"data: {}\n\n"):
            with (
                self.subTest(trailer=trailer),
                scripted_server(complete=True, trailer=trailer) as url,
                self.assertRaisesRegex(ProbeError, r"after \[DONE\]"),
            ):
                measure(url, {}, timeout=3)


if __name__ == "__main__":
    unittest.main()
