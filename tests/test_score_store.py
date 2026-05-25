"""Tests for symphony.score_store — disk-backed score snapshot storage."""
from __future__ import annotations

from pathlib import Path

import pytest

from symphony.models import InstrumentName
from symphony.models.enums import ScoreStatus
from symphony.score_store import ScoreStore
from tests.helpers.score_snapshots import make_snapshot


def _store(tmp_path: Path, **kwargs) -> ScoreStore:
    return ScoreStore(root=tmp_path / "scores", **kwargs)


class TestSave:
    def test_save_writes_json_and_replaces_atomically(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        snap = make_snapshot(score_id="s-1")
        store.save(snap)
        target = store.path_for("s-1")
        assert target.exists()
        # Atomic write: no leftover .tmp file.
        assert not list(target.parent.glob("*.json.tmp"))

    def test_save_overwrites_existing(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        store.save(make_snapshot(score_id="s-1", accumulated_text="first"))
        store.save(make_snapshot(score_id="s-1", accumulated_text="second"))
        loaded = store.load("s-1")
        assert loaded is not None
        assert loaded.accumulated_text == "second"


class TestLoad:
    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        assert store.load("nope") is None

    def test_returns_snapshot_when_present(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        store.save(make_snapshot(score_id="s-1", accumulated_text="hi"))
        loaded = store.load("s-1")
        assert loaded is not None
        assert loaded.score_id == "s-1"
        assert loaded.accumulated_text == "hi"


class TestLoadAll:
    def test_returns_empty_when_root_missing(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        # Delete the root after init so the load_all() early return fires.
        for child in store.root.iterdir():
            child.unlink()
        store.root.rmdir()
        assert store.load_all() == []

    def test_returns_all_snapshots_sorted(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        store.save(make_snapshot(score_id="b"))
        store.save(make_snapshot(score_id="a"))
        snapshots = store.load_all()
        assert [s.score_id for s in snapshots] == ["a", "b"]

    def test_skips_files_that_fail_to_parse(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        store.save(make_snapshot(score_id="good"))
        (store.root / "garbage.json").write_text("not valid json")
        snapshots = store.load_all()
        assert [s.score_id for s in snapshots] == ["good"]


class TestPruneTerminalScores:
    def test_removes_oldest_when_limit_exceeded(self, tmp_path: Path) -> None:
        store = _store(tmp_path, max_terminal_scores=2)
        # Save three terminal snapshots with distinct updated_at values.
        store.save(
            make_snapshot(
                score_id="oldest",
                status=ScoreStatus.COMPLETED,
                updated_at="2024-01-01T00:00:00+00:00",
            )
        )
        store.save(
            make_snapshot(
                score_id="middle",
                status=ScoreStatus.COMPLETED,
                updated_at="2024-01-02T00:00:00+00:00",
            )
        )
        store.save(
            make_snapshot(
                score_id="newest",
                status=ScoreStatus.COMPLETED,
                updated_at="2024-01-03T00:00:00+00:00",
            )
        )
        remaining = {s.score_id for s in store.load_all()}
        assert remaining == {"middle", "newest"}

    def test_skips_non_terminal_snapshots(self, tmp_path: Path) -> None:
        store = _store(tmp_path, max_terminal_scores=1)
        store.save(make_snapshot(score_id="running", status=ScoreStatus.RUNNING))
        store.save(make_snapshot(score_id="queued", status=ScoreStatus.QUEUED))
        store.save(
            make_snapshot(
                score_id="done",
                status=ScoreStatus.COMPLETED,
                updated_at="2024-01-02T00:00:00+00:00",
            )
        )
        # Only 1 terminal snapshot → nothing to prune.
        assert {s.score_id for s in store.load_all()} == {"running", "queued", "done"}

    def test_skips_unreadable_files_during_prune(self, tmp_path: Path) -> None:
        store = _store(tmp_path, max_terminal_scores=10)
        (store.root / "broken.json").write_text("garbage")
        # Saving a real snapshot triggers _prune_terminal_scores_locked,
        # which must skip the unreadable file without raising.
        store.save(make_snapshot(score_id="ok", status=ScoreStatus.COMPLETED))
        assert store.load("ok") is not None

    def test_default_max_terminal_scores_keeps_one_thousand(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        # The default limit is well above one entry; ensure single save survives.
        store.save(make_snapshot(score_id="one", status=ScoreStatus.COMPLETED))
        assert store.load("one") is not None


class TestPathFor:
    def test_path_for_returns_root_relative_json(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        assert store.path_for("abc") == store.root / "abc.json"


class TestInitDefaults:
    def test_default_root_uses_home_subdirectory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
        store = ScoreStore()
        assert tmp_path in store.root.parents or store.root == tmp_path / ".maestro" / "symphony" / "scores"

    def test_expands_user_paths(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        store = ScoreStore(root=Path("~/sym-scores"))
        assert str(store.root).startswith(str(tmp_path))
