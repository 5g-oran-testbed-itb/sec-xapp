"""Unit tests for DualLSTMDetector 2-of-3 voting."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import after path setup — DualLSTMDetector does not exist yet
from evaluate_detection import _vote_2of3


def test_vote_false_when_buffer_empty():
    buf = [False, False, False]
    assert _vote_2of3(buf) is False


def test_vote_false_when_only_one_true():
    buf = [True, False, False]
    assert _vote_2of3(buf) is False


def test_vote_true_when_two_true():
    buf = [True, True, False]
    assert _vote_2of3(buf) is True


def test_vote_true_when_all_true():
    buf = [True, True, True]
    assert _vote_2of3(buf) is True


def test_vote_true_non_consecutive():
    buf = [True, False, True]
    assert _vote_2of3(buf) is True


if __name__ == "__main__":
    test_vote_false_when_buffer_empty()
    test_vote_false_when_only_one_true()
    test_vote_true_when_two_true()
    test_vote_true_when_all_true()
    test_vote_true_non_consecutive()
    print("All tests passed.")
