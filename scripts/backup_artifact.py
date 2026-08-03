#!/usr/bin/env python3
"""Create and validate FlowMate backup manifests and encrypted bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

DUMP_FORMAT = "pg_dump-custom"
OFFSITE_FORMAT = "age-encrypted-tar"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REVISION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_]*$")


class ArtifactError(ValueError):
    """Raised when an artifact or manifest is unsafe or invalid."""


def _fail(message: str) -> NoReturn:
    raise ArtifactError(message)


def _regular_readable_file(path: Path, label: str) -> os.stat_result:
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        _fail(f"{label} does not exist: {path}")
    if stat.S_ISLNK(file_stat.st_mode):
        _fail(f"{label} must not be a symlink: {path}")
    if not stat.S_ISREG(file_stat.st_mode):
        _fail(f"{label} must be a regular file: {path}")
    if not os.access(path, os.R_OK):
        _fail(f"{label} is not readable: {path}")
    return file_stat


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _created_at() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    _regular_readable_file(path, "manifest")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"manifest is not valid UTF-8 JSON: {exc}")
    if type(value) is not dict:
        _fail("manifest root must be a JSON object")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        _fail(f"refusing to overwrite manifest: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file_handle:
        json.dump(value, file_handle, indent=2, sort_keys=True)
        file_handle.write("\n")


def _require_exact_fields(
    value: dict[str, Any], expected: set[str], label: str
) -> None:
    missing = sorted(expected - value.keys())
    extra = sorted(value.keys() - expected)
    if missing:
        _fail(f"{label} is missing required fields: {', '.join(missing)}")
    if extra:
        _fail(f"{label} contains unknown fields: {', '.join(extra)}")


def _validate_common(
    value: dict[str, Any], artifact: Path, *, version: int, format_name: str
) -> None:
    if type(value["manifest_version"]) is not int:
        _fail("manifest_version must be an integer")
    if value["manifest_version"] != version:
        _fail(
            f"unsupported manifest_version {value['manifest_version']!r}; "
            f"expected {version}"
        )
    if type(value["artifact_name"]) is not str:
        _fail("artifact_name must be a string")
    if value["artifact_name"] != artifact.name:
        _fail(
            f"artifact_name mismatch: expected {artifact.name!r}, "
            f"got {value['artifact_name']!r}"
        )
    if type(value["created_at"]) is not str:
        _fail("created_at must be a string")
    try:
        parsed = datetime.fromisoformat(value["created_at"].replace("Z", "+00:00"))
    except ValueError:
        _fail("created_at must be an RFC3339 timestamp")
    if parsed.tzinfo is None:
        _fail("created_at must include a timezone")
    if type(value["format"]) is not str or value["format"] != format_name:
        _fail(f"format must be {format_name!r}")
    if type(value["size_bytes"]) is not int or value["size_bytes"] < 1:
        _fail("size_bytes must be a positive integer")
    if type(value["sha256"]) is not str or not SHA256_RE.fullmatch(value["sha256"]):
        _fail("sha256 must be a lowercase 64-character hexadecimal string")

    artifact_stat = _regular_readable_file(artifact, "artifact")
    if artifact_stat.st_size != value["size_bytes"]:
        _fail(
            f"artifact size mismatch: expected {value['size_bytes']}, "
            f"got {artifact_stat.st_size}"
        )
    actual_sha256 = _sha256(artifact)
    if actual_sha256 != value["sha256"]:
        _fail(
            f"artifact SHA-256 mismatch: expected {value['sha256']}, "
            f"got {actual_sha256}"
        )


def create_dump_manifest(args: argparse.Namespace) -> None:
    artifact = Path(args.artifact)
    artifact_stat = _regular_readable_file(artifact, "artifact")
    revisions = sorted(set(args.revision))
    if not revisions or any(not REVISION_RE.fullmatch(item) for item in revisions):
        _fail("alembic_revisions must contain valid revision IDs")
    for name, value in (
        ("postgres_version", args.postgres_version),
        ("pg_dump_version", args.pg_dump_version),
    ):
        if not value or "\n" in value or "\r" in value:
            _fail(f"{name} must be a non-empty single-line string")

    manifest = {
        "manifest_version": 2,
        "artifact_name": artifact.name,
        "created_at": _created_at(),
        "format": DUMP_FORMAT,
        "size_bytes": artifact_stat.st_size,
        "sha256": _sha256(artifact),
        "postgres_version": args.postgres_version,
        "pg_dump_version": args.pg_dump_version,
        "alembic_revisions": revisions,
    }
    _write_json(Path(args.output), manifest)


def validate_dump_manifest(args: argparse.Namespace) -> None:
    artifact = Path(args.artifact)
    value = _read_json(Path(args.manifest))
    expected = {
        "manifest_version",
        "artifact_name",
        "created_at",
        "format",
        "size_bytes",
        "sha256",
        "postgres_version",
        "pg_dump_version",
        "alembic_revisions",
    }
    _require_exact_fields(value, expected, "dump manifest")
    _validate_common(value, artifact, version=2, format_name=DUMP_FORMAT)
    for field in ("postgres_version", "pg_dump_version"):
        if type(value[field]) is not str or not value[field]:
            _fail(f"{field} must be a non-empty string")
    revisions = value["alembic_revisions"]
    if type(revisions) is not list or not revisions:
        _fail("alembic_revisions must be a non-empty array")
    if any(
        type(item) is not str or not REVISION_RE.fullmatch(item) for item in revisions
    ):
        _fail("alembic_revisions contains an invalid revision ID")
    if revisions != sorted(set(revisions)):
        _fail("alembic_revisions must be sorted and contain no duplicates")
    if args.print_revisions:
        print("\n".join(revisions))


def create_offsite_manifest(args: argparse.Namespace) -> None:
    artifact = Path(args.artifact)
    artifact_stat = _regular_readable_file(artifact, "artifact")
    manifest = {
        "manifest_version": 1,
        "artifact_name": artifact.name,
        "created_at": _created_at(),
        "format": OFFSITE_FORMAT,
        "size_bytes": artifact_stat.st_size,
        "sha256": _sha256(artifact),
    }
    _write_json(Path(args.output), manifest)


def validate_offsite_manifest(args: argparse.Namespace) -> None:
    artifact = Path(args.artifact)
    value = _read_json(Path(args.manifest))
    expected = {
        "manifest_version",
        "artifact_name",
        "created_at",
        "format",
        "size_bytes",
        "sha256",
    }
    _require_exact_fields(value, expected, "off-site manifest")
    _validate_common(value, artifact, version=1, format_name=OFFSITE_FORMAT)


def pack_bundle(args: argparse.Namespace) -> None:
    dump = Path(args.artifact)
    manifest = Path(args.manifest)
    validate_dump_manifest(
        argparse.Namespace(
            artifact=str(dump), manifest=str(manifest), print_revisions=False
        )
    )
    if manifest.name != f"{dump.name}.json":
        _fail("internal manifest name must be the artifact name plus .json")
    output = Path(args.output)
    if output.exists() or output.is_symlink():
        _fail(f"refusing to overwrite bundle: {output}")
    with tarfile.open(output, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in (dump, manifest):
            info = archive.gettarinfo(str(path), arcname=path.name)
            info.mode = 0o600
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            with path.open("rb") as file_handle:
                archive.addfile(info, file_handle)
    output.chmod(0o600)


def extract_bundle(args: argparse.Namespace) -> None:
    bundle = Path(args.bundle)
    _regular_readable_file(bundle, "bundle")
    output_dir = Path(args.output_dir)
    output_stat = output_dir.lstat()
    if not stat.S_ISDIR(output_stat.st_mode) or stat.S_ISLNK(output_stat.st_mode):
        _fail("output directory must be a real directory")
    output_dir.chmod(0o700)

    try:
        with tarfile.open(bundle, mode="r:") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            if len(members) != 2 or len(set(names)) != 2:
                _fail("bundle must contain exactly two unique files")
            if any(
                not member.isfile()
                or Path(member.name).name != member.name
                or member.name in {".", ".."}
                for member in members
            ):
                _fail("bundle members must be regular top-level files")
            dump_names = [name for name in names if name.endswith(".dump")]
            if len(dump_names) != 1 or f"{dump_names[0]}.json" not in names:
                _fail("bundle must contain one .dump and its .dump.json manifest")
            for member in members:
                source = archive.extractfile(member)
                if source is None:
                    _fail(f"could not read bundle member: {member.name}")
                destination = output_dir / member.name
                descriptor = os.open(
                    destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                with source, os.fdopen(descriptor, "wb") as target:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
    except (tarfile.TarError, OSError) as exc:
        _fail(f"invalid bundle: {exc}")

    dump = output_dir / dump_names[0]
    manifest = output_dir / f"{dump.name}.json"
    validate_dump_manifest(
        argparse.Namespace(
            artifact=str(dump), manifest=str(manifest), print_revisions=False
        )
    )
    print(dump)


def parse_alembic_revisions(args: argparse.Namespace) -> None:
    revisions: set[str] = set()
    for raw_line in sys.stdin:
        line = raw_line.strip()
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9_]*)(?:\s+\(head\))?", line)
        if match:
            revisions.add(match.group(1))
    if not revisions:
        _fail("no Alembic revisions found in command output")
    print("\n".join(sorted(revisions)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_dump = subparsers.add_parser("create-dump-manifest")
    create_dump.add_argument("--artifact", required=True)
    create_dump.add_argument("--output", required=True)
    create_dump.add_argument("--postgres-version", required=True)
    create_dump.add_argument("--pg-dump-version", required=True)
    create_dump.add_argument("--revision", action="append", required=True)
    create_dump.set_defaults(handler=create_dump_manifest)

    validate_dump = subparsers.add_parser("validate-dump-manifest")
    validate_dump.add_argument("--artifact", required=True)
    validate_dump.add_argument("--manifest", required=True)
    validate_dump.add_argument("--print-revisions", action="store_true")
    validate_dump.set_defaults(handler=validate_dump_manifest)

    create_offsite = subparsers.add_parser("create-offsite-manifest")
    create_offsite.add_argument("--artifact", required=True)
    create_offsite.add_argument("--output", required=True)
    create_offsite.set_defaults(handler=create_offsite_manifest)

    validate_offsite = subparsers.add_parser("validate-offsite-manifest")
    validate_offsite.add_argument("--artifact", required=True)
    validate_offsite.add_argument("--manifest", required=True)
    validate_offsite.set_defaults(handler=validate_offsite_manifest)

    pack = subparsers.add_parser("pack-bundle")
    pack.add_argument("--artifact", required=True)
    pack.add_argument("--manifest", required=True)
    pack.add_argument("--output", required=True)
    pack.set_defaults(handler=pack_bundle)

    extract = subparsers.add_parser("extract-bundle")
    extract.add_argument("--bundle", required=True)
    extract.add_argument("--output-dir", required=True)
    extract.set_defaults(handler=extract_bundle)

    revisions = subparsers.add_parser("parse-alembic-revisions")
    revisions.set_defaults(handler=parse_alembic_revisions)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.handler(args)
    except ArtifactError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
