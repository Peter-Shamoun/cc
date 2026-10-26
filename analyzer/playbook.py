"""B1's playbook (v1): one frozen file, distilled from TRAIN-split runs and reviewed, shown in every briefing."""

from __future__ import annotations

from pathlib import Path

PLAYBOOK_PATH = Path(__file__).resolve().parent / "playbook" / "frozen_playbook.md"


class PlaybookNotReady(RuntimeError):
    """The playbook file is empty."""


def playbook_text(path: Path | None = None) -> str:
    resolved = Path(path or PLAYBOOK_PATH)
    text = resolved.read_text()
    if not text.strip():
        raise PlaybookNotReady(f"{resolved} is empty: B1 cannot be briefed without its playbook")
    return text
