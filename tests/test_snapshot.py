import json
from datetime import date, datetime, timezone

import digest


def make_cfg(tmp_path):
    return digest.Config(
        token="t", viewer="me", dry_run=True, repos=["a/b"],
        data_dir=tmp_path,
        now=datetime(2026, 8, 29, 5, 30, tzinfo=timezone.utc),
    )


def make_pr(repo="a/b", number=1):
    return {"repo": repo, "number": number, "created_at": "2026-08-25T09:00:00Z"}


def test_snapshot_roundtrip(tmp_path):
    cfg = make_cfg(tmp_path)
    snap = digest.build_snapshot(cfg, [make_pr()], resolved=[], errors=False)
    (tmp_path / "snapshots").mkdir()
    (tmp_path / "snapshots" / "2026-08-29.json").write_text(json.dumps(snap))
    loaded = digest.read_snapshot(cfg, date(2026, 8, 29))
    assert loaded["date"] == "2026-08-29" and loaded["prs"][0]["number"] == 1


def test_previous_snapshot_skips_weekend(tmp_path):
    cfg = make_cfg(tmp_path)
    (tmp_path / "snapshots").mkdir()
    (tmp_path / "snapshots" / "2026-08-28.json").write_text(json.dumps({"prs": [], "resolved": []}))
    prev = digest.previous_snapshot(cfg)  # now = Sat 2026-08-29; Friday file exists
    assert prev is not None


def test_previous_snapshot_missing(tmp_path):
    cfg = make_cfg(tmp_path)
    assert digest.previous_snapshot(cfg) is None


def test_resolve_disappeared_merged(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path)
    prev = {"prs": [make_pr()], "resolved": []}

    def fake_api(cfg, path, method="GET", body=None):
        return {"merged": True, "merged_at": "2026-08-28T14:00:00Z", "closed_at": None}

    monkeypatch.setattr(digest, "api", fake_api)
    resolved = digest.resolve_disappeared(cfg, prev, current_keys=set())
    assert resolved[0]["resolution"] == "merged"
    assert resolved[0]["merged_at"] == "2026-08-28T14:00:00Z"


def test_resolve_disappeared_closed_unmerged(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path)
    prev = {"prs": [make_pr()], "resolved": []}

    def fake_api(cfg, path, method="GET", body=None):
        return {"merged": False, "merged_at": None, "closed_at": "2026-08-28T15:00:00Z", "state": "closed"}

    monkeypatch.setattr(digest, "api", fake_api)
    resolved = digest.resolve_disappeared(cfg, prev, current_keys=set())
    assert resolved[0]["resolution"] == "closed_unmerged"


def test_resolve_disappeared_still_open_is_skipped(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path)
    prev = {"prs": [make_pr()], "resolved": []}
    resolved = digest.resolve_disappeared(cfg, prev, current_keys={"a/b#1"})
    assert resolved == []
