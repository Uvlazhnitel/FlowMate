from __future__ import annotations

import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
HELPER = ROOT_DIR / "scripts" / "backup_artifact.py"


def run_helper(
    *arguments: str, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HELPER), *arguments],
        input=input_text,
        capture_output=True,
        check=False,
        text=True,
    )


def create_manifest(tmp_path: Path) -> tuple[Path, Path]:
    artifact = tmp_path / "backup.dump"
    manifest = tmp_path / "backup.dump.json"
    artifact.write_bytes(b"safe backup bytes")
    result = run_helper(
        "create-dump-manifest",
        "--artifact",
        str(artifact),
        "--output",
        str(manifest),
        "--postgres-version",
        "16.4",
        "--pg-dump-version",
        "pg_dump (PostgreSQL) 16.4",
        "--revision",
        "0024_remove_meeting_mode",
    )
    assert result.returncode == 0, result.stderr
    return artifact, manifest


def test_dump_manifest_v2_round_trip(tmp_path: Path) -> None:
    artifact, manifest = create_manifest(tmp_path)

    result = run_helper(
        "validate-dump-manifest",
        "--artifact",
        str(artifact),
        "--manifest",
        str(manifest),
        "--print-revisions",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0024_remove_meeting_mode"
    payload = json.loads(manifest.read_text())
    assert payload["manifest_version"] == 2
    assert payload["artifact_name"] == artifact.name
    assert payload["size_bytes"] == artifact.stat().st_size
    assert payload["created_at"].endswith("Z")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.pop("manifest_version"), "missing required fields"),
        (
            lambda value: value.update(manifest_version=1),
            "unsupported manifest_version",
        ),
        (lambda value: value.pop("sha256"), "missing required fields"),
        (
            lambda value: value.update(artifact_name="other.dump"),
            "artifact_name mismatch",
        ),
        (lambda value: value.update(size_bytes=999), "artifact size mismatch"),
        (lambda value: value.update(sha256="0" * 64), "SHA-256 mismatch"),
        (lambda value: value.update(unexpected=True), "unknown fields"),
        (lambda value: value.update(alembic_revisions=[]), "non-empty array"),
    ],
)
def test_dump_manifest_rejects_invalid_data(
    tmp_path: Path, mutation: object, message: str
) -> None:
    artifact, manifest = create_manifest(tmp_path)
    payload = json.loads(manifest.read_text())
    assert callable(mutation)
    mutation(payload)
    manifest.write_text(json.dumps(payload))

    result = run_helper(
        "validate-dump-manifest",
        "--artifact",
        str(artifact),
        "--manifest",
        str(manifest),
    )

    assert result.returncode == 1
    assert message in result.stderr


def test_dump_manifest_rejects_legacy_empty_corrupt_and_symlink(tmp_path: Path) -> None:
    artifact = tmp_path / "backup.dump"
    artifact.write_bytes(b"backup")
    manifest = tmp_path / "backup.dump.json"

    for content in ("", "not-json", '{"sha256":"legacy"}'):
        manifest.write_text(content)
        result = run_helper(
            "validate-dump-manifest",
            "--artifact",
            str(artifact),
            "--manifest",
            str(manifest),
        )
        assert result.returncode == 1

    manifest.unlink()
    real_manifest = tmp_path / "real.json"
    real_manifest.write_text("{}")
    manifest.symlink_to(real_manifest)
    result = run_helper(
        "validate-dump-manifest",
        "--artifact",
        str(artifact),
        "--manifest",
        str(manifest),
    )
    assert result.returncode == 1
    assert "symlink" in result.stderr


def test_offsite_manifest_validates_ciphertext(tmp_path: Path) -> None:
    ciphertext = tmp_path / "backup.dump.tar.age"
    manifest = tmp_path / "backup.dump.tar.age.json"
    ciphertext.write_bytes(b"ciphertext")
    create = run_helper(
        "create-offsite-manifest",
        "--artifact",
        str(ciphertext),
        "--output",
        str(manifest),
    )
    assert create.returncode == 0, create.stderr

    valid = run_helper(
        "validate-offsite-manifest",
        "--artifact",
        str(ciphertext),
        "--manifest",
        str(manifest),
    )
    assert valid.returncode == 0, valid.stderr
    ciphertext.write_bytes(b"tampered")
    invalid = run_helper(
        "validate-offsite-manifest",
        "--artifact",
        str(ciphertext),
        "--manifest",
        str(manifest),
    )
    assert invalid.returncode == 1


def test_bundle_round_trip_and_rejects_extra_member(tmp_path: Path) -> None:
    artifact, manifest = create_manifest(tmp_path)
    bundle = tmp_path / "bundle.tar"
    packed = run_helper(
        "pack-bundle",
        "--artifact",
        str(artifact),
        "--manifest",
        str(manifest),
        "--output",
        str(bundle),
    )
    assert packed.returncode == 0, packed.stderr
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    extracted = run_helper(
        "extract-bundle",
        "--bundle",
        str(bundle),
        "--output-dir",
        str(output_dir),
    )
    assert extracted.returncode == 0, extracted.stderr
    assert (output_dir / artifact.name).read_bytes() == artifact.read_bytes()

    bad_bundle = tmp_path / "bad.tar"
    extra = tmp_path / "extra"
    extra.write_text("unexpected")
    with tarfile.open(bad_bundle, "w") as archive:
        archive.add(artifact, arcname=artifact.name)
        archive.add(manifest, arcname=manifest.name)
        archive.add(extra, arcname=extra.name)
    bad_output = tmp_path / "bad-output"
    bad_output.mkdir()
    rejected = run_helper(
        "extract-bundle",
        "--bundle",
        str(bad_bundle),
        "--output-dir",
        str(bad_output),
    )
    assert rejected.returncode == 1
    assert "exactly two" in rejected.stderr


def test_parse_alembic_revisions_sorts_heads() -> None:
    result = run_helper(
        "parse-alembic-revisions",
        input_text="INFO ignored\nrevision_b (head)\nrevision_a (head)\n",
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == ["revision_a", "revision_b"]
