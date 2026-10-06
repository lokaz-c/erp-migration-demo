from collections import Counter

import pytest

from erp_migration.matching.calibrate import calibrate
from erp_migration.matching.suppliers import (
    AUTO_MERGE_THRESHOLD,
    REVIEW_THRESHOLD,
    identifying_part,
    match_suppliers,
    normalize,
    pair_scores,
    similarity,
)


@pytest.mark.parametrize(
    "spelling",
    [
        "Fowler & Sons Textiles Ltd",
        "FOWLER AND SONS TEXTILES LIMITED",
        "fowler & sons textiles",
        "Fowler and Sons Textiles, Ltd.",
        "Fowler & Sons Tex Ltd",
        "Fowler & Sons Textiles S.A.R.L.",
        "Fowler & Sons Textiles Lmiited",
    ],
)
def test_normalization_rules_reach_one_key(spelling):
    assert normalize(spelling) == "fowler sons textiles"


def test_glued_trade_word_is_split_but_surnames_ending_in_son_are_not():
    assert normalize("JohnsonTextiles Ltd") == "johnson textiles"
    assert normalize("Henderson Freight Services") == "henderson freight services"
    assert normalize("Richardson Trims") == "richardson trims"


def test_identifying_part_drops_trade_words_and_their_abbreviations():
    assert identifying_part("rose cloth merchants") == "rose"
    assert identifying_part("snyder sons txtls") == "snyder"
    assert identifying_part("gallagher fabric ho") == "gallagher"
    assert identifying_part("textiles") == "textiles"  # nothing left: keep the whole key


def test_similarity_needs_the_identifying_part_to_match():
    # Long shared trade words alone must not produce a match ...
    assert similarity("rose cloth merchants", "rhodes cloth merchants") < AUTO_MERGE_THRESHOLD
    # ... and neither must a shared surname with a different trade.
    assert similarity("fowler sons textiles", "fowler sons haberdashery") < AUTO_MERGE_THRESHOLD
    # A typo in a longer name still matches.
    assert similarity("henderson fabric house", "hendreson fabric house") >= AUTO_MERGE_THRESHOLD


def test_match_suppliers_clusters_spellings():
    counts = Counter(
        {
            "Henderson Textiles Co. Ltd": 40,
            "HENDERSON TEXTILES CO. LTD": 6,
            "Henderson Textiles Company Limited": 3,
            "Hendreson Textiles Ltd": 2,
            "Henderson Print and Pack Ltd": 30,
            "Henderson Print & Pack": 5,
        }
    )
    result = match_suppliers(counts)
    clusters = {a: m.cluster for a, m in result.matches.items()}
    textiles = clusters["Henderson Textiles Co. Ltd"]
    assert clusters["HENDERSON TEXTILES CO. LTD"] == textiles
    assert clusters["Henderson Textiles Company Limited"] == textiles
    assert clusters["Henderson Print & Pack"] == clusters["Henderson Print and Pack Ltd"]
    assert clusters["Henderson Print & Pack"] != textiles
    assert result.cluster_of("Hendreson Textiles Ltd").canonical == "Henderson Textiles Co. Ltd"
    assert result.matches["Henderson Textiles Co. Ltd"].method == "exact"
    assert result.matches["HENDERSON TEXTILES CO. LTD"].method == "normalized"
    assert result.matches["Hendreson Textiles Ltd"].method == "fuzzy"


def test_scores_between_thresholds_go_to_review_not_merge():
    counts = Counter({"Doyle Cloth Merchants Ltd": 20, "Doyla Cloth Merchants Ltd": 15})
    result = match_suppliers(counts)
    assert len(result.clusters) == 2
    assert [c.alias for c in result.review] == ["Doyla Cloth Merchants Ltd"]


def test_pair_scores():
    predicted = {"a": 1, "b": 1, "c": 1, "d": 2}
    truth = {"a": "X", "b": "X", "c": "Y", "d": "Y"}
    s = pair_scores(predicted, truth)
    assert (s.true_pairs, s.predicted_pairs, s.correct_pairs) == (2, 3, 1)
    assert s.precision == pytest.approx(1 / 3)
    assert s.recall == pytest.approx(1 / 2)


def test_configured_thresholds_are_the_calibrated_ones():
    """The constants in the matcher must be what the documented selection rule picks."""
    c = calibrate()
    assert (c.auto_threshold, c.review_threshold) == (AUTO_MERGE_THRESHOLD, REVIEW_THRESHOLD)
    chosen = c.row(c.auto_threshold).scores
    assert chosen.precision >= 0.99
