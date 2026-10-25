"""Glossary versions as files, with provenance.

v000 is the basic glossary. v001 is that text plus the screen-1 pitfalls block. Both are snapshots of the strings
the sandbox already serves (bench.agents.GLOSSARY, bench.agent_cli.PITFALLS). A later version is written only by
the learner, after validation accepts an entry, and it points at its parent.

Rendering v000 and v001 must stay byte-identical to those strings. The sandbox serves a ``version:<id>`` glossary
setting through render_version, so a screen can pin the text it scored.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSIONS_DIR = Path(__file__).with_name("glossaries")


def _basic_text() -> str:
    from bench.agents import GLOSSARY
    return GLOSSARY


def _pitfalls_text() -> str:
    from bench.agent_cli import PITFALLS
    return PITFALLS


def split_entries(text: str) -> list[str]:
    """Header line, then one string per bullet. Joining with newlines reproduces the stripped source."""
    entries: list[str] = []
    current: list[str] = []
    for line in text.strip("\n").split("\n"):
        if line.startswith("- ") and current:
            entries.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        entries.append("\n".join(current))
    return entries


def _entry(entry_id: str, section: str, text: str, provenance: dict) -> dict:
    return dict(entry_id=entry_id, section=section, kind="header" if not text.startswith("- ") else "bullet",
                text=text, provenance=provenance)


def _document(version_id: str, parent_id: str | None, sections: list[dict], note: str) -> dict:
    payload = dict(version_id=version_id, parent_id=parent_id, sections=sections, note=note)
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    payload["content_hash"] = hashlib.sha256(raw.encode()).hexdigest()
    return payload


def canonical_documents() -> list[dict]:
    basic_prov = dict(origin="hand_written", source="bench/agents.py GLOSSARY",
                      train_question_ids=[], note="Written before the train/validation/test protocol.")
    pitfall_prov = dict(origin="hand_written", source="bench/agent_cli.py PITFALLS", train_question_ids=[],
                        note="Written in the same session as the screen-1 questions, before heldout_hard_1. "
                             "The +16 Haiku point estimate did not replicate on that held-out set.")
    basic_entries = [_entry(f"basic-{i:02d}", "basic", text, basic_prov)
                     for i, text in enumerate(split_entries(_basic_text()), start=1)]
    pitfall_entries = [_entry(f"pitfalls-{i:02d}", "pitfalls", text, pitfall_prov)
                       for i, text in enumerate(split_entries(_pitfalls_text()), start=1)]
    v000 = _document("v000_basic", None, [dict(section="basic", entries=basic_entries)],
                     "Basic business definitions. No pitfalls block.")
    v001 = _document("v001_screen1_pitfalls", "v000_basic",
                     [dict(section="basic", entries=basic_entries), dict(section="pitfalls", entries=pitfall_entries)],
                     "Screen-1 pitfalls glossary: basic definitions plus the invariants and traps block.")
    return [v000, v001]


def render_document(doc: dict) -> str:
    """Join sections the way the sandbox concatenates basic and pitfalls: a blank line between them."""
    parts = []
    for section in doc["sections"]:
        body = "\n".join(entry["text"] for entry in section["entries"])
        parts.append(body + "\n")
    return "\n".join(parts)


def render_version(version_id: str, directory: Path | None = None) -> str:
    return render_document(load_version(version_id, directory))


def version_path(version_id: str, directory: Path | None = None) -> Path:
    return (directory or VERSIONS_DIR) / f"{version_id}.json"


def load_version(version_id: str, directory: Path | None = None) -> dict:
    path = version_path(version_id, directory)
    if not path.exists():
        raise FileNotFoundError(f"no glossary version {version_id} at {path}")
    return json.loads(path.read_text())


def write_version(doc: dict, directory: Path | None = None) -> Path:
    path = version_path(doc["version_id"], directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    return path


def write_canonical_versions(directory: Path | None = None) -> list[Path]:
    return [write_version(doc, directory) for doc in canonical_documents()]


def iter_entries(doc: dict):
    for section in doc["sections"]:
        for entry in section["entries"]:
            yield entry


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cmd", choices=["write", "check"])
    args = parser.parse_args()
    if args.cmd == "write":
        for path in write_canonical_versions():
            print(path)
    else:
        for doc in canonical_documents():
            on_disk = load_version(doc["version_id"])
            if render_document(on_disk) != render_document(doc):
                raise SystemExit(f"{doc['version_id']} does not match the canonical glossary text")
            print(doc["version_id"], "ok", doc["content_hash"][:12])


if __name__ == "__main__":
    main()
