# Development notes

These pages document how SiliconAnvil is built, verified, and released. They are for contributors and maintainers; user-facing documentation lives one level up in [`docs/`](../).

| Task | Page |
| --- | --- |
| R-002 | [HTTP and SSE contract](../http-contract.md) |
| R-003 | [Upstream protocol comparison](../compatibility.md) |
| R-004 | [Native MLX backend](../native-mlx.md) |
| R-005 | [Release readiness](../release-readiness.md) |
| Compatibility baseline | [oMLX v0.8.2 protocol baseline](../upstream-contract-v0.8.2.md) |
| Process | [Architecture and release decisions](workflow.md) |

## Roadmap

`tools/rust_evolution/tasks.tsv` is the machine-readable task graph: task ID, prerequisites, and acceptance goal. The ledger tool in the same directory records events against those IDs.

- R-001 to R-006: independent repository, minimal HTTP/SSE contract, pinned protocol comparison, native MLX inference, release packaging, first public release.
- EV-002, EV-003, GATE-P0: current-version baseline and compatibility evidence.
- EV-101 to EV-104, GATE-P1: read-only residency observation and memory stability.
- EV-201 to EV-204: versioned supervisor protocol and fault isolation.
- EV-301 to EV-304: scheduling policy contract and overhead measurement.
- EV-401, EV-402, EV-501: expert and SSD hot spots, PoC thresholds, architecture decision.

Only completed work has evidence in the ledger. Tasks without evidence are plans, not claims.
