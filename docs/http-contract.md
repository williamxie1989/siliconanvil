# Minimal HTTP and SSE contract

`siliconanvil serve` listens on IPv4 loopback. The system assigns the port by default; `--port` sets it explicitly. The service announces the address it actually bound and exposes one fixed-output model, `siliconanvil-mock`.

- `GET /v1/models` returns one model. `POST /v1/chat/completions` accepts `model`, `messages`, and an optional `stream`. Messages support plain-text system, user, and assistant roles. A request needs at least one message. An unknown model returns 404; invalid requests and fields outside the current contract return OpenAI-style 4xx error objects.
- Non-streaming responses return an assistant message and `stop`. Streaming responses reuse the same text and emit the role, text deltas, one `finish_reason=stop`, and one `[DONE]`. A single response uses a consistent completion ID, model, and creation time. Mock deltas are generated on demand by the response stream.
- The SSE probe measures from before the HTTP call to the first visible text delta on a monotonic clock. Response-header wait, role frames, and first-output latency are included in the measurement. The probe requires exactly one finish reason and one `[DONE]`, and checks whether frames still arrive after the HTTP body ends. Total duration is sampled when `[DONE]` arrives.

The implementation order was real-TCP failure cases, then the Rust routes and responses, then the SSE probe using the historical measurement definition. Acceptance uses the process-assigned ephemeral port and a local Python fake server, covering header latency, normal completion, and a truncated stream. Model weights, inference throughput, upstream oMLX compatibility, and live cancellation belong to later tasks.

## Verification

```sh
cargo build --locked
SILICONANVIL_TEST_BINARY=target/debug/siliconanvil python3 -m unittest discover -s benchmarks/rust_evolution
cargo test --locked
```

The live TCP tests cover the model list, a non-streaming reply, SSE frame order, error responses, and a request after a client disconnect. The fake server delays response headers and the first text separately to validate the first-token timing definition, and fails the probe when the connection closes early.

To measure a single request, run `python3 benchmarks/rust_evolution/sse_probe.py "$BASE_URL" request.json`. The probe reports TTFT, total duration, finish reason, delta count, and the concatenated text.
