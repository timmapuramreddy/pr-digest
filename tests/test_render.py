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
    prev = {"prs": [strip_pr("b#1", bucket="warm"), strip_pr("b#2")]}
    resolved = [{"repo": "o/b", "number": 3, "resolution": "merged", "merged_at": None,
                 "closed_at": None, "created_at": None}]
    cur = [strip_pr("b#1", bucket="stale", waiting=4.0,
                    first_review_at="2026-08-28T10:00:00Z"),
           strip_pr("b#4")]
    s = digest.since_yesterday(cur, prev, resolved)
    assert s["new"] == ["o/b#4"]
    assert s["reviewed"] == ["o/b#1"]
    assert s["merged"] == 1 and s["closed"] == 0
    assert s["slipped"] == ["o/b#1"]


def test_since_yesterday_no_baseline():
    assert digest.since_yesterday([], None, []) is None


def test_row_badges_conflict_and_ci():
    html = digest.row(strip_pr("b#1", ci="failure", mergeable=False, additions=100, deletions=40))
    assert "CI ✗" in html and "⚠ conflicts" in html and "+100 −40" in html


def test_row_mergeable_unknown_is_shown():
    html = digest.row(strip_pr("b#1", mergeable=None))
    assert "merge unknown" in html


def test_row_mergeable_true_has_no_merge_badge():
    html = digest.row(strip_pr("b#1", mergeable=True))
    assert "merge unknown" not in html and "⚠ conflicts" not in html


def test_row_you_reviewed_tick():
    html = digest.row(strip_pr("b#1", your_last_review_sha="s", head_sha="s"))
    assert "you reviewed" in html
    html2 = digest.row(strip_pr("b#1", your_last_review_sha="old", head_sha="s"))
    assert "you reviewed" not in html2


def test_build_html_has_waiting_on_you_first():
    prs = [strip_pr("b#1", yours=True, your_last_review_sha=None, waiting=2.0),
           strip_pr("b#2", waiting=4.0, bucket="stale")]
    html = digest.build_html(
        make_cfg_for_render(dashboard_url=""), prs, [], [], None, [], []
    )
    assert html.index("Waiting on you") < html.index("Stale")


def test_since_yesterday_with_collect_shaped_prs():
    prev = {"prs": [strip_pr("b#1", bucket="warm")]}
    cur = [strip_pr("b#1")]
    del cur[0]["bucket"]  # collect() output has waiting, not bucket
    s = digest.since_yesterday(cur, prev, [])
    assert s["slipped"] == []
    cur[0]["waiting"] = 4.0  # warm → stale once bucket() computes it
    s = digest.since_yesterday(cur, prev, [])
    assert s["slipped"] == ["o/b#1"]


def make_cfg_for_render(**over):
    from datetime import datetime, timezone
    base = dict(
        token="t", viewer="me", dry_run=True, repos=["o/a"],
        data_dir=None, dashboard_url="https://ex.github.io/pr-digest/",
        now=datetime(2026, 8, 28, 5, 30, tzinfo=timezone.utc),  # a Friday
    )
    base.update(over)
    return digest.Config(**base)


def test_dashboard_link_in_footer_when_configured():
    prs = [strip_pr("b#1")]
    html = digest.build_html(make_cfg_for_render(), prs, None, None, None, [], [])
    assert "Review-debt dashboard" in html and "https://ex.github.io/pr-digest/" in html


def test_no_dashboard_link_without_url():
    prs = [strip_pr("b#1")]
    html = digest.build_html(make_cfg_for_render(dashboard_url=""), prs, None, None, None, [], [])
    assert "Review-debt dashboard" not in html


def test_no_all_clear_when_repo_errors():
    html = digest.build_html(
        make_cfg_for_render(dashboard_url=""), [], None, None, None, [],
        [("o/x", "HTTP 401")]
    )
    assert "Nothing waiting" not in html and "HTTP 401" in html
