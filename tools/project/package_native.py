# SPDX-License-Identifier: Apache-2.0
"""Stage an arm64 native release candidate from a locked macOS build."""

import argparse
import gzip
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
TARGET = "aarch64-apple-darwin"
DEPLOYMENT_TARGET = "26.0"
WHEEL = "mlx_metal-0.31.2-py3-none-macosx_26_0_arm64.whl"
WHEEL_SHA256 = "84ffb60ee503f03eb684f5fb168d5cff31e2a16b7f27c1731eaf7662bd6e9b46"
METAL_SHA256 = "8c8bfcece8c0610745b68879771e5aa1b92b29fa5e17172e5508e4f5153d8d15"
WHEEL_SOURCE = "https://pypi.org/project/mlx-metal/0.31.2/"
LICENSE_PREFIXES = (
    "LICENSE",
    "LICENCE",
    "COPYING",
    "NOTICE",
    "COPYRIGHT",
    "UNLICENSE",
    "AUTHORS",
    "THIRD-PARTY",
    "THIRD_PARTY",
)


def command(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def stage_metal(wheel, stage):
    if sha256(wheel) != WHEEL_SHA256:
        raise ValueError("MLX wheel SHA-256 differs from the pinned wheel")
    with ZipFile(wheel) as archive:
        metal = stage / "mlx.metallib"
        with archive.open("mlx/lib/mlx.metallib") as source, metal.open("wb") as dest:
            shutil.copyfileobj(source, dest)
        if sha256(metal) != METAL_SHA256:
            raise ValueError("mlx.metallib SHA-256 differs from the verified library")
        license_path = stage / "licenses/mlx-metal-0.31.2/LICENSE"
        license_path.parent.mkdir(parents=True)
        license_path.write_bytes(
            archive.read("mlx_metal-0.31.2.dist-info/licenses/LICENSE")
        )


def inspect_binary(binary):
    if command("lipo", "-archs", binary) != "arm64":
        raise ValueError("expected a single arm64 Mach-O executable")
    build_info = command("xcrun", "vtool", "-show-build", binary)
    if not (
        re.search(r"^\s*platform MACOS\s*$", build_info, re.M)
        and re.findall(r"^\s*minos\s+(\S+)", build_info, re.M) == [DEPLOYMENT_TARGET]
    ):
        raise ValueError(
            f"expected a macOS {DEPLOYMENT_TARGET} Mach-O deployment target"
        )
    libraries = [
        line.strip().split(" (", 1)[0]
        for line in command("otool", "-L", binary).splitlines()[1:]
    ]
    if not libraries or any(
        not item.startswith(("/usr/lib/", "/System/Library/")) for item in libraries
    ):
        raise ValueError("the native binary must link only macOS system libraries")
    subprocess.run(["codesign", "--verify", "--strict", binary], check=True)
    signature = subprocess.run(
        ["codesign", "-dv", binary], capture_output=True, text=True, check=True
    ).stderr.splitlines()
    return {
        "target": TARGET,
        "deployment_target": DEPLOYMENT_TARGET,
        "system_libraries": libraries,
        "signature": [
            line
            for line in signature
            if line.startswith(("Signature=", "Authority=", "TeamIdentifier="))
        ],
    }


def crate_notices(stage):
    # Cargo tree filters both target dependencies and the features of this build.
    selection = command(
        "cargo",
        "tree",
        "--locked",
        "--target",
        TARGET,
        "--features",
        "native-mlx",
        "--edges",
        "normal",
        "--prefix",
        "none",
        "--format",
        "{p}",
    )
    packages = set()
    for line in selection.splitlines():
        match = re.fullmatch(r"(\S+) v(\S+)(?: .*)?", line)
        if not match:
            raise ValueError(f"unexpected cargo tree output: {line}")
        packages.add(match.groups())
    metadata = json.loads(
        command(
            "cargo",
            "metadata",
            "--locked",
            "--filter-platform",
            TARGET,
            "--features",
            "native-mlx",
            "--format-version",
            "1",
        )
    )
    by_key = {
        (entry["name"], entry["version"]): entry for entry in metadata["packages"]
    }
    lines = ["Crate\tVersion\tDeclared license\tSource\tBundled notices"]
    crate_roots = {}
    for name, version in sorted(packages):
        entry = by_key[name, version]
        if entry["source"] is None:
            if Path(entry["manifest_path"]).resolve() != ROOT / "Cargo.toml":
                raise ValueError(f"unreviewed local dependency: {name} {version}")
            continue
        source = Path(entry["manifest_path"]).parent
        crate_roots[name, version] = source
        dest = stage / "licenses" / f"{name}-{version}"
        files = sorted(
            path
            for path in source.iterdir()
            if path.is_file() and path.name.upper().startswith(LICENSE_PREFIXES)
        )
        if entry["license_file"]:
            files.append(source / entry["license_file"])
        if not files:
            raise ValueError(f"no license file found for {name} {version}")
        dest.mkdir(parents=True)
        for path in files:
            shutil.copyfile(path, dest / path.name)
        lines.append(
            f"{name}\t{version}\t{entry['license']}\t{entry['source']}\t{dest.relative_to(stage)}"
        )
    (stage / "CARGO-LICENSES.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return crate_roots


def native_notices(stage, build, crates, rust_copyright, compiler_rt_license):
    cache = {}
    for line in (build / "CMakeCache.txt").read_text().splitlines():
        if line and not line.startswith(("#", "//")) and "=" in line:
            key, value = line.split("=", 1)
            cache[key.split(":", 1)[0]] = value
    if cache.get("CMAKE_OSX_DEPLOYMENT_TARGET") != DEPLOYMENT_TARGET:
        raise ValueError("CMake cache must match the release deployment target")
    mlex = crates["mlex", "0.1.3"]
    sources = {
        "mlx-vendored/LICENSE": mlex / "vendor/mlx/LICENSE",
        "mlx-c-vendored/LICENSE": mlex / "vendor/mlx-c/LICENSE",
        "metal-cpp/LICENSE.txt": build / "_deps/metal_cpp-src/LICENSE.txt",
        "gguflib/LICENSE": build / "_deps/gguflib-src/LICENSE",
        "fmt/LICENSE": Path(cache["FMT_SOURCE_DIR"]) / "LICENSE",
        "nlohmann-json/LICENSE.MIT": Path(cache["nlohmann_json_SOURCE_DIR"])
        / "LICENSE.MIT",
        "oniguruma/COPYING": crates["onig_sys", "69.9.3"] / "oniguruma/COPYING",
        "oniguruma/AUTHORS": crates["onig_sys", "69.9.3"] / "oniguruma/AUTHORS",
        "rust-stdlib/COPYRIGHT-library.html": rust_copyright,
        "compiler-rt/LICENSE.TXT": compiler_rt_license,
    }
    for label, source in sources.items():
        dest = stage / "licenses" / label
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    esaxx = crates["esaxx-rs", "0.1.10"]
    for filename in ("sais.hxx", "esa.hxx"):
        header = (esaxx / "src" / filename).read_text()
        notice = re.search(r"/\*.*?\*/", header, re.S)
        if not notice or "Copyright" not in notice[0]:
            raise ValueError(f"missing embedded license in {filename}")
        (stage / "licenses/esaxx-rs-0.1.10" / f"{filename}-NOTICE").write_text(
            notice[0] + "\n", encoding="utf-8"
        )


def archive_metadata(info):
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mtime = 0
    info.mode = 0o755 if info.isdir() or info.name.endswith("/siliconanvil") else 0o644
    return info


def package(args):
    output = args.output.absolute()
    with tempfile.TemporaryDirectory(
        prefix=".siliconanvil-package-", dir=output.parent
    ) as tmp:
        stage = Path(tmp) / "siliconanvil-native"
        stage.mkdir()
        binary = stage / "siliconanvil"
        shutil.copy2(args.binary, binary)
        build_info = inspect_binary(binary)
        stage_metal(args.wheel, stage)
        crates = crate_notices(stage)
        native_notices(
            stage,
            args.cmake_build,
            crates,
            args.rust_copyright,
            args.compiler_rt_license,
        )
        shutil.copyfile(ROOT / "LICENSE", stage / "LICENSE")
        shutil.copyfile(ROOT / "docs/release-readiness.md", stage / "RELEASE.md")
        for name in ("native-mlx.md", "upstream-contract-v0.8.2.md"):
            shutil.copyfile(ROOT / "docs" / name, stage / name)
        manifest = {
            **build_info,
            "source_commit": command(
                "git", "rev-parse", args.source_commit + "^{commit}"
            ),
            "cargo_lock_sha256": sha256(ROOT / "Cargo.lock"),
            "binary_sha256": sha256(binary),
            "version": command(binary, "--version"),
            "mlx_wheel": {
                "file": WHEEL,
                "source": WHEEL_SOURCE,
                "sha256": WHEEL_SHA256,
            },
            "mlx_metallib_sha256": METAL_SHA256,
        }
        (stage / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        archive = Path(tmp) / "candidate.tar.gz"
        with (
            archive.open("wb") as stream,
            gzip.GzipFile(
                fileobj=stream, filename="", mode="wb", mtime=0, compresslevel=6
            ) as compressed,
            tarfile.open(fileobj=compressed, mode="w") as tar,
        ):
            tar.add(stage, arcname=stage.name, filter=archive_metadata)
        # Publish the fully written archive without overwriting an earlier candidate.
        output.hardlink_to(archive)
    return sha256(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--cmake-build", required=True, type=Path)
    parser.add_argument("--rust-copyright", required=True, type=Path)
    parser.add_argument("--compiler-rt-license", required=True, type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        digest = package(args)
    except (OSError, KeyError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"package-native: {error}\n")
    print(f"{digest}  {args.output}")


if __name__ == "__main__":
    main()
