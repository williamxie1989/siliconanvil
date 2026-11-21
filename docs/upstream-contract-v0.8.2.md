# oMLX v0.8.2 protocol baseline

Reference: oMLX [v0.8.2](https://github.com/jundot/omlx/tree/5eeec7757f83bfc99eeb87abe49686d00a0bba1f). The capture executes the pinned FastAPI HTTP routes in a dedicated Python process with the upstream mock engine and pool. The Rust comparison starts its own process on a kernel-assigned IPv4 loopback port.

| Case | Result |
| --- | --- |
| `GET /v1/models` | The shared list and model object match. The upstream model includes `max_model_len` and `model_type`. The ownership labels differ by project. |
| Non-streaming chat | The shared completion, assistant message, and `stop` reason match. The reference includes a usage object. Each mock produces its own text. |
| Plain-text SSE | The shared assistant role, visible text, `stop`, and final `[DONE]` contract matches. The upstream emitted two role frames before content. The Rust mock emitted one. |
| Engine error during SSE | The reference emitted partial text, a `server_error` event, then `[DONE]`, with no success finish reason. The Rust mock has no injected engine error case. |

The fixture removes volatile response IDs, timestamps, and timing values. The capture still checks that each stream has a stable response ID and one final `[DONE]`. Text, usage-field names, and frame counts remain visible in the report. These results apply to the shared mock protocol and error fixture. Live cancellation, a model-backed server, performance, tool calls, and usage parity have separate acceptance criteria.

## Reproduce

Build and compare against the checked-in fixture from the SiliconAnvil checkout:

```sh
cargo build --locked
python3 benchmarks/contract/protocol_contract.py compare \
  benchmarks/contract/fixtures/omlx-v0.8.2-contract.json target/debug/siliconanvil
SILICONANVIL_TEST_BINARY=target/debug/siliconanvil python3 -m unittest discover -s benchmarks/contract
```

To recapture the reference, prepare a clean checkout of the exact commit and use a Python environment with the pinned oMLX runtime and test dependencies. The fixture records the Python and FastAPI versions used for this capture.

```sh
ref="$HOME/.siliconanvil/upstream/v0.8.2"
python3 benchmarks/contract/protocol_contract.py capture-upstream "$ref" > /tmp/omlx-reference.json
diff -u benchmarks/contract/fixtures/omlx-v0.8.2-contract.json /tmp/omlx-reference.json
```

Capture sets a temporary home and oMLX base path, clears inherited oMLX settings, and routes only HTTP scopes. This bypasses upstream lifespan startup, model loading, and cluster shim publication. The captured mock engine exception is intentional and is checked by its partial text and error message.
