import os
from pathlib import Path

import pytest

from flowmate.ai.eval import run_evaluation
from flowmate.speech.temp_files import TemporaryAudioFileService
from flowmate.stabilization.audit import validate_safe_metadata


def test_offline_ai_evaluation_passes_without_network() -> None:
    assert run_evaluation() == (10, 10)


def test_audit_metadata_rejects_user_text_and_secrets() -> None:
    assert validate_safe_metadata({"count": 2, "status": "completed"}) == {
        "count": 2,
        "status": "completed",
    }
    with pytest.raises(ValueError, match="unsafe audit metadata key"):
        validate_safe_metadata({"note_text": "private corporate text"})
    with pytest.raises(ValueError, match="unsafe audit metadata value"):
        validate_safe_metadata({"status": "contains private words"})


def test_temporary_audio_cleanup_is_prefix_owner_and_age_scoped(
    tmp_path: Path,
) -> None:
    stale = tmp_path / "flowmate-stale.ogg"
    recent = tmp_path / "flowmate-recent.ogg"
    unrelated = tmp_path / "another.ogg"
    stale.write_bytes(b"audio")
    recent.write_bytes(b"audio")
    unrelated.write_bytes(b"audio")
    os.utime(stale, (1, 1))

    removed = TemporaryAudioFileService(max_age_seconds=60).cleanup_orphans(tmp_path)

    assert removed == 1
    assert not stale.exists()
    assert recent.exists()
    assert unrelated.exists()
