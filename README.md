# oMLX-R

oMLX-R is the working name of an independent Rust inference project for Apple Silicon, inspired by [oMLX](https://github.com/jundot/omlx).

The Rust HTTP/SSE service offers a deterministic mock backend and an opt-in native MLX backend for the verified Qwen2.5-0.5B 4-bit checkpoint on Apple Silicon.

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
