"""
Daily Run Statistics Tracker
Tracks real-time discovery, deduplication, and skip counters across platforms.
"""
from dataclasses import dataclass, field


@dataclass
class DailyRunStats:
    fresh_discovered: int = 0
    previously_applied_skipped: int = 0
    duplicate_skipped: int = 0

    def reset(self):
        self.fresh_discovered = 0
        self.previously_applied_skipped = 0
        self.duplicate_skipped = 0


_STATS = DailyRunStats()


def record_discovered(count: int = 1):
    _STATS.fresh_discovered += count


def record_previously_applied_skipped(count: int = 1):
    _STATS.previously_applied_skipped += count


def record_duplicate_skipped(count: int = 1):
    _STATS.duplicate_skipped += count


def get_stats() -> DailyRunStats:
    return _STATS


def reset_stats():
    _STATS.reset()
