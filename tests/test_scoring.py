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


# --- leg_prob edges ---

def test_leg_prob_yes_at_certainty():
    assert scoring.leg_prob("yes", 1.0) == pytest.approx(1.0)


def test_leg_prob_no_at_certainty():
    assert scoring.leg_prob("no", 1.0) == pytest.approx(0.0)


def test_leg_prob_no_at_zero_price():
    assert scoring.leg_prob("no", 0.0) == pytest.approx(1.0)


# --- frechet_ceiling edges ---

def test_ceiling_when_min_is_last():
    assert scoring.frechet_ceiling([0.9, 0.6, 0.11]) == pytest.approx(0.11)


def test_ceiling_single_leg_is_that_leg():
    assert scoring.frechet_ceiling([0.37]) == pytest.approx(0.37)


def test_ceiling_all_legs_equal():
    assert scoring.frechet_ceiling([0.4, 0.4, 0.4]) == pytest.approx(0.4)


def test_ceiling_with_impossible_leg_is_zero():
    assert scoring.frechet_ceiling([0.9, 0.0]) == pytest.approx(0.0)


# --- frechet_floor edges ---

def test_floor_single_leg_is_that_leg():
    assert scoring.frechet_floor([0.37]) == pytest.approx(0.37)


def test_floor_exactly_at_zero_boundary():
    assert scoring.frechet_floor([0.5, 0.5]) == pytest.approx(0.0)


def test_floor_three_likely_legs():
    assert scoring.frechet_floor([0.9, 0.9, 0.9]) == pytest.approx(0.7)


def test_floor_all_certain_legs_is_one():
    assert scoring.frechet_floor([1.0, 1.0]) == pytest.approx(1.0)


def test_floor_never_returns_negative():
    assert scoring.frechet_floor([0.1, 0.1, 0.1]) >= 0.0


# --- independence_price edges ---

def test_independence_single_leg_is_that_leg():
    assert scoring.independence_price([0.37]) == pytest.approx(0.37)


def test_independence_with_impossible_leg_is_zero():
    assert scoring.independence_price([0.9, 0.8, 0.0]) == pytest.approx(0.0)


def test_independence_all_certain_legs_is_one():
    assert scoring.independence_price([1.0, 1.0, 1.0]) == pytest.approx(1.0)


# --- score_fill: the coherence boundary ---

def test_fill_exactly_at_ceiling_is_coherent():
    """Float noise must not flip a fill sitting on the ceiling. 0.7 + 0.1 is
    0.7999999999999999, so a bare <= would call an 0.80 fill incoherent."""
    assert scoring.score_fill(0.80, [0.7 + 0.1, 0.9])["coherent"] is True


def test_fill_one_cent_above_ceiling_is_incoherent():
    assert scoring.score_fill(0.15, [0.82, 0.14])["coherent"] is False


def test_fill_one_cent_below_ceiling_is_coherent():
    assert scoring.score_fill(0.13, [0.82, 0.14])["coherent"] is True


def test_epsilon_does_not_forgive_a_real_overpay():
    assert scoring.score_fill(0.14001, [0.82, 0.14])["coherent"] is False


# --- score_fill: gaps and shape ---

def test_gap_to_ceiling_positive_when_fill_above():
    assert scoring.score_fill(0.80, [0.82, 0.14])["gap_to_ceiling"] == pytest.approx(0.66)


def test_gap_to_ceiling_zero_when_fill_at_ceiling():
    assert scoring.score_fill(0.14, [0.82, 0.14])["gap_to_ceiling"] == pytest.approx(0.0)


def test_gap_to_independence_positive_when_fill_above():
    assert scoring.score_fill(0.30, [0.5, 0.4])["gap_to_independence"] == pytest.approx(0.10)


def test_score_fill_returns_all_contract_keys():
    expected = {"ceiling", "floor", "independence", "gap_to_ceiling",
                "gap_to_independence", "coherent"}
    assert set(scoring.score_fill(0.10, [0.5, 0.4])) == expected


def test_coherent_is_a_real_bool_not_truthy():
    assert isinstance(scoring.score_fill(0.10, [0.5, 0.4])["coherent"], bool)


# --- the Frechet invariant: floor <= independence <= ceiling ---

def test_independence_never_below_floor():
    scored = scoring.score_fill(0.10, [0.9, 0.85, 0.8])
    assert scored["independence"] >= scored["floor"]


def test_independence_never_above_ceiling():
    scored = scoring.score_fill(0.10, [0.9, 0.85, 0.8])
    assert scored["independence"] <= scored["ceiling"]


def test_floor_never_above_ceiling():
    scored = scoring.score_fill(0.10, [0.9, 0.85, 0.8])
    assert scored["floor"] <= scored["ceiling"]
