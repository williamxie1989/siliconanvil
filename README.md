# oMLX-R

oMLX-R is the working name of an independent Rust inference project for Apple Silicon, inspired by [oMLX](https://github.com/jundot/omlx).

Current milestone: a local Rust development scaffold with an upstream comparison command. Inference, API compatibility, performance, and model support are future milestones.

## Development

```sh
cargo test --locked
cargo run --locked -- --version
```

Use the [project workflow](docs/project-workflow.md) to resume work across sessions and compare a pinned oMLX commit with upstream changes.
