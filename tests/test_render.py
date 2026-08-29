import digest


def strip_pr(key, **over):
    repo, number = key.split("#")
    base = {
        "repo": f"o/{repo}", "number": int(number), "title": "t", "url": "u",
        "author": "a", "base": "main", "head_sha": "s",
        "created_at": "2026-08-25T09:00:00Z",
        "first_review_at": None, "your_last_review_sha": None,
        "ci": "success", "additions": 10, "deletions": 2, "mergeable": True,
        "bucket": "warm", "yours": False, "reviewers": ["r"], "waiting": 1.0,
    }
    base.update(over)
    return base


def test_since_yesterday_counts():
    prev = {"prs": [strip_pr("b#1", bucket="warm"), strip_pr("b#2")], "resolved": [
        {"repo": "o/b", "number": 3, "resolution": "merged", "merged_at": None,
         "closed_at": None, "created_at": None}
    ]}
    cur = [strip_pr("b#1", bucket="stale", first_review_at="2026-08-28T10:00:00Z"),
           strip_pr("b#4")]
    s = digest.since_yesterday(cur, prev)
    assert s["new"] == ["o/b#4"]
    assert s["reviewed"] == ["o/b#1"]
    assert s["merged"] == 1 and s["closed"] == 0
    assert s["slipped"] == ["o/b#1"]


def test_since_yesterday_no_baseline():
    assert digest.since_yesterday([], None) is None


def test_row_badges_conflict_and_ci():
    html = digest.row(strip_pr("b#1", ci="failure", mergeable=False, additions=100, deletions=40))
    assert "CI ✗" in html and "⚠ conflicts" in html and "+100 −40" in html


def test_row_mergeable_unknown_is_shown():
    html = digest.row(strip_pr("b#1", mergeable=None))
    assert "merge unknown" in html


def test_row_mergeable_true_has_no_merge_badge():
    html = digest.row(strip_pr("b#1", mergeable=True))
    assert "merge unknown" not in html and "⚠ conflicts" not in html


def test_build_html_has_waiting_on_you_first():
    prs = [strip_pr("b#1", yours=True, your_last_review_sha=None, waiting=2.0),
           strip_pr("b#2", waiting=4.0, bucket="stale")]
    html = digest.build_html(None, prs, [], [], None)
    assert html.index("Waiting on you") < html.index("Stale")
