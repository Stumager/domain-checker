"""Build the packaged executable."""

from pathlib import Path
import os
import shutil
import sys

import PyInstaller.__main__


BASE_DIR = Path(__file__).resolve().parent
DIST_DIR = BASE_DIR / "dist"
BUILD_DIR = BASE_DIR / "build"
SPEC_PATH = BASE_DIR / "DNS_Checker.spec"
EXE_PATH = DIST_DIR / "DNS_Checker.exe"


def remove_tree(path):
    """Delete an old build folder with a useful error if it is locked."""
    if not path.exists():
        return

    try:
        shutil.rmtree(path)
    except PermissionError as exc:
        raise RuntimeError(
            f"Could not remove '{path}'. Close any running DNS_Checker.exe and try again."
        ) from exc


def build_exe():
    """Build the Windows executable from the checked-in PyInstaller spec."""
    if not SPEC_PATH.exists():
        raise FileNotFoundError(f"Missing build spec: {SPEC_PATH}")

    os.chdir(BASE_DIR)
    remove_tree(DIST_DIR)
    remove_tree(BUILD_DIR)

    PyInstaller.__main__.run([
        str(SPEC_PATH),
        "--noconfirm",
        "--clean",
    ])

    if not EXE_PATH.exists():
        raise RuntimeError(f"Build finished without creating {EXE_PATH}")

    print(f"\nBuild complete! Executable is in {EXE_PATH}")


if __name__ == "__main__":
    try:
        build_exe()
    except Exception as exc:
        print(f"\nBuild failed: {exc}", file=sys.stderr)
        sys.exit(1)
