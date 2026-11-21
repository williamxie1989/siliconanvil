# Upstream protocol comparison

The comparison target is oMLX `v0.8.2`, whose release tag resolves to `5eeec7757f83bfc99eeb87abe49686d00a0bba1f`. As of 2026-11-21, `origin/main` of the separate reference repository is `ade9f20a`. This page records the source review. Measured versions are registered only after compatibility acceptance.

| Area | oMLX v0.8.2 source and tests | SiliconAnvil status and comparison focus |
| --- | --- | --- |
| Model list | [`list_models`](https://github.com/jundot/omlx/blob/5eeec7757f83bfc99eeb87abe49686d00a0bba1f/omlx/server.py#L3786) reads the engine pool and applies model visibility settings | Verified the list shape for a single pinned model. Compare `object`, model ID, and type against the same list contract |
| Non-streaming plain text | [`create_chat_completion`](https://github.com/jundot/omlx/blob/5eeec7757f83bfc99eeb87abe49686d00a0bba1f/omlx/server.py#L5016) accepts the unified chat request | Verified a single assistant reply and finish reason. Compare protocol structure and ignore response IDs, timestamps, and the exact generated text |
| SSE plain-text completion | [Upstream streaming test](https://github.com/jundot/omlx/blob/5eeec7757f83bfc99eeb87abe49686d00a0bba1f/tests/integration/test_e2e_streaming.py#L265) checks the SSE MIME type, the first frame, and the trailing `[DONE]` | Verified over TCP against the Rust mock. Compare role, text, finish reason, and terminator, using the concatenated text and event semantics as the criterion |
| SSE error and close | [`stream_chat_completion` error path](https://github.com/jundot/omlx/blob/5eeec7757f83bfc99eeb87abe49686d00a0bba1f/omlx/server.py#L6905) emits an error event and closes the engine iterator | The Rust mock verifies the next request after a client disconnect. The comparison must cover both an error followed by `[DONE]` and an early socket EOF |
| Request extensions and usage | [Request fields](https://github.com/jundot/omlx/blob/5eeec7757f83bfc99eeb87abe49686d00a0bba1f/omlx/api/openai_models.py#L333) support sampling parameters, token limits, and `stream_options`. The [usage frame](https://github.com/jundot/omlx/blob/5eeec7757f83bfc99eeb87abe49686d00a0bba1f/omlx/server.py#L7280) can follow the completion frame | The mock currently validates the minimal field set. Collect the fields real clients use before ordering extensions; tool calls, reasoning content, and usage get separate acceptance |

The next deliverable is a versioned two-sided response fixture and a repeatable comparison runner. Validate the HTTP contract against the upstream mock engine first, recording process versions, requests, response shapes, and differences separately. Isolated instances use an ephemeral port and a separate configuration directory. Assess the model, memory headroom, and startup mode only after confirming that a case needs a real model. The existing oMLX service keeps running as a separate workload.

## Executed comparison

The fixture, replay commands, and observed gaps are recorded in the [v0.8.2 protocol baseline](upstream-contract-v0.8.2.md).
