"""Reconcile supplier spellings into canonical suppliers.

Two stages:

1. Normalization rules (deterministic). Strip accents, case-fold, "&" ->
   "and", drop punctuation, join dotted initials ("S.A.R.L." -> "sarl"),
   expand standard business abbreviations ("Intl" -> "international"), split
   a trade word glued onto the name ("JohnsonTextiles"), and drop legal forms
   ("Ltd", "Limited", "Co.", "SARL", including misspelt ones) and "and".
   Spellings with the same key are the same supplier.
2. Fuzzy matching (rapidfuzz) on the keys that are left. Two keys are
   compared twice: as whole names, and on their identifying part only (the
   key minus generic trade words such as "textiles", "cloth merchants",
   "& sons"). The score is the lower of the two, so "Rose Cloth Merchants"
   and "Rhodes Cloth Merchants" do not match just because they share two
   long generic words, and "Fowler Trims" does not match "Fowler Textiles"
   just because they share a surname. A key joins an existing supplier when
   its score against that supplier's anchor key is at least
   AUTO_MERGE_THRESHOLD. Keys scoring between REVIEW_THRESHOLD and
   AUTO_MERGE_THRESHOLD stay separate and are listed for a person to confirm.

Keys are processed from most to least frequent, so the spelling clerks used
most often anchors each supplier, and every comparison is against that anchor
rather than against the newest member (which avoids chains like A~B, B~C, so
A~C).

Both thresholds are chosen by `erp_migration.matching.calibrate` on synthetic
data generated with different seeds from the demo run; see docs/approach.md.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache
from math import comb

from rapidfuzz import fuzz

AUTO_MERGE_THRESHOLD = 88.0
REVIEW_THRESHOLD = 73.0

LEGAL_FORMS = {
    "ltd",
    "limited",
    "co",
    "company",
    "sarl",
    "plc",
    "inc",
    "llc",
    "corp",
    "corporation",
}
LONG_LEGAL_FORMS = ("limited", "company", "corporation")  # matched fuzzily ("Lmiited")
ABBREVIATIONS = {
    "intl": "international",
    "mfg": "manufacturing",
    "bros": "brothers",
    "svcs": "services",
    "svc": "services",
    "eng": "engineering",
    "engg": "engineering",
    "pkg": "packaging",
    "acc": "accessories",
    "accs": "accessories",
    "tex": "textiles",
}
# Generic words in this industry's supplier names. They say what a supplier
# sells, not who it is.
TRADE_WORDS = (
    "textiles",
    "fabrics",
    "fabric",
    "cloth",
    "merchants",
    "house",
    "trims",
    "accessories",
    "haberdashery",
    "packaging",
    "print",
    "pack",
    "logistics",
    "freight",
    "services",
    "engineering",
    "international",
    "sons",
    "brothers",
    "trading",
    "general",
    "supplies",
    "enterprises",
    "industries",
    "mills",
)


def _join_initials(tokens: list[str]) -> list[str]:
    out: list[str] = []
    run = ""
    for t in tokens:  # "s a r l" -> "sarl"
        if len(t) == 1 and t.isalpha():
            run += t
            continue
        if run:
            out.append(run)
            run = ""
        out.append(t)
    if run:
        out.append(run)
    return out


def _split_glued(token: str) -> list[str]:
    """'johnsontextiles' -> ['johnson', 'textiles']; leaves 'henderson' alone."""
    if len(token) < 8:
        return [token]
    best_score, best_at = 0.0, 0
    for i in range(3, len(token) - 3):
        suffix = token[i:]
        if suffix in ("sons", "son"):
            continue
        score = max(fuzz.ratio(suffix, w) for w in TRADE_WORDS if len(w) >= 4)
        if score > best_score:
            best_score, best_at = score, i
    if best_score >= 85:  # split where the suffix looks most like a trade word
        return [token[:best_at], token[best_at:]]
    return [token]


def _is_legal_form(token: str) -> bool:
    if token in LEGAL_FORMS:
        return True
    return len(token) >= 5 and any(fuzz.ratio(token, w) >= 85 for w in LONG_LEGAL_FORMS)


@cache
def normalize(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.casefold().replace("&", " and ").replace("'", "")
    tokens = _join_initials(re.sub(r"[^a-z0-9]+", " ", s).split())
    tokens = [ABBREVIATIONS.get(t, t) for t in tokens]
    tokens = [part for t in tokens for part in _split_glued(t)]
    return " ".join(t for t in tokens if t != "and" and not _is_legal_form(t))


def _is_subsequence(short: str, word: str) -> bool:
    it = iter(word)
    return all(ch in it for ch in short)


def _is_trade_word(token: str, last: bool) -> bool:
    for w in TRADE_WORDS:
        if token == w:
            return True
        if len(token) >= 3 and token[0] == w[0] and _is_subsequence(token, w):
            return True  # abbreviation: "txtls", "serv", "fabs"
        if last and len(token) >= 2 and w.startswith(token):
            return True  # cut off at the end of the cell: "Gallagher Fabric Ho"
        if len(token) >= 5 and fuzz.ratio(token, w) >= 80:
            return True  # misspelt: "cltoh"
    return False


@cache
def identifying_part(key: str) -> str:
    """The key without generic trade words; the whole key if nothing else is left."""
    tokens = key.split()
    kept = [t for i, t in enumerate(tokens) if not _is_trade_word(t, i == len(tokens) - 1)]
    return " ".join(kept) if kept else key


def _name_score(a: str, b: str) -> float:
    return max(fuzz.ratio(a, b), fuzz.token_sort_ratio(a, b))


def similarity(a: str, b: str) -> float:
    """Lower of whole-name and identifying-part similarity, 0-100."""
    return min(_name_score(a, b), _name_score(identifying_part(a), identifying_part(b)))


@dataclass
class AliasMatch:
    alias: str
    key: str
    cluster: int
    method: str  # exact | normalized | fuzzy
    score: float  # similarity of the key to the cluster's anchor key


@dataclass
class ReviewCandidate:
    alias: str
    nearest_cluster: int
    score: float


@dataclass
class Cluster:
    cluster_id: int
    anchor_key: str
    keys: list[str] = field(default_factory=list)
    canonical: str = ""  # most frequent raw spelling


@dataclass
class MatchResult:
    matches: dict[str, AliasMatch]
    clusters: list[Cluster]
    review: list[ReviewCandidate]

    def cluster_of(self, alias: str) -> Cluster:
        return self.clusters[self.matches[alias].cluster]


def match_suppliers(
    counts: Mapping[str, int], auto: float = AUTO_MERGE_THRESHOLD, review: float = REVIEW_THRESHOLD
) -> MatchResult:
    """Cluster raw spellings. `counts` maps each spelling to how many rows use it."""
    by_key: dict[str, list[str]] = {}
    key_rows: Counter[str] = Counter()
    for alias, n in counts.items():
        key = normalize(alias)
        by_key.setdefault(key, []).append(alias)
        key_rows[key] += n

    clusters: list[Cluster] = []
    key_cluster: dict[str, tuple[int, float]] = {}
    review_keys: list[tuple[str, int, float]] = []
    for key in sorted(key_rows, key=lambda k: (-key_rows[k], k)):
        best_id, best = -1, -1.0
        for c in clusters:
            s = similarity(key, c.anchor_key)
            if s > best:
                best_id, best = c.cluster_id, s
        if best >= auto:
            clusters[best_id].keys.append(key)
            key_cluster[key] = (best_id, best)
            continue
        c = Cluster(len(clusters), key, [key])
        clusters.append(c)
        key_cluster[key] = (c.cluster_id, 100.0)
        if best >= review:
            review_keys.append((key, best_id, best))

    matches: dict[str, AliasMatch] = {}
    for c in clusters:
        spellings = [a for k in c.keys for a in by_key[k]]
        c.canonical = min(spellings, key=lambda a: (-counts[a], a))
        for a in spellings:
            cid, score = key_cluster[normalize(a)]
            if a == c.canonical:
                method = "exact"
            elif normalize(a) == normalize(c.canonical):
                method = "normalized"
            else:
                method = "fuzzy"
            matches[a] = AliasMatch(a, normalize(a), cid, method, round(score, 2))

    candidates = [
        ReviewCandidate(alias, cid, round(score, 2))
        for key, cid, score in review_keys
        for alias in sorted(by_key[key])
    ]
    return MatchResult(matches, clusters, candidates)


@dataclass(frozen=True)
class PairScores:
    true_pairs: int
    predicted_pairs: int
    correct_pairs: int

    @property
    def precision(self) -> float:
        return self.correct_pairs / self.predicted_pairs if self.predicted_pairs else 1.0

    @property
    def recall(self) -> float:
        return self.correct_pairs / self.true_pairs if self.true_pairs else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    def __add__(self, other: PairScores) -> PairScores:
        return PairScores(
            self.true_pairs + other.true_pairs,
            self.predicted_pairs + other.predicted_pairs,
            self.correct_pairs + other.correct_pairs,
        )


def pair_scores(predicted: Mapping[str, object], truth: Mapping[str, object]) -> PairScores:
    """Pairwise precision/recall over every pair of distinct spellings.

    A pair is predicted-same when both spellings landed in one cluster, and
    truly-same when the ground truth gives them the same supplier.
    """
    aliases = [a for a in predicted if a in truth]
    pred = Counter(predicted[a] for a in aliases)
    true = Counter(truth[a] for a in aliases)
    both = Counter((predicted[a], truth[a]) for a in aliases)
    return PairScores(
        true_pairs=sum(comb(n, 2) for n in true.values()),
        predicted_pairs=sum(comb(n, 2) for n in pred.values()),
        correct_pairs=sum(comb(n, 2) for n in both.values()),
    )
