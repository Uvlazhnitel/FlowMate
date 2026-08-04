from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
HELPER = ROOT_DIR / "scripts" / "env_file.py"


def run_helper(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HELPER), *arguments],
        capture_output=True,
        check=False,
        text=True,
    )


def test_ensure_creates_environment_file_with_mode_0600(tmp_path: Path) -> None:
    template = tmp_path / ".env.example"
    target = tmp_path / ".env"
    template.write_text("SECRET=private-value\n")

    previous_umask = os.umask(0)
    try:
        result = run_helper(
            "ensure", "--template", str(template), "--target", str(target)
        )
    finally:
        os.umask(previous_umask)

    assert result.returncode == 0, result.stderr
    assert target.read_text() == "SECRET=private-value\n"
    assert target.stat().st_mode & 0o777 == 0o600


def test_ensure_accepts_existing_secure_file_without_replacing_it(
    tmp_path: Path,
) -> None:
    template = tmp_path / ".env.example"
    target = tmp_path / ".env"
    template.write_text("SECRET=template\n")
    target.write_text("SECRET=existing\n")
    target.chmod(0o600)
    inode = target.stat().st_ino

    result = run_helper("ensure", "--template", str(template), "--target", str(target))

    assert result.returncode == 0, result.stderr
    assert target.read_text() == "SECRET=existing\n"
    assert target.stat().st_ino == inode


def test_check_allows_missing_environment_file(tmp_path: Path) -> None:
    result = run_helper("check", "--target", str(tmp_path / ".env"))

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o666, 0o400])
def test_check_rejects_non_0600_mode_without_leaking_content(
    tmp_path: Path, mode: int
) -> None:
    secret = "private-token-that-must-not-appear"
    target = tmp_path / ".env"
    target.write_text(f"TOKEN={secret}\n")
    target.chmod(mode)

    result = run_helper("check", "--target", str(target))

    assert result.returncode == 1
    assert "mode must be 0600" in result.stderr
    assert "chmod 600 .env" in result.stderr
    assert secret not in result.stderr
    assert target.read_text() == f"TOKEN={secret}\n"


def test_ensure_does_not_fix_existing_insecure_file(tmp_path: Path) -> None:
    template = tmp_path / ".env.example"
    target = tmp_path / ".env"
    template.write_text("TOKEN=template\n")
    target.write_text("TOKEN=existing\n")
    target.chmod(0o644)

    result = run_helper("ensure", "--template", str(template), "--target", str(target))

    assert result.returncode == 1
    assert target.read_text() == "TOKEN=existing\n"
    assert target.stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize("kind", ["directory", "symlink"])
def test_check_rejects_non_regular_environment_file(tmp_path: Path, kind: str) -> None:
    target = tmp_path / ".env"
    if kind == "directory":
        target.mkdir()
    else:
        real_file = tmp_path / "real-env"
        real_file.write_text("TOKEN=private\n")
        real_file.chmod(0o600)
        target.symlink_to(real_file)

    result = run_helper("check", "--target", str(target))

    assert result.returncode == 1
    assert kind in result.stderr or "regular file" in result.stderr
