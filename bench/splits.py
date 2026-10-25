"""Frozen train / validation / test assignment for glossary learning.

Knowledge text is a trained artifact (research/heldout_results_1.md). Failures on the train split may be read and
turned into general glossary entries. The validation split accepts or rejects a change and is never read. The test
split is sealed: listing or running it raises SealedSplitError unless unseal_test=True, which is for the single
end-of-protocol run.

Near-duplicates cannot land in different splits. Questions are linked, and the whole link component is one group,
when any of these hold:

- same parametric family (every instance of a family, including ones added later)
- same template: a declared template bundle, an explicit ``template`` field, or the same normalized signature
  (numbers, dates, months, CPT/HCPCS and {placeholders} masked)
- a shared content 8-gram that no other question in the catalog has (a copied clause, in any author round)
- token Jaccard of the question text at least 0.72 (a near-copy; the known family/handwritten pairs sit here or
  in a bundle)

Author round is a stratum, not a leak. The initial assignment spreads each author round across the three splits
(50/25/25) so one round is not the whole of train or test. A template that crosses rounds stays in one group, in
the ``mixed`` stratum. Adding a question later does not move a frozen question. A new question joins an existing
group when it links to one, and otherwise goes to validation (round 3 is the expected case). Linking two groups
that already have different splits raises SplitConflict.

The assignment file records a content hash per question and a hash of the assignment. ``verify`` fails if a
question's text, SQL, family, template or author round changes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

import yaml

from bench.question_families import FAMILIES

ROOT = Path(__file__).resolve().parent.parent
ASSIGNMENT_PATH = Path(__file__).with_name("question_splits.json")
QUESTIONS_PATH = Path(__file__).with_name("questions.yaml")
AUTHORED_PATH = Path(__file__).with_name("authored_questions.json")

ALGORITHM = "splits-v1"
SEED = "cc-glossary-splits-v1"
SPLITS = ("train", "validation", "test")
TARGETS = {"train": 0.50, "validation": 0.25, "test": 0.25}
JACCARD_MIN = 0.72
PHRASE_N = 8

# Handwritten questions and the parametric family that instantiates the same ask, plus authored questions that
# are the same metric with another parameter. Members of a bundle always share a split.
TEMPLATE_BUNDLES: dict[str, list[str]] = {
    "denial_rate": [
        "q07_denial_rate", "q08_denial_rate_by_payer",
        "family:payer_denial_rate", "family:payer_class_denial_rate", "family:payer_monthly_denial_rate",
        "a_54dd76175a", "a_d671e0b995", "a_c9e9bf82ca",
    ],
    "denial_carc": [
        "q10_top_denial_carcs", "q21_timely_filing",
        "family:payer_top_denial_carcs", "family:denials_with_carc",
        "a_436f690741",
    ],
    "partial_line_denial": [
        "q15_partial_line_denials", "family:payer_partial_line_denials", "a_16eb866e01",
    ],
    "primary_denial_rate_by_dimension": [
        "q12_referring_provider_denials", "q13_principal_dx_denials",
    ],
    "days_to_first_remit": [
        "q22_days_to_first_remit", "family:payer_days_to_first_remit",
    ],
    "secondary_payment": [
        "q23_secondary_paid", "family:secondary_paid_for_primary",
    ],
    "never_remitted": [
        "q20_never_remitted", "family:never_remitted_submitted_in_month",
    ],
    "paid_in_month": [
        "q04_paid_by_month", "family:payer_paid_in_month",
    ],
    "patient_responsibility": [
        "q24_primary_patient_responsibility", "family:payer_patient_responsibility",
    ],
    "frequency_code_count": [
        "q17_resubmitted", "q18_voided",
    ],
    "both_payers_adjudicated": [
        "a_168f1edf0c", "a_f5c198c4ff",
    ],
    "never_reworked_denial": [
        "a_4fe8f9aefc", "a_6ad432f575", "a_d219ba8c2d",
    ],
}

_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december")
_STOPWORDS = frozenset("""a an the of to for and or in on by with from that this these those is are was were be
been being as at it its their our we you what which how many much show give count using use only per each once
when where who whom whose into over under than then so do does did not no nor but if about across among between
both all any some other more most least less have has had will would can could should must may might also one two
three four five return order rounded decimal decimals place group grouped""".split())


class SplitError(ValueError):
    pass


class SealedSplitError(SplitError):
    """Raised when a caller asks to list or run the test split without unseal_test=True."""


class SplitConflict(SplitError):
    """A new question is a near-duplicate of frozen questions that do not share a split."""


def template_signature(question: str) -> str:
    """Parameter-masked question text. Two wordings that differ only by a payer, month, code or number match."""
    text = question.lower()
    text = re.sub(r"\{[a-z0-9_]+\}", " <param> ", text)
    text = re.sub(r"\b20\d{2}-\d{2}(?:-\d{2})?\b", " <param> ", text)
    text = re.sub(r"\b(" + "|".join(_MONTHS) + r")(?:\s+20\d{2})?\b", " <param> ", text)
    text = re.sub(r"\$[\d,]+(?:\.\d+)?", " <param> ", text)
    text = re.sub(r"\b[a-z]\d{4}\b", " <param> ", text)
    text = re.sub(r"\b\d{5}\b", " <param> ", text)
    text = re.sub(r"\b\d+(?:\.\d+)?%?\b", " <param> ", text)
    text = re.sub(r"[^a-z<>\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def content_tokens(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"\{[a-z0-9_]+\}", " ", text)
    text = re.sub(r"\b\d+(?:\.\d+)?\b", " ", text)
    return [w for w in re.findall(r"[a-z][a-z0-9_]+", text) if w not in _STOPWORDS and len(w) > 2]


def _ngrams(tokens: list[str], n: int = PHRASE_N) -> set[str]:
    return {" ".join(tokens[i:i + n]) for i in range(max(0, len(tokens) - n + 1))}


def _jaccard(a: list[str], b: list[str]) -> float:
    left, right = set(a), set(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _bundle_index(bundles: dict[str, list[str]]) -> dict[str, str]:
    index = {}
    for name, members in bundles.items():
        for member in members:
            if member in index:
                raise SplitError(f"{member} is in template bundles {index[member]!r} and {name!r}")
            index[member] = name
    return index


def bundles_hash(bundles: dict[str, list[str]] | None = None) -> str:
    payload = {name: list(members) for name, members in (bundles if bundles is not None else TEMPLATE_BUNDLES).items()}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def content_hash(record: dict) -> str:
    payload = {
        "answer_key_sql": record.get("answer_key_sql") or "",
        "author_round": record["author_round"],
        "category": record.get("category") or "",
        "difficulty": record.get("difficulty") or "",
        "expect_refusal": bool(record.get("expect_refusal")),
        "family": record.get("family") or "",
        "gold_sql": record.get("gold_sql") or "",
        "id": record["id"],
        "ledger": record.get("ledger") or "",
        "origin": record["origin"],
        "question": record["question"],
        "template_bundle": record.get("template_bundle") or "",
        "template_signature": record["template_signature"],
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def load_catalog() -> list[dict]:
    """Hand-written questions, Opus-authored questions, and one record per parametric family.

    Family instances are not in the catalog. They inherit the family's split at selection time, so a new draw of
    the same family cannot leak into another split. 33 hand-written + 66 authored = 99 concrete questions; the
    16 family records are templates, not extra questions.
    """
    handwritten = yaml.safe_load(QUESTIONS_PATH.read_text())
    authored = json.loads(AUTHORED_PATH.read_text()) if AUTHORED_PATH.exists() else []
    records = []
    for question in handwritten:
        records.append(_record_from_question(question, origin="handwritten", author_round="handwritten"))
    for question in authored:
        records.append(_record_from_question(
            question, origin="opus_authored", author_round=question.get("author_round") or question.get("question_set") or "opus_1"))
    for family in FAMILIES:
        records.append(dict(
            id=f"family:{family.family_id}", origin="parametric_family", author_round="parametric",
            family=family.family_id, category=family.category, difficulty=family.difficulty,
            question=family.phrasings[0] + "\n" + family.phrasings[1], gold_sql=family.gold_sql,
            answer_key_sql=family.answer_key_sql, expect_refusal=False, template="",
        ))
    ids = [r["id"] for r in records]
    if len(ids) != len(set(ids)):
        raise SplitError("duplicate ids in the question catalog")
    return records


def _record_from_question(question: dict, origin: str, author_round: str) -> dict:
    if "id" not in question or "question" not in question:
        raise SplitError("a question record needs id and question")
    return dict(
        id=question["id"], origin=origin, author_round=question.get("author_round") or author_round,
        family=question.get("family") or "", category=question.get("category") or "",
        difficulty=question.get("difficulty") or "", question=question["question"],
        gold_sql=question.get("gold_sql") or "", answer_key_sql=question.get("answer_key_sql") or "",
        ledger=question.get("ledger") or "", expect_refusal=bool(question.get("expect_refusal")),
        template=question.get("template") or "",
    )


def _prepare(records: list[dict], bundles: dict[str, list[str]]) -> list[dict]:
    index = _bundle_index(bundles)
    known = {r["id"] for r in records}
    missing = sorted(set(index) - known)
    if missing:
        raise SplitError(f"template bundle names questions that are not in the catalog: {missing}")
    prepared = []
    for record in records:
        item = dict(record)
        item["family"] = item.get("family") or ""
        item["template_signature"] = template_signature(item["question"])
        # A declared bundle wins. Otherwise an explicit template name (tests, a later round) joins questions
        # that share it. The display `template` is never fed back in: callers pass raw records.
        explicit = item.get("template") or ""
        if explicit and explicit == item["template_signature"]:
            explicit = ""
        item["template_bundle"] = index.get(item["id"]) or explicit or ""
        item["template"] = item["template_bundle"] or item["template_signature"]
        prepared.append(item)
    return prepared


def _exclusive_phrases(records: list[dict]) -> set[tuple[str, str]]:
    """Pairs that share a content 8-gram nobody else has, in any author round."""
    owners: dict[str, list[str]] = defaultdict(list)
    for record in records:
        for gram in _ngrams(content_tokens(record["question"])):
            owners[gram].append(record["id"])
    pairs = set()
    for ids in owners.values():
        if len(ids) != 2:
            continue
        pairs.add(tuple(sorted(ids)))
    return pairs


class _UnionFind:
    def __init__(self, ids: list[str]):
        self.parent = {i: i for i in ids}

    def find(self, item: str) -> str:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: str, right: str) -> None:
        ra, rb = self.find(left), self.find(right)
        if ra == rb:
            return
        if ra < rb:
            self.parent[rb] = ra
        else:
            self.parent[ra] = rb


def group_records(records: list[dict], bundles: dict[str, list[str]] | None = None) -> dict[str, str]:
    """Question id -> group id. Group id is ``g:`` plus the smallest member id, so it is stable for a component."""
    bundles = TEMPLATE_BUNDLES if bundles is None else bundles
    prepared = _prepare(records, bundles)
    by_id = {r["id"]: r for r in prepared}
    phrases = _exclusive_phrases(prepared)
    uf = _UnionFind(list(by_id))
    ids = list(by_id)
    for i, left_id in enumerate(ids):
        left = by_id[left_id]
        for right_id in ids[i + 1:]:
            right = by_id[right_id]
            same_family = bool(left["family"]) and left["family"] == right["family"]
            same_bundle = bool(left["template_bundle"]) and left["template_bundle"] == right["template_bundle"]
            same_signature = left["template_signature"] == right["template_signature"]
            near_copy = _jaccard(content_tokens(left["question"]), content_tokens(right["question"])) >= JACCARD_MIN
            same_phrase = tuple(sorted((left_id, right_id))) in phrases
            if same_family or same_bundle or same_signature or near_copy or same_phrase:
                uf.union(left_id, right_id)
    return {qid: "g:" + uf.find(qid) for qid in ids}


def _stratum(members: list[dict]) -> str:
    rounds = sorted({m["author_round"] for m in members})
    return rounds[0] if len(rounds) == 1 else "mixed"


def assign_group_splits(groups: list[dict], *, seed: str = SEED, targets: dict | None = None) -> dict[str, str]:
    """group_id -> split. ``groups`` items have group_id, stratum and size. Balanced inside each author-round stratum."""
    targets = targets or TARGETS
    if set(targets) != set(SPLITS):
        raise SplitError(f"targets must name {SPLITS}")
    if abs(sum(targets.values()) - 1) > 1e-9:
        raise SplitError("split targets must sum to 1")
    by_stratum: dict[str, list[dict]] = defaultdict(list)
    for group in groups:
        by_stratum[group["stratum"]].append(group)
    assigned = {}
    for stratum in sorted(by_stratum):
        members = by_stratum[stratum]
        total = sum(g["size"] for g in members)
        goal = {split: targets[split] * total for split in SPLITS}
        counts = {split: 0 for split in SPLITS}
        # Largest groups first. A template bundle is one group, and hash order alone can fill train with
        # singletons and then drop that whole bundle into the sealed test split.
        ordered = sorted(members, key=lambda g: (
            -g["size"], hashlib.sha256(f"{seed}|{stratum}|{g['group_id']}".encode()).hexdigest()))
        for group in ordered:
            choice = max(SPLITS, key=lambda split: (goal[split] - counts[split], -SPLITS.index(split)))
            assigned[group["group_id"]] = choice
            counts[choice] += group["size"]
    return assigned


def initial_assignment(records: list[dict] | None = None, bundles: dict[str, list[str]] | None = None) -> dict:
    bundles = TEMPLATE_BUNDLES if bundles is None else bundles
    records = load_catalog() if records is None else records
    prepared = _prepare(records, bundles)
    groups = group_records(records, bundles)
    members: dict[str, list[dict]] = defaultdict(list)
    for record in prepared:
        members[groups[record["id"]]].append(record)
    group_rows = [dict(group_id=gid, stratum=_stratum(rows), size=len(rows)) for gid, rows in members.items()]
    splits = assign_group_splits(group_rows)
    questions = []
    for record in prepared:
        gid = groups[record["id"]]
        questions.append(dict(
            id=record["id"], split=splits[gid], group_id=gid, group_size=len(members[gid]),
            author_round=record["author_round"], family=record.get("family") or "",
            template=record["template"], template_bundle=record.get("template_bundle") or "",
            template_signature=record["template_signature"], origin=record["origin"],
            content_hash=content_hash(record),
        ))
    questions.sort(key=lambda row: row["id"])
    doc = dict(
        algorithm=ALGORITHM, seed=SEED, targets=dict(TARGETS), bundles_hash=bundles_hash(bundles),
        questions=questions, additions=[],
    )
    doc["assignment_hash"] = _assignment_hash(questions)
    doc["summary"] = summarize(doc)
    return doc


def _assignment_hash(questions: list[dict]) -> str:
    payload = [dict(id=q["id"], split=q["split"], group_id=q["group_id"], content_hash=q["content_hash"]) for q in questions]
    payload.sort(key=lambda row: row["id"])
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def summarize(doc: dict) -> dict:
    rows = doc["questions"]
    concrete = [r for r in rows if r["origin"] != "parametric_family"]

    def counts(items):
        return {split: sum(1 for r in items if r["split"] == split) for split in SPLITS}

    by_round = {}
    for row in concrete:
        by_round.setdefault(row["author_round"], counts([]))
    for row in concrete:
        by_round[row["author_round"]][row["split"]] += 1
    return dict(
        records=len(rows), concrete_questions=len(concrete), groups=len({r["group_id"] for r in rows}),
        by_split=counts(concrete), by_split_including_family_templates=counts(rows), by_author_round=by_round,
    )


def load_assignment(path: Path | None = None) -> dict:
    path = Path(path) if path else ASSIGNMENT_PATH
    if not path.exists():
        raise SplitError(f"no frozen assignment at {path}; write one with python -m bench.splits write")
    return json.loads(path.read_text())


def write_assignment(path: Path | None = None, records: list[dict] | None = None,
                     bundles: dict[str, list[str]] | None = None) -> dict:
    path = Path(path) if path else ASSIGNMENT_PATH
    if path.exists():
        raise SplitError(f"{path} is frozen; sync adds questions without moving the ones already assigned")
    doc = initial_assignment(records, bundles)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1) + "\n")
    return doc


def verify(path: Path | None = None, records: list[dict] | None = None,
           bundles: dict[str, list[str]] | None = None) -> dict:
    """Recompute hashes and links. Fail if a frozen question changed, or if two linked frozen questions differ in split."""
    bundles = TEMPLATE_BUNDLES if bundles is None else bundles
    doc = load_assignment(path)
    if doc.get("algorithm") != ALGORITHM:
        raise SplitError(f"assignment algorithm is {doc.get('algorithm')!r}, this code is {ALGORITHM!r}")
    if doc.get("bundles_hash") != bundles_hash(bundles):
        raise SplitError("template bundles changed after the assignment was frozen")
    records = load_catalog() if records is None else records
    prepared = {r["id"]: r for r in _prepare(records, bundles)}
    rows = {r["id"]: r for r in doc["questions"]}
    for qid, row in rows.items():
        if qid not in prepared:
            if row.get("added"):
                continue
            raise SplitError(f"frozen question {qid} is missing from the catalog")
        current = content_hash(prepared[qid])
        if current != row["content_hash"]:
            raise SplitError(f"question {qid} changed after its split was frozen (content hash mismatch)")
    groups = group_records([r for r in records if r["id"] in rows], bundles)
    frozen_ids = [qid for qid in groups if qid in rows]
    for i, left in enumerate(frozen_ids):
        for right in frozen_ids[i + 1:]:
            if groups[left] == groups[right] and rows[left]["split"] != rows[right]["split"]:
                raise SplitError(f"{left} and {right} are near-duplicates but frozen into {rows[left]['split']} and {rows[right]['split']}")
    expected = _assignment_hash(doc["questions"])
    if doc.get("assignment_hash") != expected:
        raise SplitError("assignment_hash does not match the frozen rows")
    return doc


def _parse_splits(split: str) -> list[str]:
    names = [part.strip() for part in split.split(",") if part.strip()]
    unknown = [name for name in names if name not in SPLITS]
    if unknown or not names:
        raise SplitError(f"unknown split {split!r}; expected {', '.join(SPLITS)}")
    return names


def iter_assignment(split: str, *, unseal_test: bool = False, path: Path | None = None,
                    include_family_templates: bool = False) -> list[dict]:
    names = _parse_splits(split)
    if "test" in names and not unseal_test:
        raise SealedSplitError(
            "The test split is sealed. It is run once, at the end of the protocol, with unseal_test=True.")
    doc = load_assignment(path)
    rows = [r for r in doc["questions"] if r["split"] in names]
    if not include_family_templates:
        rows = [r for r in rows if r["origin"] != "parametric_family"]
    return rows


def select_cases(cases: list[dict], split: str, *, unseal_test: bool = False, path: Path | None = None) -> list[dict]:
    """Keep cases whose frozen split is in ``split``. Family instances inherit the family's split.

    A case with no assignment fails. Silently running it would let an unassigned question, including a test
    question, into a train or validation run.
    """
    names = set(_parse_splits(split))
    if "test" in names and not unseal_test:
        raise SealedSplitError(
            "The test split is sealed. It is run once, at the end of the protocol, with unseal_test=True.")
    doc = load_assignment(path)
    by_id = {r["id"]: r for r in doc["questions"]}
    kept = []
    for case in cases:
        row = by_id.get(case["id"])
        if row is None and case.get("family"):
            row = by_id.get(f"family:{case['family']}")
        if row is None:
            raise SplitError(f"question {case['id']} has no frozen split; sync the assignment before running it")
        if row["split"] in names:
            kept.append(case)
    return kept


def _linked(left: dict, right: dict) -> bool:
    """Near-duplicate test used when appending. An empty family or template does not match another empty one."""
    same_family = bool(left.get("family")) and left.get("family") == right.get("family")
    same_bundle = bool(left.get("template_bundle")) and left.get("template_bundle") == right.get("template_bundle")
    same_signature = left.get("template_signature") == right.get("template_signature") and bool(left.get("template_signature"))
    near_copy = _jaccard(content_tokens(left["question"]), content_tokens(right["question"])) >= JACCARD_MIN
    same_phrase = bool(_ngrams(content_tokens(left["question"])) & _ngrams(content_tokens(right["question"])))
    return same_family or same_bundle or same_signature or near_copy or same_phrase


def add_questions(new_questions: list[dict], path: Path | None = None, *, destination: str = "validation",
                  bundles: dict[str, list[str]] | None = None, catalog: list[dict] | None = None) -> dict:
    """Append questions. Default destination is validation. Near-duplicates inherit the frozen split instead.

    Does not move any question already in the file. Raises SplitConflict when the new question is a near-duplicate
    of frozen questions that were assigned different splits. ``catalog`` is the question set the frozen rows were
    built from; it defaults to the repo catalog. New questions are not required to be in it yet (round 3).
    """
    if destination not in SPLITS:
        raise SplitError(f"destination must be one of {SPLITS}")
    if not new_questions:
        return verify(path, records=catalog, bundles=bundles)
    bundles = TEMPLATE_BUNDLES if bundles is None else bundles
    path = Path(path) if path else ASSIGNMENT_PATH
    catalog_records = load_catalog() if catalog is None else catalog
    doc = verify(path, records=catalog_records, bundles=bundles)
    prepared_catalog = {r["id"]: r for r in _prepare(catalog_records, bundles)}
    frozen_full = []
    for row in doc["questions"]:
        source = prepared_catalog.get(row["id"])
        if source is None:
            if row.get("added"):
                continue
            raise SplitError(f"frozen question {row['id']} is not in the catalog")
        full = dict(row)
        full["question"] = source["question"]
        full["template_bundle"] = source["template_bundle"]
        full["template_signature"] = source["template_signature"]
        full["family"] = source.get("family") or ""
        frozen_full.append(full)
    for row in doc["questions"]:
        if row["id"] in prepared_catalog or not row.get("added"):
            continue
        if not row.get("question"):
            raise SplitError(f"added question {row['id']} has no stored text, so later questions cannot be checked against it")
        frozen_full.append(row)
    existing_ids = {r["id"] for r in doc["questions"]}
    raw_new = []
    for question in new_questions:
        record = _record_from_question(
            question, origin=question.get("origin") or "added",
            author_round=question.get("author_round") or question.get("question_set") or "added")
        if record["id"] in existing_ids:
            raise SplitError(f"question {record['id']} is already assigned")
        raw_new.append(record)
    if len({r["id"] for r in raw_new}) != len(raw_new):
        raise SplitError("duplicate ids in the questions being added")
    prepared_new = _prepare(raw_new, bundles)
    batch_groups = group_records(raw_new, bundles)
    touches: dict[str, set[str]] = {}
    direct_groups: dict[str, set[str]] = {}
    for record in prepared_new:
        splits_hit = set()
        groups_hit = set()
        for frozen_row in frozen_full:
            if _linked(record, frozen_row):
                splits_hit.add(frozen_row["split"])
                groups_hit.add(frozen_row["group_id"])
        touches[record["id"]] = splits_hit
        direct_groups[record["id"]] = groups_hit
    by_new_group: dict[str, list[str]] = defaultdict(list)
    for record in prepared_new:
        by_new_group[batch_groups[record["id"]]].append(record["id"])
    chosen_group: dict[str, tuple[str, str]] = {}
    for ids in by_new_group.values():
        union_splits: set[str] = set()
        union_groups: set[str] = set()
        for qid in ids:
            union_splits |= touches[qid]
            union_groups |= direct_groups[qid]
        if len(union_splits) > 1:
            raise SplitConflict(
                f"new questions {ids} are near-duplicates of frozen questions in different splits: {sorted(union_splits)}")
        split = next(iter(union_splits)) if union_splits else destination
        group_id = min(union_groups) if union_groups else batch_groups[ids[0]]
        for qid in ids:
            touches[qid] = set(union_splits)
            chosen_group[qid] = (split, group_id)
    for record in prepared_new:
        split, group_id = chosen_group[record["id"]]
        inherited = bool(touches[record["id"]])
        row = dict(
            id=record["id"], split=split, group_id=group_id, group_size=0,
            author_round=record["author_round"], family=record.get("family") or "",
            template=record["template"], template_bundle=record.get("template_bundle") or "",
            template_signature=record["template_signature"], origin=record["origin"],
            content_hash=content_hash(record), added=True, inherited_split=inherited,
            question=record["question"],
        )
        doc["questions"].append(row)
        doc.setdefault("additions", []).append(
            dict(id=record["id"], split=split, inherited_split=inherited, destination=destination))
    sizes: dict[str, int] = defaultdict(int)
    for row in doc["questions"]:
        sizes[row["group_id"]] += 1
    for row in doc["questions"]:
        row["group_size"] = sizes[row["group_id"]]
    doc["questions"].sort(key=lambda r: r["id"])
    doc["assignment_hash"] = _assignment_hash(doc["questions"])
    doc["summary"] = summarize(doc)
    path.write_text(json.dumps(doc, indent=1) + "\n")
    return doc


def sync(path: Path | None = None, *, destination: str = "validation") -> dict:
    """Verify the freeze, then assign catalog questions that are not in it yet. New groups go to ``destination``."""
    path = Path(path) if path else ASSIGNMENT_PATH
    doc = load_assignment(path)
    frozen_ids = {r["id"] for r in doc["questions"]}
    catalog = load_catalog()
    missing = [r for r in catalog if r["id"] not in frozen_ids]
    frozen_catalog = [r for r in catalog if r["id"] in frozen_ids]
    if not missing:
        return verify(path, records=frozen_catalog)
    return add_questions(missing, path, destination=destination, catalog=frozen_catalog)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cmd", choices=["write", "verify", "summary", "ids", "sync"])
    parser.add_argument("--split")
    parser.add_argument("--unseal-test", action="store_true")
    parser.add_argument("--path")
    args = parser.parse_args()
    path = Path(args.path) if args.path else None
    if args.cmd == "write":
        doc = write_assignment(path)
        print(json.dumps(doc["summary"], indent=1))
    elif args.cmd == "verify":
        doc = verify(path)
        print(json.dumps(dict(ok=True, assignment_hash=doc["assignment_hash"], **doc["summary"]), indent=1))
    elif args.cmd == "summary":
        print(json.dumps(summarize(load_assignment(path)), indent=1))
    elif args.cmd == "ids":
        if not args.split:
            raise SystemExit("--split is required")
        rows = iter_assignment(args.split, unseal_test=args.unseal_test, path=path)
        print("\n".join(r["id"] for r in rows))
    else:
        doc = sync(path)
        print(json.dumps(dict(assignment_hash=doc["assignment_hash"], **doc["summary"]), indent=1))


if __name__ == "__main__":
    main()
