"""How much each glossary entry overlaps specific benchmark questions.

The screen-1 pitfalls block was written beside the questions it was scored on, and its Haiku gain did not show up
on heldout_hard_1. This audit measures that risk. It does not decide the split and it does not read a sealed test
run; it only compares glossary text to question text, gold SQL and authored interpretations.

An entry is flagged question-specific when a 5-gram of its content words appears in exactly one question, or when
its distinctive words concentrate on one or two questions (IDF-weighted recall). General definitions that share
ordinary words ("claim", "current adjudication") with many questions are not flagged. Overlap with screen-1
questions and with heldout_hard_1 is reported separately, because a higher screen-1 overlap is the tuning signal
the held-out result raised.

    python -m bench.glossary_contamination
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from pathlib import Path

from bench.glossary_versions import canonical_documents, iter_entries
from bench.splits import content_tokens, load_catalog

ROOT = Path(__file__).resolve().parent.parent
SCREENING_PATH = ROOT / "research" / "screening_questions.json"
AUTHORED_PATH = ROOT / "bench" / "authored_questions.json"
REPORT_PATH = ROOT / "research" / "glossary_contamination.md"

# A 5-gram of content words in exactly one question is a copied clause, not a domain term.
VERBATIM_N = 5
# IDF-weighted share of the entry's content that lands in one question.
CONCENTRATED_RECALL = 0.45
# How many other questions may also match before the entry counts as general.
CONCENTRATED_SUPPORT = 2
SUPPORT_RECALL = 0.30


def _ngrams(tokens: list[str], n: int) -> set[str]:
    return {" ".join(tokens[i:i + n]) for i in range(max(0, len(tokens) - n + 1))}


def load_question_documents() -> list[dict]:
    screening = json.loads(SCREENING_PATH.read_text())
    screen_ids = set(screening["question_ids"])
    screen_families = set()
    for qid in screen_ids:
        if qid.startswith("f_") and "__" in qid:
            screen_families.add(qid[2:].split("__", 1)[0])
    interpretations = {}
    if AUTHORED_PATH.exists():
        for question in json.loads(AUTHORED_PATH.read_text()):
            interpretations[question["id"]] = question.get("interpretation") or ""
    documents = []
    for record in load_catalog():
        if record["id"] in screen_ids or (record.get("family") and record["family"] in screen_families
                                          and record["origin"] == "parametric_family"):
            tag = "screen1"
        elif record["author_round"] == "heldout_hard_1":
            tag = "heldout"
        else:
            tag = "other"
        answer = "\n".join(part for part in (record.get("gold_sql") or "", interpretations.get(record["id"], "")) if part)
        documents.append(dict(id=record["id"], tag=tag, origin=record["origin"], author_round=record["author_round"],
                              question=record["question"], answer=answer,
                              question_tokens=content_tokens(record["question"]),
                              answer_tokens=content_tokens(answer)))
    return documents


def _idf(documents: list[dict]) -> dict[str, float]:
    df: dict[str, int] = defaultdict(int)
    for doc in documents:
        for token in set(doc["question_tokens"]):
            df[token] += 1
    n = len(documents)
    return {token: math.log((n + 1) / (count + 1)) + 1 for token, count in df.items()}


def _recall(entry_tokens: list[str], doc_tokens: set[str], idf: dict[str, float]) -> float:
    weights = [idf.get(token, math.log(len(idf) + 1) + 1) for token in entry_tokens]
    total = sum(weights)
    if total <= 0:
        return 0.0
    hit = sum(weight for token, weight in zip(entry_tokens, weights) if token in doc_tokens)
    return hit / total


def score_glossary(documents: list[dict] | None = None) -> dict:
    documents = documents if documents is not None else load_question_documents()
    idf = _idf(documents)
    question_ngrams: dict[str, set[str]] = {doc["id"]: _ngrams(doc["question_tokens"], VERBATIM_N) for doc in documents}
    gram_owners: dict[str, set[str]] = defaultdict(set)
    for qid, grams in question_ngrams.items():
        for gram in grams:
            gram_owners[gram].add(qid)
    per_entry = []
    for doc_version in canonical_documents():
        for entry in iter_entries(doc_version):
            if entry["kind"] != "bullet":
                continue
            # Score each bullet once. v001 repeats the basic bullets; keep the first version that has the entry id
            # prefix, but pitfalls are only on v001. Dedupe by text.
            tokens = content_tokens(entry["text"])
            if not tokens:
                continue
            text_scores = [(document["id"], document["tag"], _recall(tokens, set(document["question_tokens"]), idf))
                           for document in documents]
            answer_scores = [(document["id"], document["tag"], _recall(tokens, set(document["answer_tokens"]), idf))
                             for document in documents]
            text_scores.sort(key=lambda row: -row[2])
            answer_scores.sort(key=lambda row: -row[2])
            support = [row for row in text_scores if row[2] >= SUPPORT_RECALL]
            verbatim = []
            for gram in sorted(_ngrams(tokens, VERBATIM_N)):
                owners = gram_owners.get(gram, set())
                if len(owners) == 1:
                    verbatim.append(dict(gram=gram, question_id=next(iter(owners))))
            best_text = text_scores[0]
            best_answer = answer_scores[0]
            # A 5-gram that also occurs inside a bullet matching dozens of questions is a shared definition,
            # not a question-specific entry. Flag verbatim only when the bullet is not widely used.
            flagged = is_question_specific(best_recall=best_text[2], support_count=len(support),
                                            verbatim_hits=len(verbatim))
            concentrated = best_text[2] >= CONCENTRATED_RECALL and len(support) <= CONCENTRATED_SUPPORT
            verbatim_rare = bool(verbatim) and len(support) <= CONCENTRATED_SUPPORT
            reasons = []
            if verbatim_rare:
                reasons.append(f"5-gram appears in only {verbatim[0]['question_id']}")
            if concentrated:
                reasons.append(
                    f"IDF recall {best_text[2]:.2f} on {best_text[0]} and only {len(support)} questions reach {SUPPORT_RECALL}")
            per_entry.append(dict(
                version_id=doc_version["version_id"], section=entry["section"], entry_id=entry["entry_id"],
                text=entry["text"], best_question=best_text[0], best_question_tag=best_text[1],
                best_question_recall=round(best_text[2], 3), best_answer_question=best_answer[0],
                best_answer_recall=round(best_answer[2], 3), questions_above_support=len(support),
                verbatim=verbatim[:3], flagged=flagged, reasons=reasons,
                mean_recall_by_tag=_mean_by_tag(text_scores),
            ))
    # v001 contains every basic bullet again. Keep one row per entry text, preferring the pitfalls version label
    # when the section is pitfalls and the first occurrence otherwise.
    seen = set()
    unique = []
    for row in per_entry:
        if row["text"] in seen:
            continue
        seen.add(row["text"])
        unique.append(row)
    return dict(entries=unique, documents=len(documents), counts=_tag_counts(documents),
                section_contrast=_section_contrast(unique), numeric_answer_leaks=_numeric_leaks(unique, documents))


def _numeric_leaks(entries: list[dict], documents: list[dict]) -> list[dict]:
    """Numbers that look like reported answers (3+ digits, or a decimal) shared with one interpretation."""
    leaks = []
    interpretations = {doc["id"]: doc["answer"] for doc in documents}
    for entry in entries:
        numbers = set(re.findall(r"\d{3,}|\d+\.\d+", entry["text"]))
        for number in numbers:
            owners = [qid for qid, text in interpretations.items() if number in text]
            if len(owners) == 1:
                leaks.append(dict(entry_id=entry["entry_id"], number=number, question_id=owners[0]))
    return leaks


def is_question_specific(*, best_recall: float, support_count: int, verbatim_hits: int) -> bool:
    """A bullet is question-specific when its distinctive words sit on one or two questions.

    A 5-gram shared with a single question does not flag the bullet when that bullet also matches many
    other questions: that is a definition, and one question happens to use the same words.
    """
    concentrated = best_recall >= CONCENTRATED_RECALL and support_count <= CONCENTRATED_SUPPORT
    verbatim_rare = verbatim_hits > 0 and support_count <= CONCENTRATED_SUPPORT
    return concentrated or verbatim_rare


def _mean_by_tag(scores: list[tuple]) -> dict:
    buckets: dict[str, list[float]] = defaultdict(list)
    for _qid, tag, score in scores:
        buckets[tag].append(score)
    return {tag: round(sum(values) / len(values), 4) for tag, values in buckets.items()}


def _tag_counts(documents: list[dict]) -> dict:
    counts: dict[str, int] = defaultdict(int)
    for doc in documents:
        counts[doc["tag"]] += 1
    return dict(counts)


def _section_contrast(entries: list[dict]) -> dict:
    out = {}
    for section in ("basic", "pitfalls"):
        rows = [e for e in entries if e["section"] == section]
        if not rows:
            continue
        screen = sum(e["mean_recall_by_tag"].get("screen1", 0) for e in rows) / len(rows)
        heldout = sum(e["mean_recall_by_tag"].get("heldout", 0) for e in rows) / len(rows)
        other = sum(e["mean_recall_by_tag"].get("other", 0) for e in rows) / len(rows)
        out[section] = dict(entries=len(rows), mean_recall_screen1=round(screen, 4),
                            mean_recall_heldout=round(heldout, 4), mean_recall_other=round(other, 4),
                            screen1_minus_heldout=round(screen - heldout, 4),
                            flagged=sum(1 for e in rows if e["flagged"]))
    return out


def _one_line(text: str, limit: int = 110) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    return flat if len(flat) <= limit else flat[:limit - 1] + "…"


def render_report(result: dict) -> str:
    contrast = result["section_contrast"]
    counts = result["counts"]
    lines = [
        "# Glossary contamination audit",
        "",
        "Compares each bullet of the basic glossary (`v000_basic`) and the screen-1 pitfalls block",
        "(`v001_screen1_pitfalls`) with the benchmark questions those texts can see: 33 hand-written,",
        "66 Opus-authored, and the 16 parametric family templates (both phrasings). Family instances are",
        "the same template, so they are not scored again. Regenerate with `python -m bench.glossary_contamination`.",
        "",
        "## Method",
        "",
        "Content words drop function words and numbers. Each question has an IDF from that corpus.",
        "An entry's recall on a question is the IDF-weighted share of its content words that the question also uses.",
        "The answer side is the gold SQL plus the authored interpretation (where one exists), scored with the same IDF.",
        f"A bullet is **question-specific** when its best-question recall is at least {CONCENTRATED_RECALL} and at most",
        f"{CONCENTRATED_SUPPORT} questions reach recall {SUPPORT_RECALL}, or when a {VERBATIM_N}-gram of its content words",
        f"occurs in exactly one question and that same support count is at most {CONCENTRATED_SUPPORT}. A {VERBATIM_N}-gram",
        "that also matches many other questions is a shared clause, listed below, and is not a flag. The cutoffs are",
        "deliberately high: a definition that shares \"denial\" and \"current adjudication\" with half the bench is",
        "general, which is what a glossary is for.",
        "",
        "Question tags: `screen1` is the 36-question screen (`research/screening_questions.json`), including a family",
        "template when that screen drew an instance of it. `heldout` is `heldout_hard_1` (27 questions authored after",
        "the pitfalls text, by authors who did not see it). Everything else is `other`.",
        "",
        f"Documents scored: {result['documents']} "
        f"(screen1 {counts.get('screen1', 0)}, heldout {counts.get('heldout', 0)}, other {counts.get('other', 0)}).",
        "",
        "## Screen-1 versus held-out overlap",
        "",
        "Mean, over bullets, of the mean per-question recall inside each tag. `screen1 − heldout` is the tuning gap:",
        "positive means the bullet's words show up more on the questions the text was written beside than on the",
        "later held-out set.",
        "",
        "| section | bullets | flagged | mean recall screen1 | mean recall heldout | mean recall other | screen1 − heldout |",
        "|---|---|---|---|---|---|---|",
    ]
    for section, row in contrast.items():
        lines.append(
            f"| {section} | {row['entries']} | {row['flagged']} | {row['mean_recall_screen1']:.3f} | "
            f"{row['mean_recall_heldout']:.3f} | {row['mean_recall_other']:.3f} | {row['screen1_minus_heldout']:+.3f} |")
    basic_gap = contrast.get("basic", {}).get("screen1_minus_heldout", 0)
    pitfall_gap = contrast.get("pitfalls", {}).get("screen1_minus_heldout", 0)
    lines += [
        "",
        f"The pitfalls block's screen−heldout gap is {pitfall_gap:+.3f}; the basic glossary's gap is {basic_gap:+.3f}.",
        _gap_sentence(basic_gap, pitfall_gap),
        "",
        "## Bullets",
        "",
        "| section | best question | tag | text recall | answer recall | questions ≥ support | flag | bullet |",
        "|---|---|---|---|---|---|---|",
    ]
    for entry in result["entries"]:
        flag = "yes" if entry["flagged"] else ""
        lines.append(
            f"| {entry['section']} | `{entry['best_question']}` | {entry['best_question_tag']} | "
            f"{entry['best_question_recall']:.2f} | {entry['best_answer_recall']:.2f} | "
            f"{entry['questions_above_support']} | {flag} | {_one_line(entry['text'], 90)} |")
    flagged = [e for e in result["entries"] if e["flagged"]]
    lines += ["", "## Question-specific flags", ""]
    if not flagged:
        lines.append("No bullet met the question-specific rule. The overlap above is spread across many questions,")
        lines.append("which is what a general definition looks like. That does not clear the block: the held-out")
        lines.append("run already showed the Haiku gain failed to replicate, and a general sentence can still be")
        lines.append("aimed at the traps in a particular set. The gap table is the quantitative check on that.")
    else:
        for entry in flagged:
            lines.append(f"- `{entry['entry_id']}` ({entry['section']}): {'; '.join(entry['reasons'])}.")
            lines.append(f"  {_one_line(entry['text'], 220)}")
    shared = [e for e in result["entries"] if e["verbatim"] and not e["flagged"]]
    lines += ["", "## Shared clauses that are not question-specific", ""]
    if not shared:
        lines.append("No bullet shares a 5-gram with exactly one question.")
    else:
        lines.append("These bullets share a 5-word content span with exactly one question and also match enough")
        lines.append("other questions that the bullet stays a general definition. The span is listed; it is not a flag.")
        lines.append("")
        for entry in shared:
            gram = entry["verbatim"][0]
            lines.append(
                f"- `{entry['entry_id']}` shares `{gram['gram']}` with `{gram['question_id']}`, "
                f"and {entry['questions_above_support']} questions reach recall {SUPPORT_RECALL}.")
    leaks = result.get("numeric_answer_leaks", [])
    lines += ["", "## Answer leakage", ""]
    if not leaks:
        lines.append("No bullet contains a number of three or more digits, or a decimal, that also appears in one")
        lines.append("question's interpretation. Those interpretations carry the expected counts and amounts; the")
        lines.append("glossary does not repeat them. Answer-side recall in the table is shared schema vocabulary in")
        lines.append("the gold SQL (`is_current_adjudication`, `payer_responsibility`), not a copied result.")
    else:
        lines.append(f"{len(leaks)} numeric overlap(s) between a bullet and an interpretation:")
        for leak in leaks:
            lines.append(f"- `{leak['entry_id']}` number `{leak['number']}` also appears in `{leak['question_id']}`.")
    lines += [
        "",
        "## What this does not show",
        "",
        "- Per-question agent errors. Those rows were not kept for screen 1 or the held-out run, so this audit",
        "  cannot line a bullet up with the exact item it would have fixed.",
        "- Whether a general bullet is *true*. Overlap is not correctness.",
        "- Round 3. Those questions are not in the repo; when they land, re-run this before they are used as",
        "  validation for a glossary that was fit on train.",
        "",
    ]
    return "\n".join(lines)


def _gap_sentence(basic_gap: float, pitfall_gap: float) -> str:
    if pitfall_gap > basic_gap + 0.02:
        return ("Pitfalls overlap the screen questions more than the basic glossary does, relative to the held-out set. "
                "That is the tuning-to-the-benchmark pattern the held-out run was built to catch.")
    if pitfall_gap > 0.02 and basic_gap > 0.02:
        return ("Both sections overlap screen-1 more than the held-out set. The basic definitions were also written "
                "with this bench in view, so the gap is not unique to the pitfalls block.")
    if abs(pitfall_gap) <= 0.02 and abs(basic_gap) <= 0.02:
        return ("Neither section prefers the screen questions over the held-out set by more than 0.02 recall. "
                "On this word-overlap measure the pitfalls block is not more screen-specific than the basic glossary. "
                "The Haiku gain can still have failed because the traps named in the block are not the traps in the "
                "held-out questions, which is a coverage miss rather than a copied question.")
    if pitfall_gap <= 0.02 and pitfall_gap <= basic_gap + 0.02:
        return ("The pitfalls block does not overlap the screen questions more than the held-out set, and it is not "
                "more screen-specific than the basic glossary. Word overlap does not explain the missing Haiku gain: "
                "the held-out questions use this vocabulary and Haiku's accuracy still did not move. That is a coverage "
                "miss (the block names traps those questions do not turn on), not a copied question.")
    return "See the table for the two gaps."


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=REPORT_PATH)
    args = parser.parse_args()
    result = score_glossary()
    text = render_report(result)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text)
    flagged = sum(1 for e in result["entries"] if e["flagged"])
    print(json.dumps(dict(entries=len(result["entries"]), flagged=flagged, contrast=result["section_contrast"]), indent=1))


if __name__ == "__main__":
    main()
