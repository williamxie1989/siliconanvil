# Architecture and release decisions

## Repository and first-release positioning

The long-term goal is an inference service written in Rust, with its own repository, version, and release cadence. oMLX serves as a compatibility reference. If the goal shifts to continuously distributing the existing Python version with a small Rust extension, a fork that shares Git history absorbs upstream commits more easily. The current Rust service is defining its own API, runtime, and packaging boundaries, so an independent history fits better.

The phase order is the Rust HTTP/SSE service with a mock backend, a pinned oMLX reference behavior, one small native MLX model, then an independent release. During development an isolated oMLX instance can provide an external comparison. The first public version that claims Rust inference must run at least one real model through the Rust entry point and the native MLX path. Quantization, MTP, SSD offload, and long-context behavior are verified item by item for DeepSeek V4.1. The initial path uses its own binary, configuration directory, port, and cache location.

The first R-002 increment is `GET /v1/models` and `POST /v1/chat/completions` on an ephemeral loopback port. Fixed text and scripted delays drive the mock backend to verify the first visible output, one normal termination, and an abnormal stream break. The historical replay from EV-002 is reused directly; the remaining hardware baseline waits for the compatibility reference chosen in R-003.

## Tracking upstream

Keep three independent facts. `upstream/reference-commit` is the starting point for diff analysis. `origin/main` of the separate reference repository is the most recently fetched upstream head. A version that passes contract or hardware tests is confirmed by evidence. The initial analysis point came from the original roadmap; no verified compatible version exists yet.

Prepare a comparison-only Git repository outside this repository first:

```sh
reference="$HOME/omlx-notes/rust-evolution/upstream/omlx.git"
mkdir -p "$(dirname "$reference")"
git clone --bare --single-branch --branch main --no-tags \
  https://github.com/jundot/omlx.git "$reference"
git -C "$reference" config remote.origin.fetch '+refs/heads/main:refs/remotes/origin/main'
```

On each round, upstream release, or dependency upgrade, fetch first and then inspect the diff after the pinned point:

```sh
reference="$HOME/omlx-notes/rust-evolution/upstream/omlx.git"
git -C "$reference" fetch --no-tags origin
cargo run --locked -- upstream-status "$reference"
```

For commits that touch the API, models, caches, MLX/Metal, or other dependencies, record the upstream SHA, the affected contract, and the decision in [upstream change decisions](../../upstream/decisions.md). Prefer a protocol or replay case for API changes. Bind model adaptation to the checkpoint, format, and version. Bind ABI changes to the MLX/Metal and binding versions, then verify on hardware. After the selected change passes its acceptance criteria, advance the baseline or migrate the content in one reviewable commit. If Git reports a divergence or the reference moves backwards, check the remote history and the baseline first.

## Cross-session project management

```sh
python3 tools/rust_evolution/project.py resume
python3 tools/rust_evolution/project.py status
python3 tools/rust_evolution/project.py history --query upstream
```

The ledger defaults to `~/omlx-notes/rust-evolution/progress.sqlite3`, outside the repository; override it with `--db`. Rust tasks use R numbers; the original EV tasks keep their historical evidence and dependencies. Record each entry with `note --summary ... --evidence ... --next ...`. Finish a task with `finish --summary ... --evidence ...`. To record another checkout, pass `--repo PATH` before the subcommand so the tool reads the Git HEAD from that checkout. Evidence paths resolve against the current working directory.

## Release checklist

- Confirm the public name and visual identity, and check whether the upstream project name may be used. State the independent maintenance and the lineage in the public description. Section 6 of Apache-2.0 governs project-name and trademark grants and needs separate handling.
- Record the exact source and license for every imported piece of code, test, model recipe, or binding. When distributing third-party code, keep the applicable copyright, license, modification notices, and NOTICE. Check the distribution terms of model weights separately.
- Record the measured compatibility range by client, streaming event, usage, tool calls, cancellation, error semantics, and model recipe. Distinguish Rust native inference from the upstream reference version in the comparison environment.
- Verify hardware CI and releases separately. Generic Rust builds and mock tests fit ordinary CI. Metal, unified memory, and performance verification bind to real Apple Silicon, the OS, MLX, the model, and the settings. Before the first distribution, state the supported architecture, the minimum macOS version, the binary format, and the signing arrangement.
- Give a new service its own default directories and configuration format. If cache formats need to interoperate, verify versioning and read-only behavior first, then design the write migration separately.

GitHub serves as the collaboration entry point for code, public issues, and releases. The local ledger keeps runtime evidence and session state. Public entries link through stable R/EV identifiers, and public reports use redacted excerpts.
