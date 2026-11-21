# oMLX-R

oMLX-R is the working name of an independent Rust inference project for Apple Silicon, inspired by [oMLX](https://github.com/jundot/omlx).

Current milestone: a local Rust HTTP/SSE service with a deterministic mock backend and an upstream comparison command. Inference, oMLX compatibility validation, and performance testing are future milestones.

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

Use the [project workflow](docs/project-workflow.md) to resume work across sessions and compare a pinned oMLX commit with upstream changes.
