# SiliconAnvil project rules

Write all new code comments, docstrings, and project documentation in English. When extending an existing document or comment, write the added material in English.

Work continues from the [architecture and release decisions](docs/development/workflow.md). Start a new session with `python3 tools/project/project.py resume`, and write evidence and the next action back to the ledger before stopping.

Reference oMLX through a separate checkout pinned to a fixed SHA. Every adopted upstream change records its source, porting approach, applicable scope, and test results. The original oMLX service, environment, settings, and caches belong to a separate runtime.

Verify new behavior with a small fixture or a mock service first. Verify real inference on an isolated port, with a separate configuration directory and enough memory headroom. Public releases describe only model and API capabilities that already exist. Keep personal evidence, model weights, caches, and the ledger outside the repository.
