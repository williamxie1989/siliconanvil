# Native MLX backend

The first native path is a local, one-shot Rust command behind the `native-mlx` Cargo feature. It uses the pinned MIT-licensed [mlex 0.1.3](https://crates.io/crates/mlex/0.1.3) runtime for Qwen tokenization and decoder execution. This runtime vendors MLX and its C API. The build is independent of the running Python oMLX service and its MLX wheel.

The smoke runs bounded text generation on a small local Qwen checkpoint, then synchronizes, drops the session, clears the process-local allocator cache, and repeats. Its JSON report records text, token counts, and allocator bytes before load, after generation with the session still live, and after cleanup. Both cycles must return live and cached allocation to the startup baseline within tolerance.

The first milestone covers the Rust command and memory lifecycle. The next step serves the same verified checkpoint through the existing HTTP routes. Distribution of the Metal kernel library remains a release task.

## Native HTTP contract

Run `target/debug/siliconanvil serve-native /path/to/local-checkpoint --port 0` with the same native build and verified Metal library. The service announces its IPv4 loopback address after preflight and model load. `GET /v1/models` lists `siliconanvil-native`. Send that ID in `POST /v1/chat/completions` with the text-only request fields used by the mock. Native requests accept up to 32 messages and 4,096 UTF-8 content bytes in total. The Qwen2 checkpoint must have a ByteLevel decoder.

One dedicated thread loads, uses, and drops the session. It admits one generation at a time. Concurrent requests receive HTTP 503 with `worker_busy` before SSE headers. The model list stays available during generation. Requests use greedy decoding, fresh generation caches, and a 32-token cap. Output is plain text with `stop` or `length`. A successful stream emits the assistant role, text deltas, one finish frame, and one `[DONE]`. The bounded output channel uses nonblocking sends. A generation failure returns HTTP 500 for JSON or an SSE error followed by EOF for streaming.

Closing the HTTP response cancels generation at the next decoder callback. SIGINT or SIGTERM closes admission and requests the same cooperative cancellation. The server allows up to two seconds to drain HTTP, then closes remaining connections and joins the worker. The worker synchronizes MLX, drops the session, clears the allocator cache, and reports live and cached bytes against the startup baseline. A running Metal operation must return before its thread can be joined.

Verify admission, cancellation, and worker cleanup with a scripted backend first. Then run the native HTTP integration tests on a kernel-assigned port with the pinned checkpoint, comparing JSON with SSE and checking allocator counters after both idle and in-flight shutdown.

```sh
cargo test --locked
SILICONANVIL_TEST_BINARY=target/debug/siliconanvil \
  SILICONANVIL_TEST_CHECKPOINT=/path/to/local-checkpoint \
  python3 -m unittest discover -s benchmarks/contract -p test_native_http.py
```

Run the Python command after rebuilding with `--features native-mlx` and colocating the verified Metal library.

On the Mac17,6 with macOS 26.5.1, JSON and SSE produced the same English and Chinese text. The counting prompt reached the generation limit and emitted `length`. Five runs of idle SIGINT and five runs of SIGTERM after the first visible delta each returned live and cached MLX allocation to zero. The interrupted stream emitted an error and closed before the success frames.

## Build and run

Use Apple Silicon, Xcode with the Metal toolchain, and CMake. Build the optional target with a bounded job count:

```sh
CMAKE_BUILD_PARALLEL_LEVEL=4 cargo build --locked --features native-mlx -j 4
```

For the verified configuration, extract `mlx/lib/mlx.metallib` from the Apple Silicon `mlx-metal==0.31.2` wheel. Colocate it with the native binary. Only the Metal library is used at runtime. Verify its SHA-256 is `8c8bfcece8c0610745b68879771e5aa1b92b29fa5e17172e5508e4f5153d8d15`.

```sh
unzip -p /path/to/mlx_metal-0.31.2-macos-arm64.whl mlx/lib/mlx.metallib > target/debug/mlx.metallib
shasum -a 256 target/debug/mlx.metallib
target/debug/siliconanvil native-smoke /path/to/local-checkpoint
```

The command accepts a UTF-8 prompt of up to 1,024 bytes with `--prompt`. It allows a Qwen2 checkpoint with one `model.safetensors` file of at most 1 GiB. It confirms that MLX selected the Metal GPU, disables prefix-cache reuse, and uses greedy decoding with a 32-token cap. It checks both live and cached allocator bytes after each cleanup with a 16 MiB tolerance.

Run the opt-in hardware regression with the pinned checkpoint and the colocated Metal library:

```sh
SILICONANVIL_TEST_CHECKPOINT=/path/to/local-checkpoint \
  cargo test --locked --features native-mlx native_smoke_generates_and_releases_the_checkpoint -- --ignored
```

The native dependency is pinned as `mlex=0.1.3`. Its vendored `mlx/version.h` reports MLX 0.31.2. CMake may fetch additional headers and sources during its first build. When a required download is unavailable, use the matching source revisions via `FETCHCONTENT_SOURCE_DIR_JSON` and `FETCHCONTENT_SOURCE_DIR_FMT` in a local CMake toolchain file. Keep this file and model weights outside the repository.

The smoke checkpoint is [mlx-community/Qwen2.5-0.5B-Instruct-4bit at a5339a4](https://huggingface.co/mlx-community/Qwen2.5-0.5B-Instruct-4bit/tree/a5339a4131f135d0fdc6a5c8b5bbed2753bbe0f3). Its weight file is 278,064,920 bytes with SHA-256 `ddffab9cbc7bf6dde941c6724841eeca8981fcfa81ca20ff8efff1396326d153`. The checkpoint is supplied separately from the build.

## Hardware result

On a Mac17,6 running macOS 26.5.1, the two cycles produced `2 + 2 equals 4.` in nine tokens and stopped. Each measured 281,161,472 live allocator bytes after generation and a peak of 368,779,348 bytes. Both live and cached allocator bytes returned to zero after each cycle. These counters describe the process-local MLX allocator.

The default CMake-built Metal library produced incorrect output on the tested system. The Rust and MLX-LM prompt token IDs matched. Layer zero matched through projection and RoPE, then the BF16 grouped-query attention output differed. With the same Rust executable and the official MLX 0.31.2 library colocated, the sampled attention values and smoke text matched the reference. The exact compiler or kernel cause remains open. The command runs a small numerical attention check before loading the checkpoint and rejects the failing library. The release milestone must validate the packaged executable and Metal library together.
