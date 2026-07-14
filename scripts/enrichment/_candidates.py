"""Candidate dataclass, scoring, selection, and conflict detection.

A Candidate represents a single extracted data point (org name, legal form,
email, address, …) along with its provenance and a priority score derived
from the page type it was found on.

Public API
----------
Candidate                          dataclass
page_priority(page_type)           -> int
select_best(candidates)            -> tuple[Candidate, str] | None
detect_conflicts(candidates)       -> list[list[Candidate]]
group_by_type(candidates)          -> dict[str, list[Candidate]]
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ── Page-type priority ────────────────────────────────────────────────────────
# Higher = more authoritative source.
_PAGE_PRIORITY: dict[str, int] = {
    "Impressum": 6,
    "Contact":   5,
    "Footer":    4,
    "About":     3,
    "Team":      2,
    "Homepage":  1,
}

# How many priority levels apart two candidates must be before the lower one
# is considered "clearly inferior" and not a conflict.
_CONFLICT_PRIORITY_GAP = 2


def page_priority(page_type: str) -> int:
    """Return the priority score for *page_type* (default 1 for unknown types)."""
    return _PAGE_PRIORITY.get(page_type, 1)


# ── Candidate dataclass ───────────────────────────────────────────────────────

@dataclass
class Candidate:
    website_normalized: str
    page_url: str
    page_type: str
    candidate_type: str   # Organisation_Name | Organisation_Legal_Form |
                          # Organisation_Type | Organisation_Nonprofit_Status |
                          # Email | Address
    value: str
    evidence_text: str
    priority_score: int = field(init=False)
    extra_score: int = 0  # used for address completeness, etc.

    def __post_init__(self) -> None:
        self.priority_score = page_priority(self.page_type)

    @property
    def total_score(self) -> int:
        return self.priority_score * 10 + self.extra_score


# ── Selection ─────────────────────────────────────────────────────────────────

def select_best(
    candidates: list[Candidate],
) -> tuple[Candidate, str] | None:
    """Return (best_candidate, selection_reason) or None if list is empty.

    Ties in total_score are broken by order of appearance (first wins).
    """
    if not candidates:
        return None
    best = max(candidates, key=lambda c: c.total_score)
    reason = (
        f"Highest priority source ({best.page_type}, "
        f"score {best.total_score})"
    )
    return best, reason


# ── Conflict detection ────────────────────────────────────────────────────────

def _normalise_value(value: str) -> str:
    """Normalise a candidate value for equality comparison."""
    import re
    return re.sub(r"\s+", " ", value).strip().lower()


def detect_conflicts(
    candidates: list[Candidate],
) -> list[list[Candidate]]:
    """Return groups of conflicting candidates.

    Two candidates conflict when:
      - They have the same candidate_type
      - Their values differ (after normalisation)
      - Neither is clearly inferior (priority gap < _CONFLICT_PRIORITY_GAP)

    Returns a list of groups; each group contains 2+ conflicting candidates.
    Empty list = no conflicts.
    """
    if len(candidates) < 2:
        return []

    # Group by normalised value
    value_groups: dict[str, list[Candidate]] = {}
    for c in candidates:
        key = _normalise_value(c.value)
        value_groups.setdefault(key, []).append(c)

    if len(value_groups) == 1:
        return []  # all agree

    # Find the highest priority among all candidates
    max_priority = max(c.priority_score for c in candidates)

    # A candidate is "in play" if it is within _CONFLICT_PRIORITY_GAP of the best
    in_play = [
        c for c in candidates
        if max_priority - c.priority_score < _CONFLICT_PRIORITY_GAP
    ]

    # Group in-play candidates by normalised value
    play_groups: dict[str, list[Candidate]] = {}
    for c in in_play:
        key = _normalise_value(c.value)
        play_groups.setdefault(key, []).append(c)

    if len(play_groups) <= 1:
        return []  # only one value among in-play candidates → no real conflict

    # Return all in-play groups as a single conflict set
    return [list(group) for group in play_groups.values()]


# ── Grouping ──────────────────────────────────────────────────────────────────

def group_by_type(
    candidates: list[Candidate],
) -> dict[str, list[Candidate]]:
    """Partition *candidates* by their candidate_type."""
    groups: dict[str, list[Candidate]] = {}
    for c in candidates:
        groups.setdefault(c.candidate_type, []).append(c)
    return groups
