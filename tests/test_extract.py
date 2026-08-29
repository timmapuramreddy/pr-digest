import digest

REVIEW = lambda login, at, sha: {"user": {"login": login}, "submitted_at": at, "commit_id": sha}


def test_review_fields_none_when_unreviewed():
    f = digest.extract_review_fields([], "me")
    assert f["first_review_at"] is None and f["your_last_review_sha"] is None


def test_review_fields_first_last():
    reviews = [REVIEW("a", "2026-08-20T10:00:00Z", "s1"), REVIEW("b", "2026-08-21T10:00:00Z", "s2")]
    f = digest.extract_review_fields(reviews, "me")
    assert f["first_review_at"] == "2026-08-20T10:00:00Z"
    assert f["last_review_at"] == "2026-08-21T10:00:00Z"
    assert f["your_last_review_at"] is None


def test_review_fields_my_last_review_sha():
    reviews = [REVIEW("me", "2026-08-20T10:00:00Z", "s1"), REVIEW("me", "2026-08-22T10:00:00Z", "s3")]
    f = digest.extract_review_fields(reviews, "me")
    assert f["your_last_review_at"] == "2026-08-22T10:00:00Z"
    assert f["your_last_review_sha"] == "s3"


def test_ci_none_without_runs():
    assert digest.ci_status({"check_runs": []}) == "none"
    assert digest.ci_status(None) == "none"


def test_ci_pending_beats_success():
    payload = {"check_runs": [
        {"status": "completed", "conclusion": "success"},
        {"status": "in_progress", "conclusion": None},
    ]}
    assert digest.ci_status(payload) == "pending"


def test_ci_failure():
    payload = {"check_runs": [{"status": "completed", "conclusion": "failure"}]}
    assert digest.ci_status(payload) == "failure"


def test_ci_success():
    payload = {"check_runs": [{"status": "completed", "conclusion": "success"}]}
    assert digest.ci_status(payload) == "success"
