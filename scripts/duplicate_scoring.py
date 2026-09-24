"""Deterministic literary-score adjustment for repeated-character groups."""

from collections import Counter

from text_normalizer import normalize_line

PENALTY_PER_EXTRA_GROUP = 0.20


def duplicate_group_count(lines):
    counts = Counter(cell['char'] for index, line in enumerate(lines, 1)
                     if isinstance(line, str) and line
                     for cell in normalize_line(line, index)['cells'] if cell['is_han'])
    return sum(count >= 2 for count in counts.values())


def penalty_for_group_count(group_count):
    return max(0, group_count - 1) * PENALTY_PER_EXTRA_GROUP


def adjust_score(score, before_lines, after_lines):
    """Apply only the penalty change introduced by this candidate/unit."""
    before = duplicate_group_count(before_lines)
    after = duplicate_group_count(after_lines)
    delta = penalty_for_group_count(after) - penalty_for_group_count(before)
    adjusted = min(1.0, max(0.0, score - delta))
    return adjusted, {
        'duplicate_groups_before': before,
        'duplicate_groups_after': after,
        'duplicate_score_penalty': max(0.0, delta),
        'duplicate_score_relief': max(0.0, -delta),
    }


def adjust_full_poem_score(score, lines):
    groups = duplicate_group_count(lines)
    penalty = penalty_for_group_count(groups)
    return max(0.0, score - penalty), {
        'duplicate_groups': groups,
        'duplicate_score_penalty': penalty,
    }
