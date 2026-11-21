// SPDX-License-Identifier: Apache-2.0
use std::path::PathBuf;
use std::process::{Command, Output};
use std::time::{SystemTime, UNIX_EPOCH};

#[cfg(not(all(feature = "native-mlx", target_os = "macos", target_arch = "aarch64")))]
#[test]
fn native_smoke_requires_the_opt_in_backend() {
    let output = Command::new(env!("CARGO_BIN_EXE_omlx-r"))
        .args(["native-smoke", "missing-checkpoint"])
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("native-mlx"));
}

#[test]
fn native_http_reports_a_startup_error_before_announcing_a_port() {
    let output = Command::new(env!("CARGO_BIN_EXE_omlx-r"))
        .args(["serve-native", "missing-checkpoint", "--port", "0"])
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    let stderr = String::from_utf8_lossy(&output.stderr);
    if cfg!(all(
        feature = "native-mlx",
        target_os = "macos",
        target_arch = "aarch64"
    )) {
        assert!(stderr.contains("local checkpoint directory"), "{stderr}");
    } else {
        assert!(
            stderr.contains("requires an Apple Silicon macOS build"),
            "{stderr}"
        );
    }
}

#[cfg(all(feature = "native-mlx", target_os = "macos", target_arch = "aarch64"))]
#[test]
fn native_smoke_rejects_a_missing_local_checkpoint() {
    let output = Command::new(env!("CARGO_BIN_EXE_omlx-r"))
        .args(["native-smoke", "missing-checkpoint"])
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("local checkpoint directory"));
}

#[cfg(all(feature = "native-mlx", target_os = "macos", target_arch = "aarch64"))]
#[test]
fn native_smoke_rejects_a_hidden_extra_tensor_file_before_loading() {
    let directory = Reference::scratch();
    std::fs::write(directory.0.join("config.json"), r#"{"model_type":"qwen2"}"#).unwrap();
    std::fs::write(directory.0.join("model.safetensors"), b"placeholder").unwrap();
    std::fs::write(directory.0.join(".safetensors"), b"extra").unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_omlx-r"))
        .arg("native-smoke")
        .arg(&directory.0)
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("extra tensor files"));
}

#[cfg(all(feature = "native-mlx", target_os = "macos", target_arch = "aarch64"))]
#[test]
#[ignore = "requires a local small Qwen2 MLX checkpoint and Apple Silicon hardware"]
fn native_smoke_generates_and_releases_the_checkpoint() {
    let checkpoint = std::env::var_os("OMLX_R_TEST_CHECKPOINT")
        .expect("point OMLX_R_TEST_CHECKPOINT at the local Qwen2 checkpoint");
    let output = Command::new(env!("CARGO_BIN_EXE_omlx-r"))
        .arg("native-smoke")
        .arg(checkpoint)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let report: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    let rounds = report["rounds"].as_array().unwrap();
    assert_eq!(report["device"], "metal");
    assert_eq!(rounds.len(), 2);
    let baseline = report["baseline"]["active_bytes"].as_u64().unwrap();
    let tolerance = report["release_tolerance_bytes"].as_u64().unwrap();
    for round in rounds {
        assert_eq!(round["text"], "2 + 2 equals 4.");
        assert_eq!(round["output_tokens"], 9);
        assert_eq!(round["finish_reason"], "stop");
        assert!(round["loaded"]["active_bytes"].as_u64().unwrap() > baseline);
        assert!(round["released"]["active_bytes"].as_u64().unwrap() <= baseline + tolerance);
    }
}

struct Reference(PathBuf);

impl Reference {
    fn new() -> Self {
        let reference = Self::scratch();
        reference.git(&["init", "-q", "-b", "main"]);
        reference
    }

    fn scratch() -> Self {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!("omlx-r-test-{}-{stamp}", std::process::id()));
        std::fs::create_dir(&path).unwrap();
        Self(path)
    }

    fn git(&self, args: &[&str]) -> String {
        let output = Command::new("git")
            .current_dir(&self.0)
            .args([
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "commit.gpgSign=false",
            ])
            .args(args)
            .output()
            .unwrap();
        assert!(output.status.success(), "{:?}", output);
        String::from_utf8(output.stdout).unwrap().trim().to_owned()
    }

    fn commit(&self, subject: &str) -> String {
        self.git(&["commit", "-q", "--allow-empty", "-m", subject]);
        self.git(&["rev-parse", "HEAD"])
    }

    fn status(&self, baseline: &str) -> Output {
        Command::new(env!("CARGO_BIN_EXE_omlx-r"))
            .arg("upstream-status")
            .arg(&self.0)
            .args(["--baseline", baseline])
            .output()
            .unwrap()
    }
}

impl Drop for Reference {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

#[test]
fn reports_the_package_version() {
    let output = Command::new(env!("CARGO_BIN_EXE_omlx-r"))
        .arg("--version")
        .output()
        .unwrap();
    assert!(output.status.success());
    assert_eq!(
        String::from_utf8(output.stdout).unwrap(),
        format!("omlx-r {}\n", env!("CARGO_PKG_VERSION"))
    );
}

#[test]
fn rejects_reference_without_git_history() {
    let output = Command::new(env!("CARGO_BIN_EXE_omlx-r"))
        .args(["upstream-status", "Cargo.toml"])
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(String::from_utf8(output.stderr).unwrap().contains("git"));
}

#[test]
fn reports_new_upstream_commits_alongside_the_pinned_reference() {
    let repo = Reference::new();
    let baseline = repo.commit("Pinned reference");
    let tip = repo.commit("New upstream API behavior");
    repo.git(&["update-ref", "refs/remotes/origin/main", &tip]);
    let output = repo.status(&baseline);
    assert!(output.status.success(), "{:?}", output);
    let result = String::from_utf8(output.stdout).unwrap();
    assert!(result.contains(&format!("Reference: {baseline}")));
    assert!(result.contains(&format!("Upstream:  {tip}")));
    assert!(result.contains("Upstream-only commits: 1"));
    assert!(result.contains("New upstream API behavior"));
}

#[test]
fn surfaces_divergence_and_uses_a_failure_exit_code() {
    let repo = Reference::new();
    let common = repo.commit("Common root");
    let baseline = repo.commit("Original baseline");
    repo.git(&["switch", "-q", "-c", "alternate", &common]);
    let tip = repo.commit("Rewritten upstream");
    repo.git(&["update-ref", "refs/remotes/origin/main", &tip]);
    let output = repo.status(&baseline);
    assert!(!output.status.success());
    let result = String::from_utf8(output.stdout).unwrap();
    assert!(result.contains("diverged"));
    assert!(result.contains("Reference-only commits: 1"));
}
