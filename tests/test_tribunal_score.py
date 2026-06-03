"""Tests for tribunal._safe_int_score helper."""
import pytest
from ripple.agents.tribunal import _safe_int_score


class TestSafeIntScore:
    def test_plain_int(self):
        assert _safe_int_score(4) == 4

    def test_zero_int(self):
        assert _safe_int_score(0) == 0

    def test_float(self):
        assert _safe_int_score(3.7) == 3

    def test_float_string(self):
        assert _safe_int_score("4.0") == 4

    def test_int_string(self):
        assert _safe_int_score("5") == 5

    def test_dict_with_score_key(self):
        assert _safe_int_score({"score": 3, "reason": "good"}) == 3

    def test_dict_with_value_key(self):
        assert _safe_int_score({"value": 2, "note": "meh"}) == 2

    def test_dict_with_nested_score(self):
        assert _safe_int_score({"score": "4.0", "reason": "nice"}) == 4

    def test_dict_without_score_or_value(self):
        assert _safe_int_score({"reason": "no score"}) == 3  # default

    def test_none(self):
        assert _safe_int_score(None) == 3  # default

    def test_invalid_string(self):
        assert _safe_int_score("abc") == 3  # default

    def test_empty_string(self):
        assert _safe_int_score("") == 3  # default

    def test_list_type(self):
        assert _safe_int_score([1, 2, 3]) == 3  # default

    def test_custom_default(self):
        assert _safe_int_score(None, default=5) == 5

    def test_negative_int(self):
        assert _safe_int_score(-1) == -1

    def test_whitespace_string(self):
        assert _safe_int_score("  3  ") == 3
