// SPDX-License-Identifier: Apache-2.0
use std::env;
use std::path::Path;
use std::process::{Command, ExitCode};

#[cfg(all(feature = "native-mlx", target_os = "macos", target_arch = "aarch64"))]
mod native;
mod server;

const REFERENCE: &str = include_str!("../upstream/reference-commit");
const UPSTREAM_MAIN: &str = "refs/remotes/origin/main";
const USAGE: &str = "Usage: omlx-r --version\n       omlx-r upstream-status <reference-repo> [--baseline <commit-sha>]\n       omlx-r serve [--port <port>]\n       omlx-r native-smoke <checkpoint-dir> [--prompt <text>]";

fn git(repo: &Path, args: &[&str]) -> Result<String, String> {
    let output = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(args)
        .output()
        .map_err(|error| format!("could not run git: {error}"))?;
    if !output.status.success() {
        return Err(format!(
            "git {} failed: {}",
            args.join(" "),
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_owned())
}

fn upstream_status(repo: &Path, baseline: &str) -> Result<ExitCode, String> {
    if baseline.len() != 40 || !baseline.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err("baseline must be a full 40-character Git SHA".into());
    }
    let shallow = git(repo, &["rev-parse", "--is-shallow-repository"])?;
    if shallow == "true" {
        return Err("git reference is shallow; full ancestry is required".into());
    }
    let reference = git(
        repo,
        &["rev-parse", "--verify", &format!("{baseline}^{{commit}}")],
    )?;
    let upstream = git(
        repo,
        &[
            "rev-parse",
            "--verify",
            &format!("{UPSTREAM_MAIN}^{{commit}}"),
        ],
    )?;
    let comparison = format!("{reference}...{upstream}");
    let counts = git(repo, &["rev-list", "--left-right", "--count", &comparison])?;
    let (reference_only, upstream_only) = counts
        .split_once('\t')
        .and_then(|(left, right)| Some((left.parse::<u64>().ok()?, right.parse::<u64>().ok()?)))
        .ok_or("git returned invalid commit counts")?;
    println!("Reference: {reference}");
    println!("Upstream:  {upstream}");
    println!("Reference-only commits: {reference_only}");
    println!("Upstream-only commits: {upstream_only}");
    match (reference_only, upstream_only) {
        (0, 0) => println!("Status: up to date"),
        (0, _) => {
            let range = format!("{reference}..{upstream}");
            let commits = git(
                repo,
                &["log", "--max-count=20", "--format=%h %s", &range, "--"],
            )?;
            println!("Status: ahead of reference\nNewest upstream changes (up to 20):\n{commits}");
        }
        (_, 0) => {
            println!("Status: upstream is behind reference; check the fetched ref");
            return Ok(ExitCode::FAILURE);
        }
        _ => {
            println!("Status: diverged; review upstream history before advancing the reference");
            return Ok(ExitCode::FAILURE);
        }
    }
    Ok(ExitCode::SUCCESS)
}

fn run() -> Result<ExitCode, String> {
    let args: Vec<_> = env::args_os().skip(1).collect();
    match args.as_slice() {
        [flag] if flag == "--version" => println!("omlx-r {}", env!("CARGO_PKG_VERSION")),
        [flag] if flag == "--help" => println!("{USAGE}"),
        [command, path] if command == "native-smoke" => {
            native_smoke(Path::new(path), "What is 2 + 2? Answer briefly.")?;
        }
        [command, path, flag, prompt] if command == "native-smoke" && flag == "--prompt" => {
            native_smoke(
                Path::new(path),
                prompt.to_str().ok_or("prompt must be UTF-8")?,
            )?;
        }
        [command] if command == "serve" => serve(0)?,
        [command, flag, value] if command == "serve" && flag == "--port" => {
            let port = value
                .to_str()
                .and_then(|value| value.parse::<u16>().ok())
                .ok_or("port must be an integer between 0 and 65535")?;
            serve(port)?;
        }
        [command, path] if command == "upstream-status" => {
            return upstream_status(Path::new(path), REFERENCE.trim());
        }
        [command, path, flag, baseline] if command == "upstream-status" && flag == "--baseline" => {
            return upstream_status(
                Path::new(path),
                baseline.to_str().ok_or("baseline must be a Git SHA")?,
            );
        }
        _ => return Err(USAGE.into()),
    }
    Ok(ExitCode::SUCCESS)
}

#[cfg(all(feature = "native-mlx", target_os = "macos", target_arch = "aarch64"))]
fn native_smoke(path: &Path, prompt: &str) -> Result<(), String> {
    native::smoke(path, prompt)
}

#[cfg(not(all(feature = "native-mlx", target_os = "macos", target_arch = "aarch64")))]
fn native_smoke(_path: &Path, _prompt: &str) -> Result<(), String> {
    Err("native-smoke requires an Apple Silicon macOS build with --features native-mlx".into())
}

fn serve(port: u16) -> Result<(), String> {
    server::serve(port).map_err(|error| format!("could not serve on 127.0.0.1:{port}: {error}"))
}

fn main() -> ExitCode {
    run().unwrap_or_else(|error| {
        eprintln!("omlx-r: {error}");
        ExitCode::FAILURE
    })
}
