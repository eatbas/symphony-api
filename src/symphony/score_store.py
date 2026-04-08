from __future__ import annotations

import threading
from pathlib import Path

from .models import ScoreSnapshot
from .models.enums import TERMINAL_STATUSES

_MAX_TERMINAL_SCORES = 1000


class ScoreStore:
    """Disk-backed storage for Symphony score snapshots."""

    def __init__(self, root: Path | None = None, max_terminal_scores: int = _MAX_TERMINAL_SCORES) -> None:
        base = root or (Path.home() / ".maestro" / "symphony" / "scores")
        self.root = base
        self.max_terminal_scores = max_terminal_scores
        self._lock = threading.Lock()
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, score_id: str) -> Path:
        return self.root / f"{score_id}.json"

    def save(self, snapshot: ScoreSnapshot) -> None:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.path_for(snapshot.score_id)
            tmp_path = path.with_suffix(".json.tmp")
            tmp_path.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
            tmp_path.replace(path)
            self._prune_terminal_scores_locked()

    def load(self, score_id: str) -> ScoreSnapshot | None:
        path = self.path_for(score_id)
        if not path.exists():
            return None
        return ScoreSnapshot.model_validate_json(path.read_text(encoding="utf-8"))

    def load_all(self) -> list[ScoreSnapshot]:
        with self._lock:
            snapshots: list[ScoreSnapshot] = []
            if not self.root.exists():
                return snapshots

            for path in sorted(self.root.glob("*.json")):
                try:
                    snapshots.append(ScoreSnapshot.model_validate_json(path.read_text(encoding="utf-8")))
                except Exception:
                    continue
            return snapshots

    def _prune_terminal_scores_locked(self) -> None:
        snapshots: list[tuple[Path, ScoreSnapshot]] = []

        for path in self.root.glob("*.json"):
            try:
                snapshot = ScoreSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if snapshot.status in TERMINAL_STATUSES:
                snapshots.append((path, snapshot))

        excess = len(snapshots) - self.max_terminal_scores
        if excess <= 0:
            return

        snapshots.sort(key=lambda item: (item[1].updated_at, item[1].created_at, item[1].score_id))
        for path, _ in snapshots[:excess]:
            path.unlink(missing_ok=True)
