from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT_DIR / "docker-compose.test.yml"
BACKUP_SCRIPT = ROOT_DIR / "scripts" / "backup_postgres.sh"
RESTORE_SCRIPT = ROOT_DIR / "scripts" / "restore_postgres.sh"
UPLOAD_SCRIPT = ROOT_DIR / "scripts" / "upload_backup_offsite.sh"
OFFSITE_RESTORE_SCRIPT = ROOT_DIR / "scripts" / "restore_backup_offsite.sh"
HELPER = ROOT_DIR / "scripts" / "backup_artifact.py"
SOURCE_DB = "flowmate_ops_source_test"
TARGET_DB = "flowmate_ops_restore_test"
CONTROL_TELEGRAM_ID = 990000001
SOURCE_TELEGRAM_ID = 990000002

pytestmark = [pytest.mark.integration, pytest.mark.ops]

if os.getenv("RUN_BACKUP_RESTORE_TESTS") != "1":
    pytest.skip(
        "set RUN_BACKUP_RESTORE_TESTS=1 to run destructive isolated ops tests",
        allow_module_level=True,
    )


@dataclass(frozen=True)
class OpsArtifacts:
    root: Path
    backup: Path
    environment: dict[str, str]
    head: str


def run(
    *arguments: str | Path,
    env: dict[str, str] | None = None,
    check: bool = True,
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(argument) for argument in arguments],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        pytest.fail(
            f"command failed ({result.returncode}): {' '.join(map(str, arguments))}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def compose(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(
        "docker",
        "compose",
        "-f",
        COMPOSE_FILE,
        *arguments,
        check=check,
    )


def sql(database: str, statement: str, *, check: bool = True) -> str:
    result = compose(
        "exec",
        "-T",
        "postgres-test",
        "psql",
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
        "-At",
        "-U",
        "flowmate_test",
        "-d",
        database,
        "-c",
        statement,
        check=check,
    )
    return result.stdout.strip()


def database_exists(database: str) -> bool:
    return (
        sql(
            "postgres",
            f"SELECT count(*) FROM pg_database WHERE datname = '{database}';",
        )
        == "1"
    )


def drop_database(database: str) -> None:
    if database_exists(database):
        sql(
            "postgres",
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{database}' AND pid <> pg_backend_pid();",
        )
        compose(
            "exec", "-T", "postgres-test", "dropdb", "-U", "flowmate_test", database
        )


def create_database(database: str) -> None:
    drop_database(database)
    compose("exec", "-T", "postgres-test", "createdb", "-U", "flowmate_test", database)


def migrate(database: str, environment: dict[str, str]) -> None:
    migration_environment = environment | {
        "DATABASE_URL": (
            "postgresql+asyncpg://flowmate_test:flowmate_test@localhost:5433/"
            f"{database}"
        )
    }
    run("uv", "run", "alembic", "upgrade", "head", env=migration_environment)


def seed_source() -> None:
    sql(
        SOURCE_DB,
        """
        INSERT INTO users (id, telegram_user_id, display_name)
        VALUES ('10000000-0000-0000-0000-000000000001', 990000002, 'Ops source');
        INSERT INTO notes (id, user_id, content, source, workspace)
        VALUES (
          '20000000-0000-0000-0000-000000000001',
          '10000000-0000-0000-0000-000000000001',
          'backup sentinel note', 'manual', 'personal'
        );
        INSERT INTO work_items (
          id, user_id, type, title, workspace, source_note_id
        ) VALUES (
          '30000000-0000-0000-0000-000000000001',
          '10000000-0000-0000-0000-000000000001',
          'task', 'backup sentinel work item', 'personal',
          '20000000-0000-0000-0000-000000000001'
        );
        """,
    )


def reset_control_target(environment: dict[str, str]) -> None:
    create_database(TARGET_DB)
    migrate(TARGET_DB, environment)
    sql(
        TARGET_DB,
        "INSERT INTO users (id, telegram_user_id, display_name) "
        "VALUES ('40000000-0000-0000-0000-000000000001', "
        f"{CONTROL_TELEGRAM_ID}, 'must survive');",
    )


def assert_control_target_survived() -> None:
    assert (
        sql(
            TARGET_DB,
            "SELECT display_name FROM users "
            f"WHERE telegram_user_id = {CONTROL_TELEGRAM_ID};",
        )
        == "must survive"
    )


def backup_database(
    database: str, backup_dir: Path, environment: dict[str, str]
) -> Path:
    backup_environment = environment | {
        "BACKUP_DIR": str(backup_dir),
        "FLOWMATE_DB_COMPOSE_FILE": str(COMPOSE_FILE),
        "FLOWMATE_DB_SERVICE": "postgres-test",
        "FLOWMATE_BACKUP_DATABASE": database,
        "FLOWMATE_BACKUP_USER": "flowmate_test",
    }
    run(BACKUP_SCRIPT, env=backup_environment)
    backups = list(backup_dir.glob("flowmate-daily-*.dump"))
    assert len(backups) == 1
    return backups[0]


def restore_backup(
    backup: Path, environment: dict[str, str], *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    restore_environment = environment | {
        "FLOWMATE_DB_COMPOSE_FILE": str(COMPOSE_FILE),
        "FLOWMATE_DB_SERVICE": "postgres-test",
    }
    return run(RESTORE_SCRIPT, backup, TARGET_DB, env=restore_environment, check=check)


@pytest.fixture(scope="module")
def ops_artifacts(tmp_path_factory: pytest.TempPathFactory) -> Iterator[OpsArtifacts]:
    for command in ("docker", "uv", "python3"):
        if shutil.which(command) is None:
            pytest.skip(f"{command} is required")
    root = tmp_path_factory.mktemp("backup-restore-ops")
    environment = dict(os.environ)
    compose("up", "-d", "--wait", "postgres-test")
    create_database(SOURCE_DB)
    migrate(SOURCE_DB, environment)
    seed_source()
    backup = backup_database(SOURCE_DB, root / "backups", environment)
    head = run("uv", "run", "alembic", "heads", env=environment).stdout.split()[0]
    try:
        yield OpsArtifacts(root=root, backup=backup, environment=environment, head=head)
    finally:
        for database in (SOURCE_DB, TARGET_DB):
            drop_database(database)


def copy_artifact(artifacts: OpsArtifacts, name: str) -> tuple[Path, Path]:
    directory = artifacts.root / name
    directory.mkdir()
    backup = directory / f"{name}.dump"
    manifest = Path(f"{backup}.json")
    shutil.copyfile(artifacts.backup, backup)
    payload = json.loads(Path(f"{artifacts.backup}.json").read_text())
    payload["artifact_name"] = backup.name
    manifest.write_text(json.dumps(payload))
    return backup, manifest


def test_backup_restore_round_trip_and_repeat_backup(
    ops_artifacts: OpsArtifacts,
) -> None:
    restore_backup(ops_artifacts.backup, ops_artifacts.environment)

    assert sql(TARGET_DB, "SELECT version_num FROM alembic_version;") == (
        ops_artifacts.head
    )
    assert sql(TARGET_DB, "SELECT count(*) FROM users;") == "1"
    assert sql(TARGET_DB, "SELECT content FROM notes;") == "backup sentinel note"
    assert sql(TARGET_DB, "SELECT title FROM work_items;") == (
        "backup sentinel work item"
    )
    assert (
        sql(
            TARGET_DB,
            "SELECT count(*) FROM work_items w JOIN notes n ON n.id = w.source_note_id "
            "JOIN users u ON u.id = w.user_id AND u.id = n.user_id;",
        )
        == "1"
    )
    assert (
        sql(
            TARGET_DB,
            "SELECT count(*) FROM pg_constraint "
            "WHERE conrelid = 'work_items'::regclass "
            "AND contype = 'f';",
        )
        != "0"
    )

    repeated = backup_database(
        TARGET_DB, ops_artifacts.root / "repeated", ops_artifacts.environment
    )
    assert repeated.stat().st_size > 0
    assert Path(f"{repeated}.json").is_file()


@pytest.mark.parametrize(
    "case",
    [
        "missing_manifest",
        "empty_manifest",
        "corrupt_manifest",
        "missing_field",
        "unknown_version",
        "wrong_name",
        "wrong_size",
        "wrong_sha",
        "modified_dump",
        "truncated_dump",
        "unreadable_archive",
        "revision_mismatch",
    ],
)
def test_invalid_artifacts_preserve_target(
    ops_artifacts: OpsArtifacts, case: str
) -> None:
    reset_control_target(ops_artifacts.environment)
    backup, manifest = copy_artifact(ops_artifacts, case)
    payload = json.loads(manifest.read_text())

    if case == "missing_manifest":
        manifest.unlink()
    elif case == "empty_manifest":
        manifest.write_text("")
    elif case == "corrupt_manifest":
        manifest.write_text("not-json")
    elif case == "missing_field":
        payload.pop("postgres_version")
        manifest.write_text(json.dumps(payload))
    elif case == "unknown_version":
        payload["manifest_version"] = 99
        manifest.write_text(json.dumps(payload))
    elif case == "wrong_name":
        payload["artifact_name"] = "different.dump"
        manifest.write_text(json.dumps(payload))
    elif case == "wrong_size":
        payload["size_bytes"] += 1
        manifest.write_text(json.dumps(payload))
    elif case == "wrong_sha":
        payload["sha256"] = "0" * 64
        manifest.write_text(json.dumps(payload))
    elif case == "modified_dump":
        with backup.open("ab") as file_handle:
            file_handle.write(b"tamper")
    elif case == "truncated_dump":
        backup.write_bytes(backup.read_bytes()[:32])
    elif case == "unreadable_archive":
        backup.write_bytes(b"not a pg_dump archive")
        create_valid_manifest(backup, manifest, ops_artifacts.head)
    elif case == "revision_mismatch":
        payload["alembic_revisions"] = ["9999_not_project_head"]
        manifest.write_text(json.dumps(payload))

    result = restore_backup(backup, ops_artifacts.environment, check=False)
    assert result.returncode != 0
    assert_control_target_survived()


def create_valid_manifest(backup: Path, manifest: Path, revision: str) -> None:
    manifest.unlink(missing_ok=True)
    run(
        "python3",
        HELPER,
        "create-dump-manifest",
        "--artifact",
        backup,
        "--output",
        manifest,
        "--postgres-version",
        "16",
        "--pg-dump-version",
        "pg_dump (PostgreSQL) 16",
        "--revision",
        revision,
    )


def test_staging_revision_mismatch_preserves_target(
    ops_artifacts: OpsArtifacts,
) -> None:
    reset_control_target(ops_artifacts.environment)
    wrong_database = "flowmate_ops_wrong_revision_test"
    create_database(wrong_database)
    migrate(wrong_database, ops_artifacts.environment)
    sql(
        wrong_database,
        "UPDATE alembic_version SET version_num = '9999_missing_revision';",
    )
    directory = ops_artifacts.root / "staging-revision-mismatch"
    directory.mkdir()
    backup = directory / "staging-revision-mismatch.dump"
    with backup.open("wb") as file_handle:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(COMPOSE_FILE),
                "exec",
                "-T",
                "postgres-test",
                "pg_dump",
                "-U",
                "flowmate_test",
                "-d",
                wrong_database,
                "--format=custom",
                "--no-owner",
                "--no-acl",
            ],
            cwd=ROOT_DIR,
            stdout=file_handle,
            stderr=subprocess.PIPE,
            check=False,
            timeout=180,
        )
    assert result.returncode == 0, result.stderr.decode()
    manifest = Path(f"{backup}.json")
    create_valid_manifest(backup, manifest, ops_artifacts.head)
    try:
        restored = restore_backup(backup, ops_artifacts.environment, check=False)
        assert restored.returncode != 0
        assert "Expected Alembic revisions:" in restored.stderr
        assert "Actual Alembic revisions:" in restored.stderr
        assert_control_target_survived()
    finally:
        drop_database(wrong_database)


def test_synthetic_multiple_project_heads_preserve_target(
    ops_artifacts: OpsArtifacts,
) -> None:
    reset_control_target(ops_artifacts.environment)
    alembic_root = ops_artifacts.root / "multihead"
    versions = alembic_root / "versions"
    versions.mkdir(parents=True)
    (alembic_root / "env.py").write_text("")
    for revision in ("fake_head_a", "fake_head_b"):
        (versions / f"{revision}.py").write_text(
            f"revision = {revision!r}\ndown_revision = None\n"
            "branch_labels = None\ndepends_on = None\n"
        )
    config = ops_artifacts.root / "multihead.ini"
    config.write_text(f"[alembic]\nscript_location = {alembic_root}\n")
    environment = ops_artifacts.environment | {"ALEMBIC_CONFIG": str(config)}

    result = restore_backup(ops_artifacts.backup, environment, check=False)

    assert result.returncode != 0
    assert "exactly one project Alembic head" in result.stderr
    assert_control_target_survived()


def test_offsite_encrypt_verify_restore_and_fail_closed(
    ops_artifacts: OpsArtifacts,
) -> None:
    for command in ("age", "age-keygen", "rclone"):
        if shutil.which(command) is None:
            pytest.skip(f"{command} is required for off-site test")
    identity = ops_artifacts.root / "age-identity.txt"
    recipients = ops_artifacts.root / "age-recipients.txt"
    run("age-keygen", "-o", identity)
    public_key = run("age-keygen", "-y", identity).stdout.strip()
    recipients.write_text(f"{public_key}\n")
    remote = ops_artifacts.root / "remote"
    remote.mkdir()
    rclone_config = ops_artifacts.root / "rclone.conf"
    rclone_config.write_text("")
    temp_root = ops_artifacts.root / "offsite-temp"
    temp_root.mkdir()
    environment = ops_artifacts.environment | {
        "FLOWMATE_OFFSITE_REMOTE": str(remote),
        "FLOWMATE_AGE_RECIPIENTS_FILE": str(recipients),
        "FLOWMATE_AGE_IDENTITY_FILE": str(identity),
        "RCLONE_CONFIG": str(rclone_config),
        "TMPDIR": str(temp_root),
        "FLOWMATE_DB_COMPOSE_FILE": str(COMPOSE_FILE),
        "FLOWMATE_DB_SERVICE": "postgres-test",
    }

    run(UPLOAD_SCRIPT, ops_artifacts.backup, env=environment)
    remote_files = sorted(path.name for path in remote.iterdir())
    assert len(remote_files) == 2
    assert remote_files[0].endswith(".tar.age")
    assert remote_files[1].endswith(".tar.age.json")
    assert not any(path.name.endswith(".dump") for path in remote.iterdir())
    ciphertext = next(
        path for path in remote.iterdir() if path.name.endswith(".tar.age")
    )

    reset_control_target(ops_artifacts.environment)
    run(OFFSITE_RESTORE_SCRIPT, ciphertext, TARGET_DB, env=environment)
    assert sql(TARGET_DB, "SELECT telegram_user_id FROM users;") == str(
        SOURCE_TELEGRAM_ID
    )
    assert not list(temp_root.glob("flowmate-offsite-*"))

    wrong_identity = ops_artifacts.root / "wrong-identity.txt"
    run("age-keygen", "-o", wrong_identity)
    reset_control_target(ops_artifacts.environment)
    wrong_environment = environment | {
        "FLOWMATE_AGE_IDENTITY_FILE": str(wrong_identity)
    }
    wrong_key = run(
        OFFSITE_RESTORE_SCRIPT,
        ciphertext,
        TARGET_DB,
        env=wrong_environment,
        check=False,
    )
    assert wrong_key.returncode != 0
    assert_control_target_survived()
    assert not list(temp_root.glob("flowmate-offsite-*"))

    with ciphertext.open("ab") as file_handle:
        file_handle.write(b"tampered")
    tampered = run(
        OFFSITE_RESTORE_SCRIPT,
        ciphertext,
        TARGET_DB,
        env=environment,
        check=False,
    )
    assert tampered.returncode != 0
    assert_control_target_survived()
    assert not list(temp_root.glob("flowmate-offsite-*"))
