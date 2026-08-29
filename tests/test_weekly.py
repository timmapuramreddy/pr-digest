import digest


def test_median_even_and_odd():
    assert digest.median([3.0, 1.0, 2.0]) == 2.0
    assert digest.median([4.0, 1.0, 2.0, 3.0]) == 2.5
    assert digest.median([]) is None


def test_collect_durations_dedupes_and_uses_earliest():
    snaps = [
        {"prs": [{"repo": "o/a", "number": 1, "created_at": "2026-08-20T09:00:00Z",
                  "first_review_at": "2026-08-21T09:00:00Z",
                  "your_last_review_at": None, "your_last_review_sha": None}],
         "resolved": [{"repo": "o/a", "number": 2, "resolution": "merged",
                       "created_at": "2026-08-20T09:00:00Z",
                       "merged_at": "2026-08-22T09:00:00Z"}]},
        {"prs": [{"repo": "o/a", "number": 1, "created_at": "2026-08-20T09:00:00Z",
                  "first_review_at": "2026-08-22T09:00:00Z",
                  "your_last_review_at": "2026-08-21T09:00:00Z",
                  "your_last_review_sha": "s"}],
         "resolved": []},
    ]
    firsts, mine, merges = digest.collect_durations(snaps)
    assert firsts["o/a#1"] == 1.0          # earliest observation wins
    assert mine["o/a#1"] == 1.0
    assert merges["o/a#2"] == 2.0


def test_weekly_stats_week_vs_prior():
    week = [{"prs": [{"repo": "o/a", "number": 1, "created_at": "2026-08-20T09:00:00Z",
                      "first_review_at": "2026-08-21T09:00:00Z",
                      "your_last_review_at": None, "your_last_review_sha": None}],
             "resolved": []}]
    stats = digest.weekly_stats(week, [])
    assert stats["first_review"] == 1.0
    assert stats["prior_first_review"] is None


def test_trend_line_uses_last_week_snapshot():
    line = digest.trend_line(12, 3, 10, 5)
    assert "▲" in line and "12" in line and "10" in line
    flat = digest.trend_line(5, 1, 5, 1)
    assert "▲" not in flat and "▼" not in flat
