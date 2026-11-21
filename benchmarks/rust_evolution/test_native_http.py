# SPDX-License-Identifier: Apache-2.0
import json
import os
import selectors
import signal
import subprocess
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from sse_probe import measure

BINARY = os.environ.get("OMLX_R_TEST_BINARY")
CHECKPOINT = os.environ.get("OMLX_R_TEST_CHECKPOINT")
MODEL = "omlx-r-native"
PROMPT = "What is 2 + 2? Answer briefly."
REQUEST = {"model": MODEL, "messages": [{"role": "user", "content": PROMPT}]}
LONG = {**REQUEST, "messages": [{"role": "user", "content": "x " * 2000}]}
COUNT = {
    **REQUEST,
    "messages": [
        {
            "role": "user",
            "content": "Count from 1 to 100, separated by commas. Start directly with 1.",
        }
    ],
}


@contextmanager
def native_server():
    with (
        tempfile.TemporaryDirectory(prefix="omlx-r-native-test-") as scratch,
        subprocess.Popen(
            [
                str(Path(BINARY).resolve()),
                "serve-native",
                str(Path(CHECKPOINT).resolve()),
            ],
            cwd=scratch,
            env={**os.environ, "XDG_CONFIG_HOME": scratch, "XDG_CACHE_HOME": scratch},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as process,
    ):
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                if not selector.select(timeout=15):
                    raise AssertionError("native service did not announce readiness")
            line = process.stdout.readline().strip()
            if not line.startswith("Listening on http://127.0.0.1:"):
                _, stderr = process.communicate(timeout=5)
                raise AssertionError(f"native service failed to start: {line} {stderr}")
            yield line.removeprefix("Listening on "), process
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)


@unittest.skipUnless(
    BINARY and CHECKPOINT, "requires the native binary and pinned checkpoint"
)
class NativeHttpTests(unittest.TestCase):
    def post(self, url, payload):
        return urlopen(
            Request(
                f"{url}/v1/chat/completions",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            ),
            timeout=5,
        )

    def assert_shutdown(self, process, sig):
        process.send_signal(sig)
        stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, stderr)
        report = json.loads(stdout)
        self.assertEqual(report["event"], "native_shutdown")
        for counter in ("active_bytes", "cache_bytes"):
            self.assertLessEqual(
                report["released"][counter],
                report["baseline"][counter] + report["release_tolerance_bytes"],
            )
        return report

    def test_model_listing_json_and_sse_then_idle_sigint(self):
        with native_server() as (url, process):
            with urlopen(f"{url}/v1/models", timeout=5) as response:
                self.assertEqual(
                    [model["id"] for model in json.load(response)["data"]], [MODEL]
                )
            with self.post(url, REQUEST) as response:
                choice = json.load(response)["choices"][0]
            self.assertEqual(choice["message"]["content"], "2 + 2 equals 4.")
            self.assertEqual(choice["finish_reason"], "stop")
            streamed = measure(url, REQUEST, timeout=5)
            self.assertEqual(streamed["content"], choice["message"]["content"])
            self.assertEqual(streamed["finish_reason"], choice["finish_reason"])
            self.assertGreater(streamed["content_chunks"], 1)
            self.assertEqual(measure(url, COUNT, timeout=5)["finish_reason"], "length")

            multilingual = {
                **REQUEST,
                "messages": [
                    {"role": "user", "content": "用中文简短回答：什么是苹果？"}
                ],
            }
            with self.post(url, multilingual) as response:
                choice = json.load(response)["choices"][0]
            self.assertEqual(
                measure(url, multilingual, timeout=5)["content"],
                choice["message"]["content"],
            )
            self.assertNotIn("\ufffd", choice["message"]["content"])

            with self.assertRaises(HTTPError) as caught:
                self.post(
                    url,
                    {**REQUEST, "messages": [{"role": "user", "content": "界" * 1366}]},
                )
            with caught.exception as response:
                self.assertEqual(response.code, 400)
                self.assertEqual(
                    json.load(response)["error"]["code"], "invalid_request"
                )
            self.assert_shutdown(process, signal.SIGINT)

    def test_busy_disconnect_and_in_flight_sigterm(self):
        with native_server() as (url, process):
            with self.post(url, {**LONG, "stream": True}) as response:
                first = json.loads(response.readline().removeprefix(b"data: "))
                self.assertEqual(first["choices"][0]["delta"], {"role": "assistant"})
                with self.assertRaises(HTTPError) as caught:
                    self.post(url, {**REQUEST, "stream": True})
                with caught.exception as busy:
                    self.assertEqual(busy.code, 503)
                    self.assertEqual(json.load(busy)["error"]["code"], "worker_busy")

            deadline = time.monotonic() + 5
            while True:
                try:
                    with self.post(url, REQUEST) as reply:
                        self.assertEqual(
                            json.load(reply)["choices"][0]["finish_reason"], "stop"
                        )
                    break
                except HTTPError as busy:
                    with busy:
                        if busy.code != 503 or time.monotonic() >= deadline:
                            raise
                        self.assertEqual(
                            json.load(busy)["error"]["code"], "worker_busy"
                        )
                    time.sleep(0.01)

            with self.post(url, {**COUNT, "stream": True}) as response:
                for line in response:
                    if line.startswith(b"data: "):
                        delta = json.loads(line.removeprefix(b"data: "))["choices"][0][
                            "delta"
                        ]
                        if delta.get("content", "").strip():
                            break
                else:
                    self.fail("no visible delta before shutdown")
                self.assert_shutdown(process, signal.SIGTERM)
                tail = response.read()
                self.assertIn(b"generation_failed", tail)
                self.assertNotIn(b"[DONE]", tail)
