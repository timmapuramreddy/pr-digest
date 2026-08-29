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
