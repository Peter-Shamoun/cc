"""Train / validation / test assignment: grouping, the freeze, and the sealed test split."""
import json

import pytest

from bench.splits import (
    ASSIGNMENT_PATH, SealedSplitError, _assignment_hash, SplitConflict, SplitError, add_questions, assign_group_splits,
    group_records, initial_assignment, iter_assignment, load_assignment, select_cases, sync,
    template_signature, verify, write_assignment,
)


def _rec(qid, text, **extra):
    row = dict(id=qid, question=text, origin="handwritten", author_round="handwritten", family="",
               gold_sql="SELECT 1", answer_key_sql="SELECT 1", template="")
    row.update(extra)
    return row


def _distinct(i):
    """Letter-only wording. Digits are masked out of a template signature, so a digit must not be the only difference."""
    def token(n):
        chars = []
        while True:
            chars.append(chr(ord("a") + n % 26))
            n //= 26
            if n == 0:
                break
        return "tok" + "".join(reversed(chars)) + "zz"

    return " ".join(token(i * 20 + k) for k in range(10))


def test_template_signature_masks_month_and_year():
    july = "What was the denial rate in July 2024 for submitted claims?"
    august = "What was the denial rate in August 2025 for submitted claims?"
    assert template_signature(july) == template_signature(august)


def test_family_template_and_signature_stay_in_one_group():
    july = "What was the denial rate in July 2024 for submitted claims?"
    august = "What was the denial rate in August 2025 for submitted claims?"
    records = [
        _rec("july_q", july, author_round="opus_1"),
        _rec("august_q", august, author_round="handwritten"),
        _rec("fam_a", "wording one about primary denial counts", family="payer_denial"),
        _rec("fam_b", "entirely separate sentence regarding payment totals", family="payer_denial"),
        _rec("tmpl_a", "alpha bravo unique wording about widgets", template="weekly_volume"),
        _rec("tmpl_b", "zebra yak xylophone unrelated sentence about quotas", template="weekly_volume"),
    ]
    groups = group_records(records, bundles={})
    assert groups["july_q"] == groups["august_q"]
    assert groups["fam_a"] == groups["fam_b"]
    assert groups["tmpl_a"] == groups["tmpl_b"]
    assert len({groups["july_q"], groups["fam_a"], groups["tmpl_a"]}) == 3


def test_singleton_catalog_hits_every_split_and_is_deterministic():
    records = [_rec(f"q{i:02d}", _distinct(i)) for i in range(12)]
    first = initial_assignment(records, bundles={})
    second = initial_assignment(records, bundles={})
    assert first["assignment_hash"] == second["assignment_hash"]
    assert [row["split"] for row in first["questions"]] == [row["split"] for row in second["questions"]]
    counts = first["summary"]["by_split"]
    assert counts == {"train": 6, "validation": 3, "test": 3}


def test_add_unlinked_question_goes_to_validation_without_moving_frozen_rows(tmp_path):
    records = [_rec(f"q{i:02d}", _distinct(i)) for i in range(12)]
    path = tmp_path / "splits.json"
    write_assignment(path, records, bundles={})
    before = {row["id"]: row["split"] for row in load_assignment(path)["questions"]}
    added = _rec("round3_new", _distinct(99), author_round="round3", origin="added")
    doc = add_questions([added], path, destination="validation", bundles={}, catalog=records)
    after = {row["id"]: row["split"] for row in doc["questions"]}
    for qid, split in before.items():
        assert after[qid] == split
    row = next(item for item in doc["questions"] if item["id"] == "round3_new")
    assert row["split"] == "validation" and row["added"] and row["inherited_split"] is False
    assert row["question"] == added["question"]
    verify(path, records=records, bundles={})
    with pytest.raises(SplitError, match="frozen"):
        write_assignment(path, records, bundles={})


def test_same_family_inherits_the_frozen_split(tmp_path):
    records = [_rec(f"q{i:02d}", _distinct(i)) for i in range(12)]
    records.append(_rec("fam_anchor", _distinct(50), family="round3fam"))
    path = tmp_path / "splits.json"
    write_assignment(path, records, bundles={})
    anchor_split = next(row["split"] for row in load_assignment(path)["questions"] if row["id"] == "fam_anchor")
    added = _rec("fam_later", _distinct(77), family="round3fam", author_round="round3", origin="added")
    doc = add_questions([added], path, destination="train", bundles={}, catalog=records)
    row = next(item for item in doc["questions"] if item["id"] == "fam_later")
    assert row["split"] == anchor_split and row["inherited_split"] is True


def test_later_batch_links_through_stored_text(tmp_path):
    records = [_rec(f"q{i:02d}", _distinct(i)) for i in range(12)]
    path = tmp_path / "splits.json"
    write_assignment(path, records, bundles={})
    first = _rec("round3_a", _distinct(80), author_round="round3", origin="added")
    add_questions([first], path, destination="validation", bundles={}, catalog=records)
    second = _rec("round3_b", first["question"], author_round="round3", origin="added")
    doc = add_questions([second], path, destination="train", bundles={}, catalog=records)
    row = next(item for item in doc["questions"] if item["id"] == "round3_b")
    assert row["split"] == "validation" and row["inherited_split"] is True


def test_bridge_across_two_frozen_splits_raises(tmp_path):
    records = [_rec(f"q{i:02d}", _distinct(i)) for i in range(12)]
    records[0]["family"] = "bridgefam"
    path = tmp_path / "splits.json"
    doc = write_assignment(path, records, bundles={})
    splits = {row["id"]: row["split"] for row in doc["questions"]}
    other = next(qid for qid, split in splits.items() if split != splits["q00"])
    other_text = next(row["question"] for row in records if row["id"] == other)
    bridge = _rec("bridge", other_text, family="bridgefam", author_round="round3", origin="added")
    with pytest.raises(SplitConflict):
        add_questions([bridge], path, bundles={}, catalog=records)


def test_verify_rejects_a_changed_question_or_a_tampered_hash(tmp_path):
    records = [_rec(f"q{i:02d}", _distinct(i)) for i in range(12)]
    path = tmp_path / "splits.json"
    write_assignment(path, records, bundles={})
    changed = [dict(row) for row in records]
    changed[1] = dict(changed[1], question=changed[1]["question"] + " extra unique phrase")
    with pytest.raises(SplitError, match="content hash"):
        verify(path, records=changed, bundles={})
    doc = json.loads(path.read_text())
    doc["assignment_hash"] = "0" * 64
    path.write_text(json.dumps(doc))
    with pytest.raises(SplitError, match="assignment_hash"):
        verify(path, records=records, bundles={})


def test_test_split_is_sealed_and_family_instances_inherit(tmp_path):
    records = [_rec(f"q{i:02d}", _distinct(i)) for i in range(8)]
    records.append(_rec("family:payer_x", "family template phrasing one\nphrasing two",
                        origin="parametric_family", author_round="parametric", family="payer_x"))
    path = tmp_path / "splits.json"
    write_assignment(path, records, bundles={})
    fam_split = next(row["split"] for row in load_assignment(path)["questions"] if row["id"] == "family:payer_x")
    case = {"id": "f_payer_x__july", "family": "payer_x"}
    kept = select_cases([case], fam_split, unseal_test=(fam_split == "test"), path=path)
    assert kept == [case]
    with pytest.raises(SealedSplitError):
        iter_assignment("test", path=path)
    with pytest.raises(SealedSplitError):
        select_cases([case], "test", path=path)
    assert iter_assignment("test", unseal_test=True, path=path)
    with pytest.raises(SplitError, match="no frozen split"):
        select_cases([{"id": "missing"}], "train", path=path)


def test_sync_sends_catalog_questions_missing_from_the_file_to_validation(tmp_path, monkeypatch):
    records = [_rec(f"q{i:02d}", _distinct(i)) for i in range(12)]
    path = tmp_path / "splits.json"
    monkeypatch.setattr("bench.splits.TEMPLATE_BUNDLES", {})
    write_assignment(path, records[:8])
    before = {row["id"]: row["split"] for row in load_assignment(path)["questions"]}
    monkeypatch.setattr("bench.splits.load_catalog", lambda: records)
    doc = sync(path)
    added = [row for row in doc["questions"] if row.get("added")]
    assert {row["id"] for row in added} == {f"q{i:02d}" for i in range(8, 12)}
    assert {row["split"] for row in added} == {"validation"}
    assert {row["id"]: row["split"] for row in doc["questions"] if row["id"] in before} == before


def test_committed_assignment_matches_the_v1_freeze():
    # Later rounds are added on top; the v1 rows themselves must never move.
    doc = verify()
    frozen = [row for row in doc["questions"] if not row.get("added")]
    assert _assignment_hash(frozen) == "7f7600eaaaaafe6ee5fdd6c0c63644fd12886973ae6152dec15be2746e4de10d"
    concrete = [row for row in frozen if row["origin"] != "parametric_family"]
    assert len(concrete) == 99
    assert len({row["group_id"] for row in frozen}) == 92
    assert {split: sum(row["split"] == split for row in concrete) for split in ("train", "validation", "test")} == (
        {"train": 49, "validation": 25, "test": 25})
    assert all(row.get("added") for row in doc["questions"] if row not in frozen)


def test_targets_must_cover_the_three_splits():
    with pytest.raises(SplitError):
        assign_group_splits([dict(group_id="g:a", stratum="r", size=1)], targets={"train": 1.0})
