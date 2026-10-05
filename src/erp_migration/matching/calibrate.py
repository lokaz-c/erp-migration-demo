"""Choose the supplier-matching thresholds on held-out synthetic data.

The demo run uses seed 42. Calibration builds supplier spellings the same way
the book generator does, but from five other seeds, and sweeps the auto-merge
threshold from 70 to 100. Tuning on the evaluation seed itself would make the
reported precision and recall look better than they are.

Selection rule (a business choice, not a statistical one):

* AUTO_MERGE_THRESHOLD: the threshold with the highest recall among those
  with pooled precision of at least 0.99. A wrong merge folds two suppliers'
  payables history together and is hard to notice afterwards; a missed merge
  leaves a duplicate supplier that someone can merge later in the ERP. So
  precision is the constraint and recall is what is maximised.
* REVIEW_THRESHOLD: with the auto threshold fixed, suggested matches are
  grouped into 5-point score bands. Going down from the auto threshold, the
  review floor is the last band in which at least half of the suggestions are
  right. Below it a suggestion is more often wrong than right and not worth
  a reviewer's time.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass

from faker import Faker

from erp_migration.generate.aliases import make_alias_pool, pick_spelling
from erp_migration.generate.catalog import make_suppliers
from erp_migration.matching.suppliers import PairScores, match_suppliers, pair_scores

CALIBRATION_SEEDS = (101, 202, 303, 404, 505)
THRESHOLDS = tuple(range(70, 101))
MIN_PRECISION_AUTO = 0.99
MIN_REVIEW_HIT_RATE = 0.5
REVIEW_BAND = 5
REVIEW_SEARCH_FLOOR = 50


@dataclass(frozen=True)
class SweepRow:
    threshold: int
    scores: PairScores


@dataclass(frozen=True)
class ReviewBand:
    low: int  # scores in [low, low + REVIEW_BAND)
    suggestions: int
    correct: int


@dataclass(frozen=True)
class Calibration:
    rows: tuple[SweepRow, ...]
    auto_threshold: int
    review_threshold: int
    review_bands: tuple[ReviewBand, ...]
    seeds: tuple[int, ...]

    def row(self, threshold: int) -> SweepRow:
        return next(r for r in self.rows if r.threshold == threshold)


def alias_universe(seed: int) -> tuple[Counter[str], dict[str, str]]:
    """Spellings with row counts, plus the true supplier of each spelling."""
    rng = random.Random(f"{seed}:calibration")
    fake = Faker("en_US")
    fake.seed_instance(seed)
    counts: Counter[str] = Counter()
    truth: dict[str, str] = {}
    for s in make_suppliers(rng, fake):
        pool = [
            a
            for a in make_alias_pool(rng, s.name, rng.randint(3, 7))
            if a not in truth and a.upper() not in truth
        ]
        for _ in range(rng.randint(20, 120)):
            spelling = pick_spelling(rng, pool)
            if rng.random() < 0.1:  # some years' books were kept in capitals
                spelling = spelling.upper()
            if truth.setdefault(spelling, s.code) == s.code:
                counts[spelling] += 1
    return counts, truth


def calibrate(seeds: tuple[int, ...] = CALIBRATION_SEEDS) -> Calibration:
    universes = [alias_universe(seed) for seed in seeds]
    rows = []
    for t in THRESHOLDS:
        total = PairScores(0, 0, 0)
        for counts, truth in universes:
            result = match_suppliers(counts, auto=t, review=t)
            predicted = {a: m.cluster for a, m in result.matches.items()}
            total = total + pair_scores(predicted, truth)
        rows.append(SweepRow(t, total))
    eligible = [r for r in rows if r.scores.precision >= MIN_PRECISION_AUTO]
    auto = max(eligible, key=lambda r: (r.scores.recall, r.threshold)).threshold

    suggestions: list[tuple[float, bool]] = []
    for counts, truth in universes:
        result = match_suppliers(counts, auto=auto, review=REVIEW_SEARCH_FLOOR)
        for c in result.review:
            nearest = result.clusters[c.nearest_cluster].canonical
            suggestions.append((c.score, truth[c.alias] == truth[nearest]))
    bands = []
    for low in range(auto - REVIEW_BAND, REVIEW_SEARCH_FLOOR - 1, -REVIEW_BAND):
        hits = [ok for score, ok in suggestions if low <= score < low + REVIEW_BAND]
        bands.append(ReviewBand(low, len(hits), sum(hits)))
    review = auto
    for band in bands:
        if not band.suggestions or band.correct / band.suggestions < MIN_REVIEW_HIT_RATE:
            break
        review = band.low
    return Calibration(tuple(rows), auto, review, tuple(bands), seeds)
