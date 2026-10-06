"""Build the deployment zip of the database rotation Lambda function.

Run by Terragrunt (before_hook) ahead of plan, apply and destroy; the `db-rotation` module
reads the zip through `package_path`. The handler and its pinned dependencies
(src/rotation/db_rotation/requirements.txt) go at the root of the zip, so the Lambda handler
is `handler.handler`. The dependencies are pure Python wheels, installed for the Lambda
platform (Linux arm64, Python 3.14) whatever the machine that builds the package.

The output lives in `.build/` (git-ignored) and the zip is deterministic: sorted entries,
fixed timestamps and permissions, so the same inputs give the same hash.

Usage:

    python scripts/package_rotation.py
"""

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "src" / "rotation" / "db_rotation"
BUILD_DIR = REPO_ROOT / ".build" / "rotation"
PACKAGE_DIR = BUILD_DIR / "package"
ZIP_PATH = BUILD_DIR / "db-rotation.zip"

PLATFORM = "manylinux2014_aarch64"
PYTHON_VERSION = "3.14"
FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
FILE_MODE = 0o644 << 16  # regular file, rw-r--r--

SOURCE_FILES = ["handler.py"]
IGNORED_PARTS = {"__pycache__"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}


def install_dependencies() -> None:
    """Install the pinned dependencies as wheels for the Lambda platform."""
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--no-compile",
            "--disable-pip-version-check",
            "--target",
            str(PACKAGE_DIR),
            "--platform",
            PLATFORM,
            "--python-version",
            PYTHON_VERSION,
            "--implementation",
            "cp",
            "--only-binary=:all:",
            "--requirement",
            str(SOURCE_DIR / "requirements.txt"),
        ],
        check=True,
    )


def write_zip() -> None:
    """Zip the package folder in a deterministic way."""
    files = sorted(
        path
        for path in PACKAGE_DIR.rglob("*")
        if path.is_file()
        and not IGNORED_PARTS.intersection(path.parts)
        and path.suffix not in IGNORED_SUFFIXES
    )
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(PACKAGE_DIR).as_posix(), FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = FILE_MODE
            info.create_system = 3  # Unix, so the mode above is honored
            zf.writestr(info, path.read_bytes())


def package() -> Path:
    for name in SOURCE_FILES:
        if not (SOURCE_DIR / name).is_file():
            raise SystemExit(f"Missing source file: {SOURCE_DIR / name}")

    shutil.rmtree(BUILD_DIR, ignore_errors=True)
    PACKAGE_DIR.mkdir(parents=True)
    install_dependencies()
    for name in SOURCE_FILES:
        shutil.copyfile(SOURCE_DIR / name, PACKAGE_DIR / name)
    write_zip()
    return ZIP_PATH


if __name__ == "__main__":
    print(f"Rotation package ready: {package()}")
