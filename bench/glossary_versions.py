"""Glossary versions as files, with provenance.

v000 is the basic glossary. v001 is that text plus the screen-1 pitfalls block. Both are snapshots of the strings
the sandbox already serves (bench.agents.GLOSSARY, bench.agent_cli.PITFALLS). v002 is the "+ invariants" level of
parameter #1 (research/analyzer_parameters_v1.md): basic plus only the pitfalls bullets that state facts about the
data (submission grain, current adjudication, de-duplicated 835s, line grain, time basis). It leaves out the
answer-shape conventions (percent arithmetic, ranking) that were written against the screen-1 questions and did
not replicate held-out. A later version is written only by the learner, after validation accepts an entry, and it
points at its parent.

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


INVARIANTS_HEADER = "Invariants:"
# 1-based positions of the PITFALLS bullets that are data invariants. 6 (percent arithmetic) and 7 (ranking) are
# the screen-1 answer-shape pitfalls and stay out of v002.
INVARIANT_PITFALL_BULLETS = (1, 2, 3, 4, 5)


def _invariant_entries(pitfall_entries: list[dict], provenance: dict) -> list[dict]:
    bullets = [entry for entry in pitfall_entries if entry["kind"] == "bullet"]
    chosen = [bullets[position - 1] for position in INVARIANT_PITFALL_BULLETS]
    header = _entry("invariants-00", "invariants", INVARIANTS_HEADER, provenance)
    return [header] + [_entry(entry["entry_id"].replace("pitfalls-", "invariants-"), "invariants", entry["text"],
                              provenance) for entry in chosen]


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
    invariant_prov = dict(origin="hand_written", source="bench/agent_cli.py PITFALLS bullets "
                          + ", ".join(str(position) for position in INVARIANT_PITFALL_BULLETS),
                          train_question_ids=[],
                          note="The data-invariant bullets of the screen-1 pitfalls block, without its answer-shape "
                               "pitfalls (percent arithmetic, ranking). Bullet text is unchanged.")
    v002 = _document("v002_invariants", "v000_basic",
                     [dict(section="basic", entries=basic_entries),
                      dict(section="invariants", entries=_invariant_entries(pitfall_entries, invariant_prov))],
                     "Basic definitions plus the invariants part of the screen-1 block (parameter #1 '+ invariants').")
    return [v000, v001, v002]


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
