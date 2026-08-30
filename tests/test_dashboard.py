import digest


def test_svg_line_basic():
    svg = digest.svg_line([("Mon", 3), ("Tue", 5)])
    assert "<svg" in svg and "<polyline" in svg and "Mon" in svg


def test_svg_line_empty():
    assert "No data" in digest.svg_line([])


def test_svg_line_flat_values_no_division_by_zero():
    svg = digest.svg_line([("a", 4), ("b", 4), ("c", 4)])
    assert "<polyline" in svg


def test_dashboard_contains_panels():
    snaps = [{"date": "2026-08-28", "prs": [
        {"repo": "o/a", "number": 1, "bucket": "warm", "waiting_days": 1.0},
    ], "resolved": []}]
    html = digest.build_dashboard(snaps, prs_now=[], first_review_trend=[])
    assert "Open PRs" in html and "Review debt" in html and "Per-repo" in html


def test_svg_line_labels_last_value():
    svg = digest.svg_line([("Mon", 3), ("Tue", 5)])
    assert ">5<" in svg


def test_svg_line_custom_empty_message():
    assert "needs history" in digest.svg_line([], empty_msg="needs history")


def test_dashboard_today_counts_and_timestamp():
    from datetime import datetime, timezone

    snaps = [{"date": "2026-08-29", "generated_at": "2026-08-29T05:30:00+00:00", "prs": [
        {"repo": "o/a", "number": 1, "bucket": "warm", "waiting_days": 1.0},
    ], "resolved": []}]
    now = datetime(2026, 8, 29, 5, 30, tzinfo=timezone.utc)
    html = digest.build_dashboard(snaps, prs_now=[], first_review_trend=[], now=now)
    assert "Data as of" in html
    assert "today: 0" in html          # 0 open PRs right now
    assert "0 stale or blocked" in html
    assert "Each point is the total open" in html
    assert "waiting more than 3 days" in html
    assert "Lower is faster" in html
    assert "fills in as PRs get their first review" in html


def test_dashboard_today_counts_reflect_prs_now():
    snaps = [{"date": "2026-08-29", "prs": [
        {"repo": "o/a", "number": 1, "bucket": "stale", "waiting_days": 4.0},
    ], "resolved": []}]
    prs = [{"repo": "o/a", "number": 2, "bucket": "stale", "waiting": 5.0}]
    html = digest.build_dashboard(snaps, prs_now=prs, first_review_trend=[])
    assert "today: 1" in html
    assert "1 stale or blocked" in html


def test_dashboard_accepts_collect_shaped_prs():
    prs = [{"repo": "o/a", "number": 2, "waiting": 5.0}]  # collect() has waiting, no bucket
    html = digest.build_dashboard([], prs_now=prs, first_review_trend=[])
    assert "today: 1" in html and "1 stale or blocked" in html
