#!/usr/bin/env python3
"""Create and validate a local environment file without exposing secrets."""

from __future__ import annotations

import argparse
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import NoReturn

SECURE_MODE = 0o600


class EnvironmentFileError(ValueError):
    """Raised when an environment file is missing or unsafe."""


def _fail(message: str) -> NoReturn:
    raise EnvironmentFileError(message)


def _validate_regular_file(path: Path, *, label: str) -> os.stat_result:
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        _fail(f"{label} does not exist: {path}")
    if stat.S_ISLNK(file_stat.st_mode):
        _fail(f"{label} must not be a symlink: {path}")
    if not stat.S_ISREG(file_stat.st_mode):
        _fail(f"{label} must be a regular file: {path}")
    return file_stat


def check_environment_file(path: Path, *, allow_missing: bool) -> None:
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return
        _fail(f"environment file does not exist: {path}")

    if stat.S_ISLNK(file_stat.st_mode):
        _fail(
            f"environment file must not be a symlink: {path}. "
            "Remove it and rerun make setup"
        )
    if not stat.S_ISREG(file_stat.st_mode):
        _fail(
            f"environment file must be a regular file: {path}. "
            "Remove it and rerun make setup"
        )
    if file_stat.st_uid != os.getuid():
        _fail(
            f"environment file must be owned by the current user: {path}. "
            "Correct its ownership, then run chmod 600 .env"
        )
    actual_mode = stat.S_IMODE(file_stat.st_mode)
    if actual_mode != SECURE_MODE:
        _fail(
            f"environment file mode must be 0600, got {actual_mode:04o}: {path}. "
            "Run chmod 600 .env"
        )


def ensure_environment_file(template: Path, target: Path) -> None:
    if target.exists() or target.is_symlink():
        check_environment_file(target, allow_missing=False)
        return

    _validate_regular_file(template, label="environment template")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=target.parent
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, SECURE_MODE)
        with template.open("rb") as source, os.fdopen(descriptor, "wb") as destination:
            descriptor = -1
            while chunk := source.read(1024 * 1024):
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        try:
            os.link(temporary_path, target, follow_symlinks=False)
        except FileExistsError:
            check_environment_file(target, allow_missing=False)
            return
        check_environment_file(target, allow_missing=False)
        print(f"Created {target} with mode 0600")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    ensure = subparsers.add_parser("ensure")
    ensure.add_argument("--template", type=Path, required=True)
    ensure.add_argument("--target", type=Path, required=True)

    check = subparsers.add_parser("check")
    check.add_argument("--target", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "ensure":
            ensure_environment_file(args.template, args.target)
        else:
            check_environment_file(args.target, allow_missing=True)
    except EnvironmentFileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
