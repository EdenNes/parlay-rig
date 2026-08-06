import pytest

import calibration


def fill(price, result):
    return {"price": price, "result": result}


class TestBucketOf:
    def test_the_lowest_price_lands_in_the_first_bucket(self):
        assert calibration.bucket_of(0.005) == 0

    def test_a_price_on_an_edge_opens_the_next_bucket(self):
        assert calibration.bucket_of(0.01) == 1

    def test_a_mid_range_price_lands_where_expected(self):
        assert calibration.bucket_of(0.07) == 3

    def test_the_highest_prices_share_the_last_bucket(self):
        assert calibration.bucket_of(0.99) == len(
            calibration.BUCKET_EDGES) - 2

    def test_a_price_at_one_stays_in_the_last_bucket(self):
        assert calibration.bucket_of(1.0) == len(
            calibration.BUCKET_EDGES) - 2


class TestWilsonInterval:
    def test_the_interval_brackets_the_observed_rate(self):
        low, high = calibration.wilson_interval(50, 100)
        assert low < 0.5 < high

    def test_more_data_narrows_the_interval(self):
        narrow = calibration.wilson_interval(500, 1000)
        wide = calibration.wilson_interval(5, 10)
        assert (narrow[1] - narrow[0]) < (wide[1] - wide[0])

    def test_zero_hits_never_produces_a_negative_lower_bound(self):
        low, _ = calibration.wilson_interval(0, 30)
        assert low >= 0.0

    def test_every_trial_hitting_never_exceeds_one(self):
        _, high = calibration.wilson_interval(30, 30)
        assert high <= 1.0

    def test_a_known_case_matches_the_published_formula(self):
        low, high = calibration.wilson_interval(2, 100)
        assert low == pytest.approx(0.0055, abs=0.001)
        assert high == pytest.approx(0.0700, abs=0.001)


class TestCalibrate:
    def test_a_single_bucket_reports_its_hit_rate(self):
        rows = calibration.calibrate([fill(0.12, "yes"), fill(0.12, "no"),
                                      fill(0.12, "no"), fill(0.12, "no")])
        assert len(rows) == 1
        assert rows[0]["hit_rate"] == pytest.approx(0.25)

    def test_misses_are_counted_not_dropped(self):
        rows = calibration.calibrate([fill(0.12, "no")] * 10)
        assert rows[0]["n"] == 10
        assert rows[0]["hits"] == 0

    def test_edge_is_the_sellers_price_minus_the_hit_rate(self):
        rows = calibration.calibrate([fill(0.20, "yes")] +
                                     [fill(0.20, "no")] * 9)
        assert rows[0]["edge"] == pytest.approx(0.10)

    def test_a_market_that_is_fair_shows_no_edge(self):
        rows = calibration.calibrate([fill(0.50, "yes")] * 5 +
                                     [fill(0.50, "no")] * 5)
        assert rows[0]["edge"] == pytest.approx(0.0)

    def test_prices_are_grouped_into_separate_buckets(self):
        rows = calibration.calibrate([fill(0.005, "no"), fill(0.40, "yes")])
        assert len(rows) == 2

    def test_rows_come_back_in_ascending_price_order(self):
        rows = calibration.calibrate([fill(0.40, "no"), fill(0.005, "no")])
        assert [r["bucket"] for r in rows] == sorted(
            r["bucket"] for r in rows)

    def test_an_empty_bucket_is_omitted_rather_than_reported_as_zero(self):
        rows = calibration.calibrate([fill(0.12, "no")])
        assert len(rows) == 1

    def test_mean_price_is_the_average_within_the_bucket(self):
        rows = calibration.calibrate([fill(0.10, "no"), fill(0.14, "no")])
        assert rows[0]["mean_price"] == pytest.approx(0.12)

    def test_each_row_carries_an_interval(self):
        rows = calibration.calibrate([fill(0.12, "no")] * 20)
        low, high = rows[0]["interval"]
        assert 0.0 <= low <= high <= 1.0

    def test_no_fills_produces_no_rows(self):
        assert calibration.calibrate([]) == []
