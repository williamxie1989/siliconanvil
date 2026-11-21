# Release readiness

The acceptance artifact is a local Apple Silicon release candidate for the native text service. Its archive contains the release executable, the verified MLX 0.31.2 Metal kernel library beside it, and the applicable license and provenance records. Keep the archive, the pinned checkpoint, and machine-specific evidence outside the repository.

## Candidate format and scope

The tar archive extracts to `siliconanvil-native/` with the `siliconanvil` executable, `mlx.metallib`, `manifest.json`, and license and release documentation. The executable uses macOS system libraries. The model is supplied separately as a local checkpoint. Support evidence covers the pinned Qwen2.5-0.5B-Instruct-4bit revision and the Mac17,6 on macOS 26.5.1. The Mach-O deployment target is macOS 26.0, matching the platform tag of the verified Metal wheel. Other macOS releases and Apple Silicon models require hardware verification.

`SiliconAnvil` is the public name; the crate and executable are both named `siliconanvil`. The project uses the oMLX name only descriptively, to identify the protocol reference it is compared against. Apache-2.0 §6 grants no rights to the upstream project name, so the public description states the independent maintenance and the lineage instead of relying on the name. The pinned oMLX v0.8.2 checkout provides a protocol reference and a captured fixture. The binary and archive contain no upstream oMLX server code or branding assets. [Apache-2.0 §§4 and 6](https://www.apache.org/licenses/LICENSE-2.0).

The candidate uses the linker-generated ad hoc signature for local verification. For a Developer ID distribution, sign the final executable with a Developer ID Application certificate, Hardened Runtime and a secure timestamp. Use Apple's notarization workflow for the chosen distribution container and retest the signed, downloaded artifact under Gatekeeper. [Apple's macOS notarization documentation](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution).

## Third-party provenance

| Component | Pinned input and notice |
| --- | --- |
| SiliconAnvil | Apache-2.0 project license. The manifest records the executable source commit and lockfile digest. |
| Rust dependencies | The locked arm64 `native-mlx` normal dependency graph supplies `CARGO-LICENSES.tsv` and each crate's published license files. The package also retains the Oniguruma notices from `onig_sys` and the two embedded MIT notices from `esaxx-rs`. |
| mlex and vendored MLX / MLX-C | mlex 0.1.3 is pinned by Cargo.lock. Keep its license and the two vendored MIT licenses. |
| Native CMake inputs | The MLX build uses Apple's `metal-cpp_26.zip`, gguflib at `8fa6eb65236618e28fd7710a0fba565f7faa1848`, JSON v3.11.3 and fmt 12.1.0. The archive retains their notices from the CMake build inputs. |
| Metal kernels | The MIT-licensed `mlx_metal-0.31.2-py3-none-macosx_26_0_arm64.whl` published from MLX commit `68cf2fddd8de5edd8ab3d926391772b2e2cedad8`. The packager checks the wheel and extracted library digests, and includes the wheel's license. [PyPI file and attestation](https://pypi.org/project/mlx-metal/0.31.2/#mlx_metal-0.31.2-py3-none-macosx_26_0_arm64.whl). |
| Toolchain runtimes | Retain the Rust Standard Library copyright report for the compiler used and the upstream compiler-rt license and LLVM exceptions. Apple clang links its builtins archive for MLX. [Rust copyright](https://github.com/rust-lang/rust/blob/1.95.0/COPYRIGHT) and [compiler-rt license](https://github.com/llvm/llvm-project/blob/llvmorg-21.1.0/compiler-rt/LICENSE.TXT). |
| Qwen checkpoint | The [pinned MLX Community model card](https://huggingface.co/mlx-community/Qwen2.5-0.5B-Instruct-4bit/tree/a5339a4131f135d0fdc6a5c8b5bbed2753bbe0f3) identifies Apache-2.0. The binary archive ships separately from model files. |

## Build and verify

Set `wheel`, `rust_copyright`, `compiler_rt_license`, and `checkpoint` to the exact local inputs described above. Run the build from the repository root. The candidate uses a build directory outside the home directory and source-path remapping. If the CMake source downloads are unavailable, use the pinned source overrides described in the [native MLX build notes](native-mlx.md).

```sh
build="$(mktemp -d /private/tmp/siliconanvil-release.XXXXXX)"
candidate=/path/to/siliconanvil-native-darwin-arm64-candidate.tar.gz
MACOSX_DEPLOYMENT_TARGET=26.0 CMAKE_BUILD_PARALLEL_LEVEL=4 CARGO_TARGET_DIR="$build" \
  RUSTFLAGS="--remap-path-prefix=$HOME=/build-home --remap-path-prefix=$PWD=/source" \
  CFLAGS="-ffile-prefix-map=$HOME=/build-home" CXXFLAGS="-ffile-prefix-map=$HOME=/build-home" \
  cargo build --locked --release --target aarch64-apple-darwin --features native-mlx -j 4
release="$build/aarch64-apple-darwin/release"
python3 tools/project/package_native.py \
  --binary "$release/siliconanvil" --wheel "$wheel" \
  --cmake-build "$release"/build/mlex-*/out/build \
  --rust-copyright "$rust_copyright" --compiler-rt-license "$compiler_rt_license" \
  --source-commit "$(git rev-parse HEAD)" --output "$candidate"

verify="$(mktemp -d)"
tar -xzf "$candidate" -C "$verify"
binary="$verify/siliconanvil-native/siliconanvil"
(cd "$verify" && "$binary" native-smoke "$checkpoint")
SILICONANVIL_TEST_BINARY="$binary" SILICONANVIL_TEST_CHECKPOINT="$checkpoint" \
  python3 -m unittest discover -s benchmarks/contract
```

Verify from a fresh working directory with the build-tree Metal libraries unavailable. The package manifest records architecture, the Mach-O minimum version, linked system libraries, signature and binary digests. Retain separate evidence for the OS and hardware observed during execution.

## Compatibility matrix

| Area | Packaged native service | Pinned oMLX v0.8.2 comparison |
| --- | --- | --- |
| Clients | Tested using `curl`, Python `urllib` and the project's SSE probe over TCP. Third-party SDK compatibility remains unverified. | Python HTTP capture with the pinned FastAPI routes and mock engine. |
| Models | `GET /v1/models` lists `siliconanvil-native`. The pinned Qwen checkpoint is the only model with hardware evidence. | Shared list and model shape verified with the Rust mock. The reference adds model metadata. |
| Chat JSON | Text requests and assistant replies verified on the packaged binary. The minimum request has `model` and `messages`. | Shared assistant reply and finish shape verified with the Rust mock. |
| SSE | Assistant role, text deltas, one `stop` or `length` finish, and one final `[DONE]`. English and Chinese text match JSON. | Reference mock emits two initial role frames. Rust emits one. |
| Usage and tools | The current contract is plain text. The strict parser rejects extended request fields. | Usage and tool-call parity remain separate acceptance work. |
| Cancellation and admission | Disconnect permits another request. Overlapping generation returns 503. SIGTERM during generation cancels the stream and produces an error. | Live cancellation and overloaded native generation have no cross-project equivalence evidence. |
| Error termination | Verified SSE error followed by EOF when SIGTERM interrupts a stream. The JSON HTTP 500 generation-failure response is defined by the implementation. | The reference mock engine failure emits an error followed by `[DONE]`. |

See the [captured protocol baseline](upstream-contract-v0.8.2.md) for fixture details and the [native HTTP contract](native-mlx.md) for limits and shutdown behavior.
