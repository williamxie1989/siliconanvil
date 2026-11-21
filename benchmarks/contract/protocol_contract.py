# SPDX-License-Identifier: Apache-2.0
"""Capture the pinned oMLX contract and compare it with the Rust mock service."""

import argparse
import io
import json
import os
import platform
import runpy
import selectors
import subprocess
import sys
import tempfile
from contextlib import closing, contextmanager
from http.client import HTTPConnection, HTTPException
from importlib.metadata import version
from pathlib import Path
from unittest.mock import patch

from sse_probe import data_events

UPSTREAM = {"tag": "v0.8.2", "commit": "5eeec7757f83bfc99eeb87abe49686d00a0bba1f"}
MODEL = "siliconanvil-mock"
REQUEST = {"model": MODEL, "messages": [{"role": "user", "content": "Hello"}]}
CASES = ("models", "chat_json", "chat_sse")
ERROR_CASE = "chat_sse_error"


class ProtocolError(ValueError):
    """A response or checkout failed validation."""


def require(condition, message):
    if not condition:
        raise ProtocolError(message)


def identity(frame, object_type):
    require(frame["model"] == MODEL, f"expected model {MODEL}")
    require(frame["object"] == object_type, f"expected object {object_type}")
    require(isinstance(frame["id"], str) and frame["id"], "expected a response id")
    require(type(frame["created"]) is int, "expected an integer creation timestamp")
    return frame["id"]


def require_plain_text(message):
    require(
        all(
            key in ("role", "content") or value is None
            for key, value in message.items()
        ),
        "unexpected non-text message field",
    )


def inspect_sse(body, *, expected_error):
    events = list(data_events(io.BytesIO(body)))
    require(
        events and events[-1] == "[DONE]" and events.count("[DONE]") == 1,
        "expected one [DONE] as the final data event",
    )
    response_id = None
    role_frames = 0
    content = []
    finish_reason = None
    error_type = None
    error_message = None
    usage_fields = []
    for data in events[:-1]:
        frame = json.loads(data)
        require(error_type is None, "unexpected data after a stream error")
        if "error" in frame:
            require(finish_reason is None, "unexpected error after finish")
            error_type = frame["error"]["type"]
            require(isinstance(error_type, str) and error_type, "missing error type")
            error_message = frame["error"]["message"]
            require(
                isinstance(error_message, str) and error_message,
                "missing error message",
            )
            continue
        frame_id = identity(frame, "chat.completion.chunk")
        require(
            response_id in (None, frame_id), "response id changed within the stream"
        )
        response_id = frame_id
        if frame["choices"] == []:
            require(finish_reason is not None, "usage arrived before finish")
            require(isinstance(frame["usage"], dict), "expected a usage object")
            usage_fields = sorted(frame["usage"])
            continue
        (choice,) = frame["choices"]
        require(choice["index"] == 0, "expected choice index 0")
        require(finish_reason is None, "choice arrived after finish")
        delta = choice["delta"]
        require_plain_text(delta)
        role = delta.get("role")
        if role is not None:
            require(
                role == "assistant" and not content, "unexpected assistant role frame"
            )
            role_frames += 1
        text = delta.get("content")
        if text is not None:
            require(isinstance(text, str), "expected a text delta")
            if text:
                require(role_frames > 0, "content arrived before the assistant role")
                content.append(text)
        reason = choice.get("finish_reason")
        if reason is not None:
            require(isinstance(reason, str) and reason, "invalid finish reason")
            finish_reason = reason

    require(role_frames > 0, "missing the assistant role")
    if expected_error:
        require(
            error_type == "server_error" and finish_reason is None,
            "expected a server error",
        )
    else:
        require(
            error_type is None and finish_reason is not None,
            "missing successful finish",
        )
        require(content, "missing the visible completion")
    return (
        {
            "object": "chat.completion.chunk",
            "model": MODEL,
            "role": "assistant",
            "finish_reason": finish_reason,
            "outcome": error_type or "completed",
            "done": True,
        },
        {
            "content_chunks": len(content),
            "role_frames": role_frames,
            "text": "".join(content),
            "usage_fields": usage_fields,
            "error_message": error_message,
        },
    )


def inspect_json(case, body):
    frame = json.loads(body)
    if case == "models":
        (model,) = frame["data"]
        require(frame["object"] == "list", "expected a model list")
        require(model["id"] == MODEL, f"expected model {MODEL}")
        require(model["object"] == "model", "expected a model object")
        return (
            {"object": "list", "models": [{"id": MODEL, "object": "model"}]},
            {"model_fields": sorted(model), "owned_by": model.get("owned_by")},
        )

    identity(frame, "chat.completion")
    (choice,) = frame["choices"]
    message = choice["message"]
    require_plain_text(message)
    require(
        choice["index"] == 0 and message["role"] == "assistant",
        "invalid assistant choice",
    )
    require(
        isinstance(message["content"], str) and message["content"],
        "missing visible text",
    )
    reason = choice["finish_reason"]
    require(isinstance(reason, str) and reason, "invalid finish reason")
    return (
        {
            "object": "chat.completion",
            "model": MODEL,
            "role": "assistant",
            "finish_reason": reason,
        },
        {"text": message["content"], "usage_fields": sorted(frame.get("usage", {}))},
    )


def observe(case, status, content_type, body):
    """Keep wire differences visible while removing volatile values."""
    stream = case in ("chat_sse", ERROR_CASE)
    media_type = content_type.partition(";")[0].strip().lower()
    expected_media = "text/event-stream" if stream else "application/json"
    require(
        status == 200 and media_type == expected_media, f"{case}: {status} {media_type}"
    )
    try:
        shared, details = (
            inspect_sse(body, expected_error=case == ERROR_CASE)
            if stream
            else inspect_json(case, body)
        )
    except ProtocolError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ProtocolError(f"{case}: invalid response shape: {exc}") from exc
    return {
        "shared": {"status": status, "media_type": media_type, **shared},
        "observed": details,
    }


def differences(reference, candidate):
    return {
        key: {"reference": reference.get(key), "candidate": candidate.get(key)}
        for key in sorted(reference.keys() | candidate.keys())
        if reference.get(key) != candidate.get(key)
    }


def compare(reference, candidate):
    missing = reference.keys() - candidate.keys() - {ERROR_CASE}
    require(not missing, f"candidate missing shared case {', '.join(sorted(missing))}")
    results = {}
    for case, observed in candidate.items():
        require(case in reference, f"reference is missing case {case}")
        results[case] = {
            "shared_differences": differences(
                reference[case]["shared"], observed["shared"]
            ),
            "observed_differences": differences(
                reference[case]["observed"], observed["observed"]
            ),
        }
    reference_only = {
        case: reference[case] for case in sorted(reference.keys() - candidate.keys())
    }
    return {
        "shared_contract_matches": bool(results)
        and all(not entry["shared_differences"] for entry in results.values()),
        "cases": results,
        "reference_only": reference_only,
    }


def capture_upstream(checkout):
    checkout = checkout.resolve()
    require(
        subprocess.check_output(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
        ).strip()
        == UPSTREAM["commit"],
        f"expected upstream checkout {UPSTREAM['commit']}",
    )
    require(
        not subprocess.check_output(
            [
                "git",
                "-C",
                str(checkout),
                "status",
                "--porcelain",
                "--untracked-files=normal",
            ]
        ),
        "upstream checkout must be clean",
    )
    with tempfile.TemporaryDirectory(prefix="siliconanvil-reference-") as isolated:
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("OMLX_")
        }
        env.update(
            {
                "HOME": isolated,
                "OMLX_BASE_PATH": str(Path(isolated) / ".omlx"),
                "XDG_CACHE_HOME": str(Path(isolated) / "cache"),
                "XDG_CONFIG_HOME": str(Path(isolated) / "config"),
                "XDG_DATA_HOME": str(Path(isolated) / "data"),
                "HF_HOME": str(Path(isolated) / "hf"),
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
            }
        )
        with patch.dict(os.environ, env, clear=True):
            cases = capture_routes(checkout)
    return {
        "schema": 1,
        "reference": UPSTREAM,
        "transport": "ASGI HTTP with mock engine",
        "runtime": {"python": platform.python_version(), "fastapi": version("fastapi")},
        "cases": cases,
    }


def capture_routes(checkout):
    sys.path.insert(0, str(checkout))
    sys.dont_write_bytecode = True
    from fastapi.testclient import TestClient
    from omlx import server

    require(
        Path(server.__file__).resolve() == checkout / "omlx/server.py",
        "upstream module was loaded from a different checkout",
    )
    fixtures = runpy.run_path(str(checkout / "tests/integration/test_e2e_streaming.py"))
    mock_engine = fixtures["MockBaseEngine"]

    class FailingEngine(mock_engine):
        async def stream_chat(self, *_args, **_kwargs):
            yield fixtures["MockGenerationOutput"](
                text="Partial output", new_text="Partial output", completion_tokens=1
            )
            raise RuntimeError("fixture engine failure")

    def pool(engine_type):
        instance = fixtures["MockEnginePool"](engine_type(model_name=MODEL))
        instance._models[0]["id"] = MODEL
        return instance

    async def http_only(scope, receive, send):
        require(
            scope["type"] == "http",
            "only HTTP requests are allowed in the capture process",
        )
        await server.app(scope, receive, send)

    server._server_state.engine_pool = pool(mock_engine)
    server._server_state.default_model = MODEL
    client = TestClient(http_only)
    try:
        replies = {
            "models": client.get("/v1/models"),
            "chat_json": client.post("/v1/chat/completions", json=REQUEST),
            "chat_sse": client.post(
                "/v1/chat/completions", json={**REQUEST, "stream": True}
            ),
        }
        server._server_state.engine_pool = pool(FailingEngine)
        replies[ERROR_CASE] = client.post(
            "/v1/chat/completions", json={**REQUEST, "stream": True}
        )
        observed = {
            case: observe(
                case, reply.status_code, reply.headers["content-type"], reply.content
            )
            for case, reply in replies.items()
        }
        error = observed[ERROR_CASE]["observed"]
        require(
            error["text"] == "Partial output"
            and error["error_message"] == "fixture engine failure",
            "expected the injected engine error after partial output",
        )
        return observed
    finally:
        client.close()


@contextmanager
def mock_server(binary):
    """Run an isolated Rust child on a kernel-assigned loopback port."""
    with subprocess.Popen(
        [str(Path(binary).resolve()), "serve", "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                require(
                    selector.select(timeout=5), "Rust mock did not report its address"
                )
            line = process.stdout.readline().strip()
            prefix = "Listening on http://127.0.0.1:"
            require(
                line.startswith(prefix) and line[len(prefix) :].isdecimal(),
                "invalid Rust mock address",
            )
            yield line.removeprefix("Listening on ")
        finally:
            process.terminate()
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=3)


def capture_candidate(binary):
    with (
        mock_server(binary) as url,
        closing(
            HTTPConnection("127.0.0.1", int(url.rsplit(":", 1)[1]), timeout=5)
        ) as client,
    ):
        result = {}
        for case in CASES:
            payload = {**REQUEST, "stream": True} if case == "chat_sse" else REQUEST
            body = None if case == "models" else json.dumps(payload).encode()
            path = "/v1/models" if case == "models" else "/v1/chat/completions"
            client.request(
                "GET" if case == "models" else "POST",
                path,
                body=body,
                headers={"Content-Type": "application/json"},
            )
            with client.getresponse() as response:
                result[case] = observe(
                    case,
                    response.status,
                    response.getheader("Content-Type", ""),
                    response.read(),
                )
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("capture-upstream").add_argument("checkout", type=Path)
    candidate = commands.add_parser("compare")
    candidate.add_argument("fixture", type=Path)
    candidate.add_argument("binary", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "capture-upstream":
            output = capture_upstream(args.checkout)
        else:
            fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
            require(
                fixture["schema"] == 1
                and fixture["reference"] == UPSTREAM
                and set(fixture["cases"]) == {*CASES, ERROR_CASE},
                "unexpected fixture schema, reference, or cases",
            )
            output = compare(fixture["cases"], capture_candidate(args.binary))
            output["reference"] = fixture["reference"]
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        HTTPException,
        subprocess.CalledProcessError,
    ) as exc:
        parser.exit(2, f"Protocol check failed: {exc}\n")
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if output.get("shared_contract_matches", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
