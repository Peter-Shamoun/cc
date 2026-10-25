"""Contamination flags: question-specific bullets versus shared definitions."""
from bench.glossary_contamination import REPORT_PATH, is_question_specific, render_report, score_glossary


def test_flag_rule_ignores_a_definition_shared_by_many_questions():
    assert is_question_specific(best_recall=0.45, support_count=2, verbatim_hits=0) is True
    assert is_question_specific(best_recall=0.45, support_count=3, verbatim_hits=0) is False
    assert is_question_specific(best_recall=0.10, support_count=1, verbatim_hits=1) is True
    assert is_question_specific(best_recall=0.90, support_count=70, verbatim_hits=3) is False
    assert is_question_specific(best_recall=0.44, support_count=1, verbatim_hits=0) is False


def test_screen1_glossaries_are_not_question_specific():
    result = score_glossary()
    contrast = result["section_contrast"]
    assert contrast["basic"]["flagged"] == 0
    assert contrast["pitfalls"]["flagged"] == 0
    assert contrast["pitfalls"]["screen1_minus_heldout"] <= contrast["basic"]["screen1_minus_heldout"] + 0.02
    assert contrast["pitfalls"]["screen1_minus_heldout"] < 0
    assert result["numeric_answer_leaks"] == []
    assert render_report(result) == REPORT_PATH.read_text()
