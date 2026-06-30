"""Unit tests for pure functions extracted from scripts/data_wizard.py."""

import pytest


def _import():
    from data_wizard import find_duplicate_pairs
    return find_duplicate_pairs


def _row(name, date, distance, source=""):
    return {"name": name, "date": date, "distance": distance, "source": source}


class TestFindDuplicatePairs:
    def setup_method(self):
        self.find = _import()

    def test_no_rows_returns_empty(self):
        names, pairs = self.find([])
        assert names == set()
        assert pairs == []

    def test_single_trip_no_duplicates(self):
        rows = [_row("a.gpx", "2024-01-01", 10.0)]
        names, pairs = self.find(rows)
        assert names == set()
        assert pairs == []

    def test_same_date_similar_distance(self):
        rows = [
            _row("a.gpx", "2024-01-01", 10.0),
            _row("b.gpx", "2024-01-01", 10.3),  # within 5%
        ]
        names, pairs = self.find(rows)
        assert "a.gpx" in names
        assert "b.gpx" in names
        assert len(pairs) == 1

    def test_same_date_very_different_distance(self):
        rows = [
            _row("a.gpx", "2024-01-01", 10.0),
            _row("b.gpx", "2024-01-01", 20.0),  # 100% difference
        ]
        names, pairs = self.find(rows)
        assert names == set()
        assert pairs == []

    def test_different_dates_not_flagged(self):
        rows = [
            _row("a.gpx", "2024-01-01", 10.0),
            _row("b.gpx", "2024-01-02", 10.0),  # same distance, different date
        ]
        names, pairs = self.find(rows)
        assert names == set()

    def test_both_none_distance_not_flagged(self):
        # Can't confirm duplicate without distance data — require known distance
        rows = [
            _row("a.gpx", "2024-01-01", None),
            _row("b.gpx", "2024-01-01", None),
        ]
        names, pairs = self.find(rows)
        assert names == set()
        assert pairs == []

    def test_one_none_distance_not_flagged(self):
        rows = [
            _row("a.gpx", "2024-01-01", None),
            _row("b.gpx", "2024-01-01", 10.0),
        ]
        names, pairs = self.find(rows)
        assert names == set()

    def test_three_trips_same_date_two_similar(self):
        rows = [
            _row("a.gpx", "2024-01-01", 10.0),
            _row("b.gpx", "2024-01-01", 10.2),  # within 5% of a
            _row("c.gpx", "2024-01-01", 25.0),  # far from both
        ]
        names, pairs = self.find(rows)
        assert "a.gpx" in names
        assert "b.gpx" in names
        assert "c.gpx" not in names
        assert len(pairs) == 1

    def test_exact_match_distance(self):
        rows = [
            _row("a.gpx", "2024-06-15", 42.195),
            _row("b.gpx", "2024-06-15", 42.195),
        ]
        names, pairs = self.find(rows)
        assert len(pairs) == 1

    def test_boundary_5_percent(self):
        rows = [
            _row("a.gpx", "2024-01-01", 100.0),
            _row("b.gpx", "2024-01-01", 95.0),   # exactly 5% — within
            _row("c.gpx", "2024-01-01", 94.9),   # just over 5% — not a dup
        ]
        names, pairs = self.find(rows)
        # a+b should be flagged; a+c or b+c should not
        pair_name_sets = [{p[0]["name"], p[1]["name"]} for p in pairs]
        assert {"a.gpx", "b.gpx"} in pair_name_sets
        assert {"a.gpx", "c.gpx"} not in pair_name_sets
