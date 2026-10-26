"""#2 playbook slot: one frozen file, hashed into the session meta when prepare() creates a session.

The file is written only after the TRAIN-split distillation and human review in research/analyzer_bundles_v1.md
(build item 10). Until then it is empty, and a bundle that turns the playbook on cannot be prepared: B1 and B10 wait
on it. The briefing re-hashes the file and fails if it changed after the session was prepared.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

PLAYBOOK_PATH = Path(__file__).resolve().parent / "playbook" / "frozen_playbook.md"


class PlaybookNotReady(RuntimeError):
    """The playbook file is empty (not yet distilled and reviewed) or changed after a session was prepared."""


def playbook_text(path: Path | None = None) -> str:
    resolved = Path(path or PLAYBOOK_PATH)
    text = resolved.read_text()
    if not text.strip():
        raise PlaybookNotReady(f"{resolved} is empty: the playbook has not been distilled from TRAIN runs and "
                               "reviewed yet (bundles B1 and B10 wait on it)")
    return text


def playbook_sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def stamp(path: Path | None = None) -> dict:
    """Session meta fields that freeze the playbook a session saw. Raises while the file is empty."""
    resolved = Path(path or PLAYBOOK_PATH)
    return {"playbook_path": str(resolved), "playbook_sha256": playbook_sha256(playbook_text(resolved))}


def frozen_text_for(meta: dict) -> str:
    """The playbook text a prepared session shows, after checking it is the text that was hashed."""
    if not meta.get("playbook_sha256"):
        raise PlaybookNotReady("session meta has no playbook hash: prepare the session through eval_subagents")
    text = playbook_text(Path(meta["playbook_path"]))
    if playbook_sha256(text) != meta["playbook_sha256"]:
        raise PlaybookNotReady("the playbook file changed after this session was prepared")
    return text
