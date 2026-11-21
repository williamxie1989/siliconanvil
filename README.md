# SiliconAnvil

SiliconAnvil is an independent Rust project for Metal-accelerated local LLM inference on Apple Silicon, inspired by [oMLX](https://github.com/jundot/omlx). Early development used the working name oMLX-R.

The Rust HTTP/SSE service offers a deterministic mock backend and an opt-in native MLX backend for the verified Qwen2.5-0.5B 4-bit checkpoint on Apple Silicon.

Version 0.1.0 is a source release under the [Apache License 2.0](LICENSE). Build locally with Cargo. The executable is named `omlx-r`. For native inference, supply the compatible Metal library and pinned checkpoint described in the [native setup guide](docs/r-004-native-mlx.md).

## Development

```sh
cargo test --locked
cargo run --locked -- --version
cargo run --locked -- serve
```

The mock service prints its loopback address. Set `BASE_URL` to that address to try it:

```sh
curl "$BASE_URL/v1/models"
curl -N "$BASE_URL/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{"model":"omlx-r-mock","messages":[{"role":"user","content":"hello"}],"stream":true}'
```

See the [mock HTTP/SSE contract](docs/r-002-http-contract.md) for supported request fields and integration tests.

Use `serve-native <checkpoint-dir>` for local Metal-backed chat completions or `native-smoke <checkpoint-dir>` to check generation and allocator cleanup. Both commands require the optional build and a verified Metal library beside the executable. See the [native MLX build and HTTP contract](docs/r-004-native-mlx.md).

Use the [project workflow](docs/project-workflow.md) to resume work across sessions and compare a pinned oMLX commit with upstream changes.

See [release readiness](docs/r-005-release-readiness.md) for the arm64 candidate build, license inventory and compatibility matrix.
