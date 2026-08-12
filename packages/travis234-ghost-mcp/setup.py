from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from setuptools import Distribution, setup
from setuptools.command.bdist_wheel import bdist_wheel
from setuptools.command.build_py import build_py

PACKAGE_ROOT = Path(__file__).resolve().parent
VENDOR_ROOT = PACKAGE_ROOT / "vendor" / "ghost-os"
SOURCE_DATE_EPOCH = "1774305702"
os.environ.setdefault("SOURCE_DATE_EPOCH", SOURCE_DATE_EPOCH)


def require_build_host() -> None:
    import platform
    import sys

    version_text = platform.mac_ver()[0]
    try:
        version = tuple(int(part) for part in version_text.split(".")[:2])
    except ValueError:
        version = (0,)
    if sys.platform != "darwin" or platform.machine() != "arm64" or version < (14, 0):
        raise RuntimeError(
            "travis234-ghost-mcp can only be built on macOS 14 or newer on Apple Silicon"
        )


def build_environment() -> dict[str, str]:
    retained = {
        name: os.environ[name]
        for name in (
            "DEVELOPER_DIR",
            "HOME",
            "LANG",
            "LC_ALL",
            "PATH",
            "SDKROOT",
            "TMPDIR",
        )
        if name in os.environ
    }
    retained["MACOSX_DEPLOYMENT_TARGET"] = "14.0"
    retained["SOURCE_DATE_EPOCH"] = SOURCE_DATE_EPOCH
    return retained


class BuildPy(build_py):
    def run(self) -> None:
        super().run()
        require_build_host()
        subprocess.run(
            [
                "swift",
                "build",
                "-c",
                "release",
                "--disable-automatic-resolution",
                "--package-path",
                str(VENDOR_ROOT),
            ],
            check=True,
            env=build_environment(),
        )
        source = VENDOR_ROOT / ".build" / "release" / "ghost"
        if not source.is_file():
            raise RuntimeError("Swift build did not produce the Ghost executable")
        destination = Path(self.build_lib) / "travis234_ghost_mcp" / "bin" / "ghost"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        destination.chmod(0o755)
        subprocess.run(
            ["/usr/bin/codesign", "--force", "--sign", "-", str(destination)],
            check=True,
            env=build_environment(),
        )


class BdistWheel(bdist_wheel):
    def finalize_options(self) -> None:
        super().finalize_options()
        self.root_is_pure = False

    def get_tag(self) -> tuple[str, str, str]:
        return ("py3", "none", "macosx_14_0_arm64")


class BinaryDistribution(Distribution):
    def has_ext_modules(self) -> bool:
        return True


setup(
    cmdclass={"build_py": BuildPy, "bdist_wheel": BdistWheel},
    distclass=BinaryDistribution,
)
