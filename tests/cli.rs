// SPDX-License-Identifier: Apache-2.0
use std::path::PathBuf;
use std::process::{Command, Output};
use std::time::{SystemTime, UNIX_EPOCH};

struct Reference(PathBuf);

impl Reference {
    fn new() -> Self {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!("omlx-r-test-{}-{stamp}", std::process::id()));
        std::fs::create_dir(&path).unwrap();
        let reference = Self(path);
        reference.git(&["init", "-q", "-b", "main"]);
        reference
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
