// SPDX-License-Identifier: Apache-2.0
use std::ffi::{c_int, c_void};
use std::fs;
use std::path::Path;
use std::time::Instant;

use mlex::array::synchronize;
use mlex::generate::{FinishReason, GenerateOptions, GenerateReply, Session};
use mlex::tokenizer::ChatMessage;
use serde::Serialize;
use serde_json::{json, Value};

use crate::server::worker::{Engine, JobOutput, Reply, MAX_TOKENS};
use crate::server::{Message, Role};

const MAX_WEIGHT_BYTES: u64 = 1 << 30;
const ROUNDS: usize = 2;
const RELEASE_TOLERANCE: usize = 16 << 20;
const MLX_GPU: c_int = 1;

// These declarations match the memory and device headers vendored by mlex 0.1.3.
#[repr(C)]
#[derive(Clone, Copy)]
struct MlxDevice {
    context: *mut c_void,
}

unsafe extern "C" {
    fn mlx_device_new() -> MlxDevice;
    fn mlx_get_default_device(device: *mut MlxDevice) -> c_int;
    fn mlx_device_get_type(kind: *mut c_int, device: MlxDevice) -> c_int;
    fn mlx_device_free(device: MlxDevice) -> c_int;
    fn mlx_get_active_memory(bytes: *mut usize) -> c_int;
    fn mlx_get_cache_memory(bytes: *mut usize) -> c_int;
    fn mlx_get_peak_memory(bytes: *mut usize) -> c_int;
    fn mlx_reset_peak_memory() -> c_int;
    fn mlx_clear_cache() -> c_int;
}

#[derive(Serialize)]
struct Allocator {
    active_bytes: usize,
    cache_bytes: usize,
    peak_bytes: usize,
}

fn mlx_status(operation: &str, status: c_int) -> Result<(), String> {
    if status == 0 {
        Ok(())
    } else {
        Err(format!("MLX {operation} failed with status {status}"))
    }
}

fn require_gpu() -> Result<(), String> {
    let mut kind = -1;
    // The output pointers address initialized local storage and the device is
    // freed on both success and failure before leaving this thread.
    let (get, query, free) = unsafe {
        let mut device = mlx_device_new();
        let get = mlx_get_default_device(&mut device);
        let query = if get == 0 {
            mlx_device_get_type(&mut kind, device)
        } else {
            get
        };
        (get, query, mlx_device_free(device))
    };
    mlx_status("get default device", get)?;
    mlx_status("get device type", query)?;
    mlx_status("free device", free)?;
    if kind != MLX_GPU {
        return Err("MLX did not select its Metal GPU device".into());
    }
    Ok(())
}

fn allocator() -> Result<Allocator, String> {
    let mut report = Allocator {
        active_bytes: 0,
        cache_bytes: 0,
        peak_bytes: 0,
    };
    // The C functions write size_t values to valid, uniquely borrowed fields.
    unsafe {
        mlx_status(
            "get active memory",
            mlx_get_active_memory(&mut report.active_bytes),
        )?;
        mlx_status(
            "get cache memory",
            mlx_get_cache_memory(&mut report.cache_bytes),
        )?;
        mlx_status(
            "get peak memory",
            mlx_get_peak_memory(&mut report.peak_bytes),
        )?;
    }
    Ok(report)
}

fn cleanup() -> Result<Allocator, String> {
    synchronize().map_err(|error| format!("synchronize MLX: {error}"))?;
    // This synchronous call clears the native allocator in this process.
    mlx_status("clear cache", unsafe { mlx_clear_cache() })?;
    allocator()
}

fn read_json(path: &Path) -> Result<Value, String> {
    let raw = fs::read(path).map_err(|error| format!("read {}: {error}", path.display()))?;
    serde_json::from_slice(&raw).map_err(|error| format!("parse {}: {error}", path.display()))
}

fn checkpoint_bytes(path: &Path) -> Result<u64, String> {
    if !path.is_dir() {
        return Err(format!(
            "expected a local checkpoint directory: {}",
            path.display()
        ));
    }
    let config = read_json(&path.join("config.json"))?;
    if config["model_type"] != "qwen2" {
        return Err("native MLX requires a small, single-file Qwen2 checkpoint".into());
    }
    let index = path.join("model.safetensors.index.json");
    if index.exists() {
        let index = read_json(&index)?;
        let map = index["weight_map"]
            .as_object()
            .ok_or("checkpoint index has no weight map")?;
        if map.is_empty() || map.values().any(|file| file != "model.safetensors") {
            return Err("native MLX requires a single model.safetensors weight file".into());
        }
    }
    let weights = path.join("model.safetensors");
    let file =
        fs::metadata(&weights).map_err(|error| format!("read {}: {error}", weights.display()))?;
    if !file.is_file() || !(1..=MAX_WEIGHT_BYTES).contains(&file.len()) {
        return Err("model.safetensors must be a file of at most 1 GiB".into());
    }
    for entry in path
        .read_dir()
        .map_err(|error| format!("list checkpoint: {error}"))?
    {
        let entry = entry.map_err(|error| format!("read checkpoint entry: {error}"))?;
        let name = entry.file_name();
        if name
            .to_str()
            .is_some_and(|value| value.ends_with(".safetensors"))
            && name != "model.safetensors"
        {
            return Err("native MLX does not load extra tensor files".into());
        }
    }
    Ok(file.len())
}

fn check_attention_kernel() -> mlex::Result<bool> {
    use mlex::ops::{self, AttentionMask};
    use mlex::{Array, Dtype};

    // Reproduce Qwen2.5-0.5B's grouped-query BF16 prefill shape. Constant value
    // rows make the expected causal attention output independent of position.
    const HEAD_DIM: usize = 64;
    const LENGTH: usize = 40;
    let query = Array::from_slice(&vec![0.0f32; 14 * LENGTH * HEAD_DIM], &[1, 14, 40, 64]);
    let key = Array::from_slice(&vec![0.0f32; 2 * LENGTH * HEAD_DIM], &[1, 2, 40, 64]);
    let row: Vec<f32> = (1..=HEAD_DIM).map(|value| value as f32 / 64.0).collect();
    let values = Array::from_slice(&row.repeat(2 * LENGTH), &[1, 2, 40, 64]);
    let query = ops::astype(&query, Dtype::BFloat16)?;
    let key = ops::astype(&key, Dtype::BFloat16)?;
    let values = ops::astype(&values, Dtype::BFloat16)?;
    let result =
        ops::scaled_dot_product_attention(&query, &key, &values, 0.125, AttentionMask::Causal)?;
    let flattened = ops::reshape(&result, &[-1])?.to_vec_f32()?;
    Ok(flattened.len() == 14 * LENGTH * HEAD_DIM
        && flattened
            .iter()
            .zip(row.iter().cycle())
            .all(|(actual, expected)| (actual - expected).abs() <= 0.0078125))
}

fn generate(path: &Path, prompt: &str) -> Result<(GenerateReply, Allocator), String> {
    let session = Session::load(path).map_err(|error| format!("load checkpoint: {error}"))?;
    let reply = session
        .generate_cached(
            &[ChatMessage::user(prompt)],
            None,
            GenerateOptions {
                max_tokens: MAX_TOKENS,
                enable_thinking: Some(false),
                prompt_cache: Some(false),
                ..GenerateOptions::default()
            },
            |_| true,
        )
        .map_err(|error| format!("generate with MLX: {error}"))?;
    synchronize().map_err(|error| format!("complete MLX generation: {error}"))?;
    Ok((reply, allocator()?))
}

fn prepare_metal() -> Result<Allocator, String> {
    mlex::error::install_error_handler();
    require_gpu()?;
    let attention_matches = check_attention_kernel()
        .map_err(|error| format!("run the Metal attention self-check: {error}"))?;
    if !attention_matches {
        return Err("Metal attention self-check failed; colocate the verified MLX 0.31.2 mlx.metallib with this binary".into());
    }
    cleanup()
}

fn verify_release(baseline: &Allocator, released: &Allocator) -> Result<(), String> {
    if released.active_bytes > baseline.active_bytes.saturating_add(RELEASE_TOLERANCE)
        || released.cache_bytes > baseline.cache_bytes.saturating_add(RELEASE_TOLERANCE)
    {
        return Err(format!(
            "MLX allocations did not return to baseline: {} active bytes, {} cached bytes",
            released.active_bytes, released.cache_bytes
        ));
    }
    Ok(())
}

pub fn smoke(path: &Path, prompt: &str) -> Result<(), String> {
    if prompt.trim().is_empty() || prompt.len() > 1024 {
        return Err("prompt must contain between 1 and 1024 UTF-8 bytes".into());
    }
    let bytes = checkpoint_bytes(path)?;
    let baseline = prepare_metal()?;
    let mut rounds = Vec::with_capacity(ROUNDS);
    for _ in 0..ROUNDS {
        // Reset the peak counter immediately before creating the session.
        mlx_status("reset peak memory", unsafe { mlx_reset_peak_memory() })?;
        let start = Instant::now();
        let generated = generate(path, prompt);
        let generation_ms = start.elapsed().as_millis();
        let released = cleanup();
        let (reply, loaded) = generated?;
        let released = released?;
        if reply.text.trim().is_empty() || reply.usage.completion_tokens == 0 {
            return Err("MLX generated no visible output".into());
        }
        if loaded.active_bytes <= baseline.active_bytes {
            return Err("MLX active memory did not rise while the checkpoint was loaded".into());
        }
        verify_release(&baseline, &released)?;
        let finish = match reply.finish_reason {
            FinishReason::Stop => "stop",
            FinishReason::Length => "length",
            FinishReason::ToolCalls => "tool_calls",
            FinishReason::Aborted => "aborted",
        };
        rounds.push(json!({
            "text": reply.text,
            "input_tokens": reply.usage.prompt_tokens,
            "output_tokens": reply.usage.completion_tokens,
            "finish_reason": finish,
            "load_and_generate_ms": generation_ms,
            "loaded": loaded,
            "released": released,
        }));
    }
    println!(
        "{}",
        json!({
            "device": "metal",
            "runtime": "mlex-0.1.3",
            "weight_bytes": bytes,
            "release_tolerance_bytes": RELEASE_TOLERANCE,
            "baseline": baseline,
            "rounds": rounds,
        })
    );
    Ok(())
}

pub(crate) struct HttpEngine {
    session: Session,
    baseline: Allocator,
}

impl HttpEngine {
    pub(crate) fn load(path: &Path) -> Result<Self, String> {
        checkpoint_bytes(path)?;
        let tokenizer = read_json(&path.join("tokenizer.json"))?;
        if tokenizer["decoder"]["type"] != "ByteLevel" {
            return Err("serve-native requires a Qwen2 checkpoint with a ByteLevel decoder".into());
        }
        let baseline = prepare_metal()?;
        let session = Session::load(path).map_err(|error| format!("load checkpoint: {error}"))?;
        Ok(Self { session, baseline })
    }

    fn generate_text(&self, messages: Vec<Message>, output: &JobOutput) -> Result<Reply, String> {
        let messages: Vec<_> = messages
            .into_iter()
            .map(|message| match message.role {
                Role::System => ChatMessage::system(message.content),
                Role::User => ChatMessage::user(message.content),
                Role::Assistant => ChatMessage::assistant(message.content),
            })
            .collect();
        let input = self
            .session
            .encode_chat(&messages)
            .map_err(|error| error.to_string())?;
        if output.is_cancelled() {
            return Err("generation cancelled".into());
        }
        let tokenizer = self.session.tokenizer();
        let mut ids = Vec::with_capacity(MAX_TOKENS);
        let mut sent_text = String::new();
        let mut stream_error = None;
        let mut finish_reason = "length";
        self.session
            .generate(
                &input,
                GenerateOptions {
                    max_tokens: MAX_TOKENS,
                    ..GenerateOptions::default()
                },
                |token| {
                    if output.is_cancelled() {
                        return false;
                    }
                    if token.finished {
                        finish_reason = "stop";
                        return true;
                    }
                    ids.push(token.id);
                    if !output.stream {
                        return true;
                    }
                    let delta = tokenizer
                        .decode(&ids)
                        .map_err(|error| format!("decode generated text: {error}"))
                        .and_then(|decoded| {
                            // ByteLevel decoding replaces incomplete trailing UTF-8 until more bytes arrive.
                            let stable = decoded.trim_end_matches('\u{fffd}');
                            send_delta(output, stable, &mut sent_text)
                        });
                    if let Err(error) = delta {
                        stream_error = Some(error);
                        return false;
                    }
                    true
                },
            )
            .map_err(|error| format!("generate with MLX: {error}"))?;
        if let Some(error) = stream_error {
            return Err(error);
        }
        if output.is_cancelled() {
            return Err("generation cancelled".into());
        }
        let text = tokenizer.decode(&ids).map_err(|error| error.to_string())?;
        if output.stream {
            send_delta(output, &text, &mut sent_text)?;
        }
        Ok(Reply {
            text,
            finish_reason,
        })
    }
}

fn send_delta(output: &JobOutput, text: &str, sent: &mut String) -> Result<(), String> {
    let suffix = text
        .strip_prefix(sent.as_str())
        .ok_or("decoded text changed after it was sent")?;
    if !suffix.is_empty() && !output.text(suffix.into()) {
        return Err("could not send generated text".into());
    }
    sent.push_str(suffix);
    Ok(())
}

impl Engine for HttpEngine {
    fn generate(&mut self, messages: Vec<Message>, output: &JobOutput) -> Result<Reply, String> {
        let reply = self.generate_text(messages, output);
        let synchronized =
            synchronize().map_err(|error| format!("complete MLX generation: {error}"));
        reply.and_then(|reply| synchronized.map(|()| reply))
    }

    fn shutdown(self) -> Result<(), String> {
        let Self { session, baseline } = self;
        let synchronized =
            synchronize().map_err(|error| format!("synchronize before unloading MLX: {error}"));
        drop(session);
        let released = cleanup()?;
        println!(
            "{}",
            json!({
                "event": "native_shutdown",
                "baseline": baseline,
                "released": released,
                "release_tolerance_bytes": RELEASE_TOLERANCE,
            })
        );
        synchronized?;
        verify_release(&baseline, &released)
    }
}
