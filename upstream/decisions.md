# Upstream change decisions

First review on 2026-11-21. Reference point `e1a402cf`; observed `ade9f20a` (v0.8.2), with 11 new upstream commits. No code was ported and no compatibility verification was completed for this window.

| Upstream commit | Scope | Decision |
| --- | --- | --- |
| `e095f9d5` | Shape applicability conditions for the DeepSeek V4 hc kernel | Keep for review during native DeepSeek adaptation |
| `fd240293` | Qwen4 image positional encoding | Build a fixture once the VLM enters the supported range |
| `24e5f905`, `13fd0c9d`, `9d0f474b` | Inkling 2 Mini models, quantization, and ModelOpt loading | Keep for model-format verification |
| `2cf8fce6` | Hy4 family, MTP, and quantization | Keep for model-scope evaluation |
| Remaining 5 | Version, Formula, CUDA helper versions | Record as release background of the reference project |

The next review appends the date, comparison range, commits, affected contracts, and the adopt-or-defer rationale. Compatibility conclusions must link to actual verification evidence.
