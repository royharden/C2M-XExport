"""Atomic publication regression tests."""

from __future__ import annotations

import pytest

from xexport.publish import atomic_write_text


def test_failed_replace_preserves_previous_export(tmp_path, monkeypatch):
    """what_bug_this_catches: a failed refresh must not truncate its receipt."""
    target = tmp_path / "receipt.md"
    target.write_text("previous complete export", encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("simulated publication failure")

    monkeypatch.setattr("xexport.publish.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated publication failure"):
        atomic_write_text(target, "incomplete replacement")

    assert target.read_text(encoding="utf-8") == "previous complete export"
    assert not list(tmp_path.glob("*.tmp"))
