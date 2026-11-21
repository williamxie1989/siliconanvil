# SPDX-License-Identifier: Apache-2.0
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import package_native


class MetalWheelTests(unittest.TestCase):
    def test_rejects_an_unverified_wheel_before_writing_any_files(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            wheel = root / "candidate.whl"
            wheel.write_bytes(b"wrong wheel")
            stage = root / "stage"
            stage.mkdir()
            with self.assertRaisesRegex(ValueError, "wheel SHA-256"):
                package_native.stage_metal(wheel, stage)
            self.assertEqual(list(stage.iterdir()), [])

    def test_stages_only_the_verified_metal_library_and_its_license(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            wheel = root / "candidate.whl"
            with ZipFile(wheel, "w") as archive:
                archive.writestr("mlx/lib/mlx.metallib", b"Metal fixture")
                archive.writestr(
                    "mlx_metal-0.31.2.dist-info/licenses/LICENSE", b"license fixture"
                )
                archive.writestr("mlx/lib/unrelated.dylib", b"other file")
            stage = root / "stage"
            stage.mkdir()
            with (
                patch.object(
                    package_native,
                    "WHEEL_SHA256",
                    hashlib.sha256(wheel.read_bytes()).hexdigest(),
                ),
                patch.object(
                    package_native,
                    "METAL_SHA256",
                    hashlib.sha256(b"Metal fixture").hexdigest(),
                ),
            ):
                package_native.stage_metal(wheel, stage)
            self.assertEqual(
                sorted(
                    path.relative_to(stage).as_posix()
                    for path in stage.rglob("*")
                    if path.is_file()
                ),
                ["licenses/mlx-metal-0.31.2/LICENSE", "mlx.metallib"],
            )
            self.assertEqual((stage / "mlx.metallib").read_bytes(), b"Metal fixture")
