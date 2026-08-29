import digest


def make_pr(**over):
    base = {
        "yours": False,
        "reviewers": ["someone-else"],
        "your_last_review_sha": None,
        "head_sha": "s1",
    }
    base.update(over)
    return base


def test_requested_reviewer_unreviewed():
    assert digest.waiting_on_you(make_pr(yours=True))


def test_no_reviewers_assigned():
    assert digest.waiting_on_you(make_pr(reviewers=[]))


def test_not_mine_and_has_reviewers():
    assert not digest.waiting_on_you(make_pr())


def test_i_reviewed_current_head():
    assert not digest.waiting_on_you(make_pr(yours=True, your_last_review_sha="s1"))


def test_author_pushed_after_my_review():
    pr = make_pr(yours=True, your_last_review_sha="s1", head_sha="s2")
    assert digest.waiting_on_you(pr)
