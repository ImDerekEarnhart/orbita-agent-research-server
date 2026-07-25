"""Find places where someone appears to have changed their mind, and refuse to judge.

The valuable thing in a personal archive is not that it can be searched. It is that it
contains your own positions at many points in time, and you cannot remember most of
them. Surfacing "in March you said this, in June you said the opposite" is the thing
that makes an archive feel like memory rather than storage.

The temptation is to call that a contradiction. This module does not, and the reason is
the same failure this codebase already has on record: run_c6b010982fd04cf0 emitted six
refutations from a scorer that could not evaluate its candidates. Deciding that two
sentences genuinely conflict requires understanding what each asserts, and nothing here
understands anything. What it can do honestly is much narrower:

  * find a later message carrying an explicit reversal marker ("was wrong", "changed my
    mind", "actually", "no longer"), which is the author signalling a change themselves
  * find earlier messages about the same subject, by term overlap
  * hand back both, with both dates and both receipts, and say plainly that a human has
    to decide whether they actually conflict

So the output is `candidate_reversal`, never `contradiction`. Nothing here writes to the
belief graph. Promoting a candidate into a recorded contradiction is a separate,
deliberate, human act, exactly as plan v3's unscorable_boundary requires.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Phrases in which an author marks their own change of position. Detecting these is not
# understanding the sentence; it is noticing a signal the writer put there deliberately.
REVERSAL_MARKERS: tuple[tuple[str, str], ...] = (
    (r"\b(i )?was wrong\b", "explicit self-correction"),
    (r"\bchanged my mind\b", "explicit self-correction"),
    (r"\bno longer (think|believe|use|need)\b", "explicit self-correction"),
    (r"\bturns out\b", "revised by evidence"),
    (r"\bactually,?\s", "revision marker"),
    (r"\bscratch that\b", "revision marker"),
    (r"\bthe wrong (call|choice|approach|idea)\b", "explicit self-correction"),
    (r"\bshould have\b", "retrospective regret"),
    (r"\bin hindsight\b", "retrospective regret"),
    (r"\bcorrection[:,]", "revision marker"),
    (r"\binstead of\b", "replacement marker"),
    (r"\bwe don'?t need\b", "reversal of requirement"),
    (r"\bnever mind\b", "revision marker"),
)

STOPWORDS = frozenset(
    """
    a about above after again against all am an and any are aren as at be because been
    before being below between both but by can cannot could couldn did didn do does
    doesn doing don down during each few for from further had hadn has hasn have haven
    having he her here hers herself him himself his how i if in into is isn it its itself
    just let me more most mustn my myself no nor not now of off on once only or other
    ought our ours ourselves out over own same shan she should shouldn so some such than
    that the their theirs them themselves then there these they this those through to too
    under until up very was wasn we were weren what when where which while who whom why
    with won would wouldn you your yours yourself yourselves think thing things really
    actually maybe probably going get got make made use used using need needs one two
    also still even much many lot bit way ways going want wanted
    """.split()
)

MIN_TERM_LENGTH = 3
MIN_SHARED_TERMS = 2
DEFAULT_MIN_DAYS_APART = 1


def salient_terms(text: str) -> set[str]:
    """Content words, which stand in for what a message is about."""
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    return {
        word
        for word in words
        if len(word) >= MIN_TERM_LENGTH and word not in STOPWORDS and not word.isdigit()
    }


def reversal_markers(text: str) -> list[str]:
    lowered = (text or "").lower()
    return sorted({label for pattern, label in REVERSAL_MARKERS if re.search(pattern, lowered)})


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class CandidateReversal:
    earlier: dict[str, Any]
    later: dict[str, Any]
    shared_terms: list[str]
    markers: list[str]
    overlap: float
    days_apart: float | None
    adjudication: str = field(
        default=(
            "Not adjudicated. Orbita found a later message in which you marked a change of "
            "position, and an earlier message about the same subject. It has not determined "
            "that these conflict, that either is true, or which one you hold now. Only you "
            "can decide that."
        )
    )

    def public(self) -> dict[str, Any]:
        return {
            "status": "candidate_reversal",
            "shared_terms": self.shared_terms,
            "markers": self.markers,
            "overlap": round(self.overlap, 4),
            "days_apart": round(self.days_apart, 2) if self.days_apart is not None else None,
            "earlier": self.earlier,
            "later": self.later,
            "adjudication": self.adjudication,
        }


def _row_view(row: Any) -> dict[str, Any]:
    return {
        "text": row["text"],
        "role": row["role"],
        "created_at": row["created_at"],
        "receipt": {
            "conversation_id": row["conversation_id"],
            "conversation_title": row["conversation_title"],
            "node_id": row["node_id"],
            "case_id": row["case_id"],
            "file_id": row["file_id"],
            "source_name": row["source_name"],
        },
    }


def find_candidate_reversals(
    index: Any,
    *,
    case_id: str | None = None,
    role: str = "user",
    limit: int = 20,
    min_days_apart: float = DEFAULT_MIN_DAYS_APART,
    min_shared_terms: int = MIN_SHARED_TERMS,
) -> dict[str, Any]:
    """Surface earlier/later message pairs that may represent a change of position.

    Only messages with a timestamp participate. A pair whose ordering cannot be
    established is not evidence of anything, so it is excluded rather than guessed at.
    """
    limit = max(1, min(int(limit), 100))

    where = ["create_time IS NOT NULL"]
    params: list[Any] = []
    if role:
        where.append("role = ?")
        params.append(role)
    if case_id:
        where.append("case_id = ?")
        params.append(case_id)

    rows = index.conn.execute(
        f"SELECT * FROM memory_messages WHERE {' AND '.join(where)} ORDER BY create_time",
        params,
    ).fetchall()

    scanned = len(rows)
    prepared = [(row, salient_terms(row["text"]), reversal_markers(row["text"])) for row in rows]

    candidates: list[CandidateReversal] = []
    for later_index, (later_row, later_terms, markers) in enumerate(prepared):
        if not markers or not later_terms:
            continue
        for earlier_row, earlier_terms, _ in prepared[:later_index]:
            shared = earlier_terms & later_terms
            if len(shared) < min_shared_terms:
                continue
            seconds = (later_row["create_time"] or 0) - (earlier_row["create_time"] or 0)
            days = seconds / 86_400
            if days < min_days_apart:
                continue
            candidates.append(
                CandidateReversal(
                    earlier=_row_view(earlier_row),
                    later=_row_view(later_row),
                    shared_terms=sorted(shared),
                    markers=markers,
                    overlap=_overlap(earlier_terms, later_terms),
                    days_apart=days,
                )
            )

    # Strongest topical agreement first; a pair sharing more of what it is about is more
    # likely to be the same question revisited rather than an incidental word match.
    candidates.sort(key=lambda c: (c.overlap, c.days_apart or 0), reverse=True)

    return {
        "candidate_count": len(candidates),
        "candidates": [candidate.public() for candidate in candidates[:limit]],
        "messages_scanned": scanned,
        "method": (
            "A later message containing an explicit change-of-position marker was paired "
            "with an earlier message sharing at least "
            f"{min_shared_terms} content words, at least {min_days_apart} day(s) apart."
        ),
        "boundary": (
            "These are candidates, not contradictions. Orbita matched a self-correction "
            "marker and shared subject matter; it did not read either statement. Word "
            "overlap is not topic identity, and a change of position is not an error. "
            "Nothing here has been written to the belief graph, and nothing will be "
            "without your explicit instruction."
        ),
    }
