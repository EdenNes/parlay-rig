import pytest

import scoring


def test_leg_prob_yes_side():
    assert scoring.leg_prob("yes", 0.82) == pytest.approx(0.82)


def test_leg_prob_no_side():
    assert scoring.leg_prob("no", 0.82) == pytest.approx(0.18)


def test_ceiling_is_min_leg():
    assert scoring.frechet_ceiling([0.82, 0.14]) == pytest.approx(0.14)


def test_floor_two_likely_legs():
    assert scoring.frechet_floor([0.8, 0.7]) == pytest.approx(0.5)


def test_floor_clamps_at_zero():
    assert scoring.frechet_floor([0.5, 0.4, 0.3]) == pytest.approx(0.0)


def test_independence_multiplies():
    assert scoring.independence_price([0.5, 0.4, 0.3]) == pytest.approx(0.06)


def test_study_fill_flagged_incoherent():
    assert scoring.score_fill(0.80, [0.82, 0.14])["coherent"] is False


def test_coherent_fill_flagged_true():
    assert scoring.score_fill(0.10, [0.82, 0.14])["coherent"] is True


def test_gap_to_independence_signed():
    assert scoring.score_fill(0.10, [0.5, 0.4])["gap_to_independence"] == pytest.approx(-0.10)
