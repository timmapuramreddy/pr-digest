from datetime import datetime, timezone

import digest


def make_cfg(now_hour=5):
    return digest.Config(
        token="t",
        viewer="me",
        dry_run=True,
        repos=["a/b"],
        data_dir=None,
        now=datetime(2026, 8, 29, now_hour, 30, tzinfo=timezone.utc),
    )


def state(keys, you=None, errors=False):
    return {
        "prs": [
            {"key": k, "bucket": "warm", "you_queue": bool(you and k in you)}
            for k in keys
        ],
        "errors": errors,
    }


def test_first_run_sends():
    changed, reason = digest.diff_state(state(["a#1"]), None)
    assert changed and "baseline" in reason


def test_no_change_sends_nothing():
    s = state(["a#1"])
    changed, _ = digest.diff_state(s, s)
    assert not changed


def test_new_pr_sends():
    changed, reason = digest.diff_state(state(["a#1", "a#2"]), state(["a#1"]))
    assert changed and "a#2" in reason


def test_bucket_move_sends():
    cur = state(["a#1"])
    cur["prs"][0]["bucket"] = "stale"
    changed, reason = digest.diff_state(cur, state(["a#1"]))
    assert changed and ("bucket" in reason.lower() or "moved" in reason)


def test_waiting_on_you_gain_sends():
    changed, _ = digest.diff_state(state(["a#1"], you={"a#1"}), state(["a#1"]))
    assert changed


def test_waiting_on_you_loss_is_silent():
    base = state(["a#1"], you={"a#1"})
    changed, _ = digest.diff_state(state(["a#1"]), base)
    assert not changed


def test_repo_error_sends():
    changed, _ = digest.diff_state(state(["a#1"], errors=True), state(["a#1"]))
    assert changed


def test_morning_run_always_sends():
    send, _ = digest.should_send(make_cfg(now_hour=3), state(["a#1"]), state(["a#1"]))
    assert send


def test_afternoon_quiet_run_sends_nothing():
    s = state(["a#1"])
    send, _ = digest.should_send(make_cfg(now_hour=5), s, s)
    assert not send
