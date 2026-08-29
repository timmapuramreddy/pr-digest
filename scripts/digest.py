#!/usr/bin/env python3
"""Daily PR digest across the Scikraft repos.

Reads every configured repo, finds open non-draft PRs, ages them into
buckets, writes an HTML email body, and (optionally) pings PRs that have
been waiting past the escalation threshold.

Stdlib only — no pip install step in CI. The script computes and writes
files to the workspace only; the workflow owns every git push and every
pass/fail decision.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta as _timedelta, timezone
from pathlib import Path

API = "https://api.github.com"

DEFAULT_REPOS = [
    "Scikraft-Edu-Engg-Design/xperimentor-backend-v3.0",
    "Scikraft-Edu-Engg-Design/xperimentor-frontend-v3.0",
    "Scikraft-Edu-Engg-Design/tiqer-standalone-backend",
    "Scikraft-Edu-Engg-Design/tiqer-standalone-frontend",
    "Scikraft-Edu-Engg-Design/tiqer-standalone-mobile",
    "Scikraft-Edu-Engg-Design/xp-live-mobile",
    "scikraft-eed/xperimentor-devops",
]

# Ageing thresholds, in days.
STALE_DAYS = 3
ESCALATE_DAYS = 7

# Hidden marker so we can tell our own ping comments apart from human ones.
PING_MARKER = "<!-- pr-digest-stale-ping -->"


@dataclass
class Config:
    token: str
    viewer: str
    dry_run: bool
    repos: list
    data_dir: Path
    dashboard_url: str
    now: datetime


def load_config(now=None):
    """Read settings from the environment. Injectable `now` keeps tests deterministic."""
    return Config(
        token=os.environ["PR_SCAN_TOKEN"],
        viewer=os.environ.get("VIEWER_LOGIN", "timmapuramreddy"),
        dry_run=os.environ.get("DRY_RUN", "false").lower() == "true",
        repos=list(DEFAULT_REPOS),
        data_dir=Path(os.environ.get("DATA_DIR", "data")),
        dashboard_url=os.environ.get("DASHBOARD_URL", ""),
        now=now or datetime.now(timezone.utc),
    )


def api(cfg, path, method="GET", body=None):
    """Call the GitHub API. Returns parsed JSON, or None on 404."""
    url = path if path.startswith("http") else f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {cfg.token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def parse_ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def age_days(cfg, ts):
    return (cfg.now - ts).total_seconds() / 86400


def already_pinged(cfg, repo, number):
    """True if we posted a ping on this PR in the last ESCALATE_DAYS."""
    comments = api(cfg, f"/repos/{repo}/issues/{number}/comments?per_page=100") or []
    for c in reversed(comments):
        if PING_MARKER in (c.get("body") or ""):
            return age_days(cfg, parse_ts(c["created_at"])) < ESCALATE_DAYS
    return False


def ping(cfg, repo, number, waiting):
    body = (
        f"{PING_MARKER}\n"
        f"⏳ This PR has been open and unreviewed for **{waiting:.0f} days**.\n\n"
        f"Reviewers, please take a look — or close it if it is no longer needed."
    )
    api(cfg, f"/repos/{repo}/issues/{number}/comments", method="POST", body={"body": body})


def extract_review_fields(reviews, viewer):
    """First/last review timestamps and the viewer's last review sha.

    The review payload carries commit_id — the sha the review was made on —
    which is what lets the waiting-on-you queue survive re-reviews.
    """
    stamped = [r for r in reviews if r.get("submitted_at")]
    if not stamped:
        return {
            "first_review_at": None,
            "last_review_at": None,
            "your_last_review_at": None,
            "your_last_review_sha": None,
        }
    first = min(stamped, key=lambda r: parse_ts(r["submitted_at"]))
    last = max(stamped, key=lambda r: parse_ts(r["submitted_at"]))
    mine = [r for r in stamped if (r.get("user") or {}).get("login") == viewer]
    my_last = max(mine, key=lambda r: parse_ts(r["submitted_at"])) if mine else None
    return {
        "first_review_at": first["submitted_at"],
        "last_review_at": last["submitted_at"],
        "your_last_review_at": my_last["submitted_at"] if my_last else None,
        "your_last_review_sha": my_last.get("commit_id") if my_last else None,
    }


def ci_status(check_runs_payload):
    """Roll a check-runs payload up to none/pending/success/failure/neutral."""
    runs = (check_runs_payload or {}).get("check_runs") or []
    if not runs:
        return "none"
    if any(r.get("status") != "completed" for r in runs):
        return "pending"
    bad = {"failure", "timed_out", "startup_failure", "action_required", "stale"}
    conclusions = {r.get("conclusion") for r in runs}
    if conclusions & bad:
        return "failure"
    if "success" in conclusions:
        return "success"
    return "neutral"


def pr_detail(cfg, repo, number):
    """Size + mergeability. mergeable is None while GitHub computes it."""
    d = api(cfg, f"/repos/{repo}/pulls/{number}") or {}
    return {
        "additions": d.get("additions", 0),
        "deletions": d.get("deletions", 0),
        "mergeable": d.get("mergeable"),
    }


def collect(cfg, repo):
    """Return (list_of_pr_dicts, error_string_or_None) for one repo."""
    try:
        pulls = api(cfg, f"/repos/{repo}/pulls?state=open&per_page=100")
    except urllib.error.HTTPError as exc:
        return [], f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return [], f"network error: {exc.reason}"
    if pulls is None:
        return [], "not found / no access"

    out = []
    for pr in pulls:
        if pr.get("draft"):
            continue
        number = pr["number"]
        created = parse_ts(pr["created_at"])
        sha = pr["head"]["sha"]
        try:
            reviews = api(cfg, f"/repos/{repo}/pulls/{number}/reviews?per_page=100") or []
            detail = pr_detail(cfg, repo, number)
            checks = api(cfg, f"/repos/{repo}/commits/{sha}/check-runs?per_page=100")
        except urllib.error.HTTPError as exc:
            return [], f"HTTP {exc.code} on PR {number}"
        except urllib.error.URLError as exc:
            return [], f"network error on PR {number}: {exc.reason}"
        fields = extract_review_fields(reviews, cfg.viewer)
        reviewed = fields["last_review_at"] is not None
        waiting = age_days(cfg, parse_ts(fields["last_review_at"]) if reviewed else created)
        reviewers = [u["login"] for u in pr.get("requested_reviewers") or []]
        out.append(
            {
                "repo": repo,
                "number": number,
                "title": pr["title"],
                "url": pr["html_url"],
                "author": pr["user"]["login"],
                "base": pr["base"]["ref"],
                "head_sha": sha,
                "created_at": pr["created_at"],
                "age": age_days(cfg, created),
                "waiting": waiting,
                "reviewed": reviewed,
                **fields,
                "reviewers": reviewers,
                "yours": cfg.viewer in reviewers,
                "ci": ci_status(checks),
                "additions": detail["additions"],
                "deletions": detail["deletions"],
                "mergeable": detail["mergeable"],
            }
        )
    return out, None


def pr_key(pr):
    return f"{pr['repo']}#{pr['number']}"


def waiting_on_you(pr):
    """True when the viewer should act: he is a requested reviewer, or nobody
    is assigned, and the current head is not the commit he already reviewed.

    GitHub removes a reviewer from requested_reviewers the moment they
    review — comparing commit ids is what keeps this queue alive after the
    author pushes fixes to a changes-requested review.
    """
    involved = pr["yours"] or not pr["reviewers"]
    already_handled = (
        pr["your_last_review_sha"] is not None
        and pr["your_last_review_sha"] == pr["head_sha"]
    )
    return involved and not already_handled


def state_for_baseline(all_prs, errors):
    """Minimal per-PR state used to decide whether the next run changed anything."""
    return {
        "prs": [
            {
                "key": pr_key(p),
                "bucket": bucket(p),
                "you_queue": waiting_on_you(p),
            }
            for p in all_prs
        ],
        "errors": bool(errors),
    }


def diff_state(current, baseline):
    """Return (changed, reason) comparing current state with the last-sent baseline."""
    if baseline is None:
        return True, "no baseline (first run)"
    cur = {p["key"]: p for p in current["prs"]}
    prev = {p["key"]: p for p in baseline.get("prs", [])}
    if set(cur) != set(prev):
        gained = sorted(set(cur) - set(prev))
        lost = sorted(set(prev) - set(cur))
        return True, f"pr set changed (+{gained} -{lost})"
    for key in sorted(cur):
        if cur[key]["bucket"] != prev[key]["bucket"]:
            return True, f"{key} moved {prev[key]['bucket']} → {cur[key]['bucket']}"
        if cur[key].get("you_queue") and not prev[key].get("you_queue"):
            return True, f"{key} is now waiting on you"
    if current.get("errors"):
        return True, "repo read error"
    return False, "no change"


def should_send(cfg, current, baseline):
    """Morning run always sends; other runs send only on change."""
    if cfg.now.hour == 3:
        return True, "morning run"
    return diff_state(current, baseline)


def read_baseline(cfg):
    path = cfg.data_dir / "last-sent.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def build_snapshot(cfg, all_prs, resolved, errors):
    """One JSON-serialisable snapshot of today's state. Last write per day wins."""
    return {
        "date": cfg.now.date().isoformat(),
        "generated_at": cfg.now.isoformat(),
        "prs": [
            {
                "repo": p["repo"],
                "number": p["number"],
                "title": p.get("title"),
                "url": p.get("url"),
                "author": p.get("author"),
                "base": p.get("base"),
                "head_sha": p.get("head_sha"),
                "created_at": p["created_at"],
                "age_days": round(p.get("age", 0), 2),
                "waiting_days": round(p.get("waiting", 0), 2),
                "first_review_at": p.get("first_review_at"),
                "last_review_at": p.get("last_review_at"),
                "your_last_review_at": p.get("your_last_review_at"),
                "your_last_review_sha": p.get("your_last_review_sha"),
                "reviewers": p.get("reviewers", []),
                "yours": p.get("yours", False),
                "ci": p.get("ci"),
                "additions": p.get("additions", 0),
                "deletions": p.get("deletions", 0),
                "mergeable": p.get("mergeable"),
                "bucket": bucket({**p, "waiting": p.get("waiting", 0)}),
                "you_queue": waiting_on_you(
                    {
                        **p,
                        "reviewers": p.get("reviewers", []),
                        "yours": p.get("yours", False),
                        "your_last_review_sha": p.get("your_last_review_sha"),
                        "head_sha": p.get("head_sha"),
                    }
                ),
            }
            for p in all_prs
        ],
        "resolved": resolved,
        "errors": bool(errors),
    }


def read_snapshot(cfg, day):
    path = cfg.data_dir / "snapshots" / f"{day.isoformat()}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def previous_snapshot(cfg, lookback_days=4):
    """Most recent snapshot before today — walks back over weekends."""
    for offset in range(1, lookback_days + 1):
        snap = read_snapshot(cfg, cfg.now.date() - _timedelta(days=offset))
        if snap is not None:
            return snap
    return None


def resolve_disappeared(cfg, prev_snapshot, current_keys):
    """PRs in the previous snapshot but gone now → merged or closed_unmerged.

    Blind spot by design: a PR opened and merged between two runs is never
    seen by any snapshot.
    """
    out = []
    for p in prev_snapshot.get("prs", []):
        key = f"{p['repo']}#{p['number']}"
        if key in current_keys:
            continue
        try:
            d = api(cfg, f"/repos/{p['repo']}/pulls/{p['number']}") or {}
        except (urllib.error.HTTPError, urllib.error.URLError):
            continue
        merged = bool(d.get("merged"))
        out.append(
            {
                "repo": p["repo"],
                "number": p["number"],
                "resolution": "merged" if merged else "closed_unmerged",
                "merged_at": d.get("merged_at") if merged else None,
                "closed_at": d.get("closed_at"),
                "created_at": p.get("created_at"),
            }
        )
    return out


# Trends
def median(values):
    vals = sorted(values)
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2


def _days_between(iso_start, iso_end):
    if not iso_start or not iso_end:
        return None
    return (parse_ts(iso_end) - parse_ts(iso_start)).total_seconds() / 86400


def collect_durations(snapshots):
    """Dedupe PRs across snapshots; earliest observed duration wins.

    first_review_at is stable once set, so repeated observations of the same
    PR should agree — min() just absorbs clock/scheduling skew.
    """
    firsts, mine, merges = {}, {}, {}
    for snap in snapshots:
        for p in snap.get("prs", []):
            key = f"{p['repo']}#{p['number']}"
            d = _days_between(p.get("created_at"), p.get("first_review_at"))
            if d is not None:
                firsts[key] = min(firsts.get(key, 1e9), d)
            d = _days_between(p.get("created_at"), p.get("your_last_review_at"))
            if d is not None:
                mine[key] = min(mine.get(key, 1e9), d)
        for r in snap.get("resolved", []):
            if r.get("resolution") != "merged":
                continue
            key = f"{r['repo']}#{r['number']}"
            d = _days_between(r.get("created_at"), r.get("merged_at"))
            if d is not None:
                merges[key] = min(merges.get(key, 1e9), d)
    return firsts, mine, merges


def weekly_stats(this_week_snaps, prior_week_snaps):
    firsts, mine, merges = collect_durations(this_week_snaps)
    stats = {
        "first_review": median(firsts.values()),
        "your_review": median(mine.values()),
        "merge": median(merges.values()),
        "prior_first_review": None,
        "prior_your_review": None,
        "prior_merge": None,
    }
    if prior_week_snaps:
        p_firsts, p_mine, p_merges = collect_durations(prior_week_snaps)
        stats["prior_first_review"] = median(p_firsts.values())
        stats["prior_your_review"] = median(p_mine.values())
        stats["prior_merge"] = median(p_merges.values())
    return stats


def _arrow(now, before):
    if now > before:
        return "▲"
    if now < before:
        return "▼"
    return "–"


def trend_line(total, stale, prev_total, prev_stale):
    return (
        f"open {_arrow(total, prev_total)} {total} (was {prev_total}) · "
        f"stale {_arrow(stale, prev_stale)} {stale} (was {prev_stale})"
    )


# Dashboard
def svg_line(points, width=640, height=170, color="#1d4ed8", label="", empty_msg="No data yet."):
    """points: [(label, value)] — a dependency-free inline SVG polyline."""
    if not points:
        return f"<p style='color:#6b7280'>{esc(empty_msg)}</p>"
    vals = [v for _, v in points]
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    step = (width - 60) / max(len(points) - 1, 1)
    coords = [(30 + i * step, 140 - (v - lo) / rng * 110) for i, (_, v) in enumerate(points)]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    every = max(len(points) // 8, 1)
    labels = "".join(
        f'<text x="{30 + i * step:.0f}" y="160" font-size="9" fill="#6b7280" '
        f'text-anchor="middle">{esc(lbl)}</text>'
        for i, (lbl, _) in enumerate(points)
        if i % every == 0
    )
    title = f'<text x="30" y="14" font-size="11" fill="#374151">{esc(label)}</text>' if label else ""
    value = (
        f'<text x="{min(coords[-1][0] + 7, width - 26):.1f}" '
        f'y="{max(coords[-1][1] - 8, 12):.1f}" font-size="12" font-weight="bold" '
        f'fill="{color}">{esc(f"{vals[-1]:g}")}</text>'
    )
    return (
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">'
        f"{title}<polyline points=\"{poly}\" fill=\"none\" stroke=\"{color}\" "
        f'stroke-width="2"/>'
        f'<circle cx="{coords[-1][0]:.1f}" cy="{coords[-1][1]:.1f}" r="3" fill="{color}"/>'
        f"{value}{labels}</svg>"
    )


def build_dashboard(snapshots, prs_now, first_review_trend, now=None):
    """snapshots: all day files sorted by date. prs_now: today's PR dicts."""
    total = len(prs_now)
    stale_now = sum(1 for p in prs_now if p["bucket"] in ("stale", "escalate"))
    if now is not None:
        updated = now.strftime("%A, %d %B %Y %H:%M UTC")
    elif snapshots and snapshots[-1].get("generated_at"):
        updated = snapshots[-1]["generated_at"]
    else:
        updated = "unknown"
    days = [(s["date"], len(s.get("prs", []))) for s in snapshots]
    debt = [
        (s["date"], sum(1 for p in s.get("prs", []) if p["bucket"] in ("stale", "escalate")))
        for s in snapshots
    ]
    per_repo = {}
    for p in prs_now:
        per_repo[p["repo"]] = per_repo.get(p["repo"], 0) + 1
    repo_rows = "".join(
        f"<tr><td style='padding:4px 10px'>{esc(r.split('/')[-1])}</td>"
        f"<td style='padding:4px 10px'><div style='background:#1d4ed8;height:12px;"
        f"width:{min(n * 30, 400)}px'></div></td><td style='padding:4px 10px'>{n}</td></tr>"
        for r, n in sorted(per_repo.items(), key=lambda kv: -kv[1])
    )
    stuck = sorted(prs_now, key=lambda p: -p["waiting"])[:10]
    stuck_rows = "".join(
        f"<li>{esc(p['repo'].split('/')[-1])} #{p['number']} — waiting {p['waiting']:.0f}d</li>"
        for p in stuck
        if p["waiting"] >= STALE_DAYS
    )
    debt_word = "healthy" if stale_now == 0 else "needs attention"
    return f"""<html><head><meta charset="utf-8"><title>PR digest dashboard</title></head>
<body style="font-family:-apple-system,Segoe UI,sans-serif;max-width:820px;margin:24px auto">
<h2>PR digest dashboard</h2>
<p style="color:#6b7280">Data as of {esc(updated)} · today: {total} open,
{stale_now} stale or blocked ({debt_word})</p>
<h3>Open PRs <small style="color:#6b7280;font-weight:normal">— today: {total}</small></h3>
<p style="color:#6b7280;font-size:13px;margin:4px 0">Each point is the total open
(non-draft) PRs across all repos that day. Rising fast means PRs are arriving
faster than they close.</p>
{svg_line(days, label="open PRs per day", empty_msg="Needs a few days of history — one dot per day builds the line.")}
<h3>Review debt <small style="color:#6b7280;font-weight:normal">— today: {stale_now}</small></h3>
<p style="color:#6b7280;font-size:13px;margin:4px 0">PRs waiting more than 3 days
for review. Flat at zero is the goal; a rising line means reviews are falling behind.</p>
{svg_line(debt, color="#b91c1c", label="stale + blocked per day", empty_msg="Needs a few days of history — one dot per day builds the line.")}
<h3>Per-repo open PRs</h3>
<p style="color:#6b7280;font-size:13px;margin:4px 0">Where the current open PRs sit.</p>
<table>{repo_rows or '<tr><td>No open PRs</td></tr>'}</table>
<h3>Median time-to-first-review (rolling week)</h3>
<p style="color:#6b7280;font-size:13px;margin:4px 0">Median days from PR open to
first human review, over a rolling 7-day window. Lower is faster.</p>
{svg_line(first_review_trend, color="#15803d", label="days", empty_msg="No data yet — fills in as PRs get their first review.")}
<h3>Most stuck (current)</h3>
<p style="color:#6b7280;font-size:13px;margin:4px 0">PRs waiting more than 3 days
right now, longest wait first.</p>
<ul>{stuck_rows or '<li>Nothing stuck — every open PR is under 3 days.</li>'}</ul>
</body></html>"""


def bucket(pr):
    if pr["waiting"] >= ESCALATE_DAYS:
        return "escalate"
    if pr["waiting"] >= STALE_DAYS:
        return "stale"
    if pr["waiting"] >= 1:
        return "warm"
    return "fresh"


BUCKET_META = {
    "escalate": ("⚫", f"Blocked &gt; {ESCALATE_DAYS} days", "#7c2d12"),
    "stale": ("🔴", f"Stale &gt; {STALE_DAYS} days", "#b91c1c"),
    "warm": ("🟡", "Waiting 1–3 days", "#b45309"),
    "fresh": ("🟢", "Opened in the last 24h", "#15803d"),
}

BUCKET_ORDER = ["escalate", "stale", "warm", "fresh"]


def esc(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def since_yesterday(current_prs, prev_snapshot, resolved):
    """Change summary vs the previous day file. None when there is no baseline."""
    if prev_snapshot is None:
        return None
    prev = {f"{p['repo']}#{p['number']}": p for p in prev_snapshot.get("prs", [])}
    cur = {f"{p['repo']}#{p['number']}": p for p in current_prs}
    new = sorted(set(cur) - set(prev))
    reviewed = [
        k for k in sorted(set(cur) & set(prev))
        if not prev[k].get("first_review_at") and cur[k].get("first_review_at")
    ]
    slipped = [
        k for k in sorted(set(cur) & set(prev))
        if BUCKET_ORDER.index(bucket(cur[k])) < BUCKET_ORDER.index(prev[k]["bucket"])
    ]
    return {
        "new": new,
        "reviewed": reviewed,
        "merged": sum(1 for r in resolved if r["resolution"] == "merged"),
        "closed": sum(1 for r in resolved if r["resolution"] == "closed_unmerged"),
        "slipped": slipped,
    }


def _badge(label, bg):
    return (
        f' <span style="background:{bg};color:#fff;padding:1px 5px;'
        f'border-radius:3px;font-size:11px">{label}</span>'
    )


CI_BADGE = {
    "success": ("CI ✓", "#15803d"),
    "failure": ("CI ✗", "#b91c1c"),
    "pending": ("CI …", "#b45309"),
    "neutral": ("CI –", "#6b7280"),
}


def row(pr):
    badges = ""
    if pr["yours"]:
        badges += _badge("YOU", "#1d4ed8")
    ci = CI_BADGE.get(pr.get("ci"))
    if ci:
        badges += _badge(ci[0], ci[1])
    badges += _badge(f"+{pr.get('additions', 0)} −{pr.get('deletions', 0)}", "#475569")
    if pr.get("mergeable") is False:
        badges += _badge("⚠ conflicts", "#b91c1c")
    elif pr.get("mergeable") is None:
        badges += _badge("merge unknown", "#6b7280")
    unreviewed = "" if pr.get("reviewed", bool(pr.get("first_review_at"))) else " · <em>no review yet</em>"
    reviewers = ", ".join(pr["reviewers"]) or "—"
    you_reviewed_tick = (
        " · <span style=\"color:#15803d\">✓ you reviewed</span>"
        if pr.get("your_last_review_sha")
        and pr.get("head_sha")
        and pr["your_last_review_sha"] == pr["head_sha"]
        else ""
    )
    return f"""
    <tr>
      <td style="padding:8px 10px;border-bottom:1px solid #e5e7eb">
        <a href="{pr['url']}" style="color:#1d4ed8;text-decoration:none;font-weight:600">
          {esc(pr['repo'].split('/')[-1])} #{pr['number']}</a>{badges}<br>
        <span style="color:#111827">{esc(pr['title'])}</span><br>
        <span style="color:#6b7280;font-size:12px">
          by {esc(pr['author'])} → {esc(pr['base'])} ·
          waiting {pr['waiting']:.1f}d · reviewers: {esc(reviewers)}{you_reviewed_tick}{unreviewed}
        </span>
      </td>
    </tr>"""


def build_html(
    cfg, all_prs, since, trend=None, stats=None, quiet_repos=None, errors=None
):
    buckets = {k: [] for k in BUCKET_META}
    for pr in all_prs:
        buckets[bucket(pr)].append(pr)

    date_line = (
        f'{cfg.now.strftime("%A, %d %B %Y")} · {len(all_prs)} open PR(s) across '
        f'{len(cfg.repos)} repos'
        if cfg is not None
        else f'{len(all_prs)} open PR(s)'
    )
    parts = [
        '<div style="font-family:-apple-system,Segoe UI,sans-serif;max-width:760px">',
        f'<h2 style="margin:0 0 4px">PR review digest</h2>',
        f'<p style="color:#6b7280;margin:0 0 18px">'
        f'{date_line}</p>',
    ]

    if since:
        bits = []
        if since["new"]:
            bits.append(f"<b>+{len(since['new'])} new</b>")
        if since["reviewed"]:
            bits.append(f"✅ {len(since['reviewed'])} reviewed")
        if since["merged"]:
            bits.append(f"🔀 {since['merged']} merged")
        if since["closed"]:
            bits.append(f"❌ {since['closed']} closed")
        if since["slipped"]:
            bits.append(f"⬇ {len(since['slipped'])} slipped")
        if bits:
            parts.append(
                '<p style="background:#eef2ff;border-radius:6px;padding:10px 14px;'
                'font-size:13px;margin:0 0 14px">Since yesterday: ' + " · ".join(bits) + "</p>"
            )

    you_prs = sorted(
        [p for p in all_prs if waiting_on_you(p)], key=lambda p: -p["waiting"]
    )
    if you_prs:
        parts.append(
            f'<h3 style="color:#7c2d12;margin:20px 0 6px;font-size:15px">'
            f"⚡ Waiting on you ({len(you_prs)})</h3>"
            '<table style="width:100%;border-collapse:collapse;font-size:14px">'
            + "".join(row(p) for p in you_prs)
            + "</table>"
        )

    if not all_prs and not errors:
        parts.append(
            '<p style="padding:14px;background:#f0fdf4;border-radius:6px">'
            "✅ Nothing waiting. Every repo is clear.</p>"
        )

    # Most urgent first — that ordering is the whole point of the digest.
    for key in ("escalate", "stale", "warm", "fresh"):
        prs = sorted(buckets[key], key=lambda p: -p["waiting"])
        if not prs:
            continue
        icon, label, color = BUCKET_META[key]
        parts.append(
            f'<h3 style="color:{color};margin:20px 0 6px;font-size:15px">'
            f"{icon} {label} ({len(prs)})</h3>"
            '<table style="width:100%;border-collapse:collapse;font-size:14px">'
            + "".join(row(p) for p in prs)
            + "</table>"
        )

    if trend:
        parts.append(
            '<p style="color:#374151;font-size:13px;margin:0 0 10px">📈 ' + trend + "</p>"
        )

    if (
        cfg is not None
        and cfg.now.weekday() == 4
        and stats
        and stats.get("first_review") is not None
    ):
        def _fmt(label, val, prior):
            if val is None:
                return ""
            extra = f" (prior week {prior:.1f}d)" if prior is not None else ""
            return f"{label} {val:.1f}d{extra} · "

        summary = (
            _fmt("time-to-first-review", stats.get("first_review"), stats.get("prior_first_review"))
            + _fmt("time-to-my-review", stats.get("your_review"), stats.get("prior_your_review"))
            + _fmt("time-to-merge", stats.get("merge"), stats.get("prior_merge"))
        ).rstrip(" ·")
        parts.append(
            '<p style="background:#fffbeb;border-radius:6px;padding:10px 14px;'
            f'font-size:13px;margin:20px 0 0"><b>This week</b> — {summary}</p>'
        )

    if cfg.dashboard_url:
        parts.append(
            f'<p style="font-size:13px;margin-top:22px">📈 <a href="{esc(cfg.dashboard_url)}" '
            f'style="color:#1d4ed8">Review-debt dashboard</a></p>'
        )

    if quiet_repos:
        parts.append(
            '<p style="color:#6b7280;font-size:12px;margin-top:22px">'
            "No open PRs: " + ", ".join(esc(r.split("/")[-1]) for r in quiet_repos) + "</p>"
        )

    if errors:
        rows = "".join(
            f"<li>{esc(repo)} — {esc(err)}</li>" for repo, err in errors
        )
        parts.append(
            '<p style="color:#b91c1c;font-size:12px;margin-top:10px">'
            f"Could not read:<ul>{rows}</ul></p>"
        )

    parts.append("</div>")
    return "\n".join(parts)


def main():
    cfg = load_config()
    all_prs, quiet_repos, errors = [], [], []

    for repo in cfg.repos:
        prs, err = collect(cfg, repo)
        if err:
            errors.append((repo, err))
            continue
        if prs:
            all_prs.extend(prs)
        else:
            quiet_repos.append(repo)

    baseline = read_baseline(cfg)
    current = state_for_baseline(all_prs, errors)
    send, reason = should_send(cfg, current, baseline)
    print(f"send={send} ({reason})")

    prev_snap = previous_snapshot(cfg)
    current_keys = {pr_key(p) for p in all_prs}
    resolved = resolve_disappeared(cfg, prev_snap, current_keys) if prev_snap else []
    snapshot = build_snapshot(cfg, all_prs, resolved, errors)
    with open("snapshot.json", "w") as fh:
        json.dump(snapshot, fh, indent=1)

    week_snaps = [
        s for s in (read_snapshot(cfg, cfg.now.date() - _timedelta(days=o))
                    for o in range(0, 7))
        if s is not None and s.get("date") != snapshot["date"]
    ]
    week_snaps.append(snapshot)
    prior_snaps = [
        s for s in (read_snapshot(cfg, cfg.now.date() - _timedelta(days=o))
                    for o in range(7, 14))
        if s is not None
    ]
    stats = weekly_stats(week_snaps, prior_snaps)
    last_week = read_snapshot(cfg, cfg.now.date() - _timedelta(days=7))
    trend = None
    if last_week is not None:
        stale_now = sum(1 for p in all_prs if p["waiting"] >= STALE_DAYS)
        lw_stale = sum(1 for p in last_week["prs"] if p["bucket"] in ("stale", "escalate"))
        trend = trend_line(len(all_prs), stale_now, len(last_week["prs"]), lw_stale)

    all_snaps = []
    if cfg.data_dir.exists():
        for f in sorted((cfg.data_dir / "snapshots").glob("*.json")):
            all_snaps.append(json.loads(f.read_text()))
    all_snaps = [s for s in all_snaps if s.get("date") != snapshot["date"]]
    all_snaps.append(snapshot)
    first_review_trend = []
    for i in range(len(all_snaps)):
        window = all_snaps[max(i - 6, 0) : i + 1]
        firsts, _, _ = collect_durations(window)
        m = median(firsts.values())
        if m is not None:
            first_review_trend.append((all_snaps[i]["date"][5:], round(m, 2)))
    with open("index.html", "w") as fh:
        fh.write(build_dashboard(all_snaps, all_prs, first_review_trend))

    # Escalation pings — only for PRs past the threshold, once per window.
    pinged = 0
    for pr in all_prs:
        if pr["waiting"] < ESCALATE_DAYS:
            continue
        if cfg.dry_run:
            print(f"[dry-run] would ping {pr['repo']}#{pr['number']}")
            continue
        try:
            if not already_pinged(cfg, pr["repo"], pr["number"]):
                ping(cfg, pr["repo"], pr["number"], pr["waiting"])
                pinged += 1
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            print(f"ping failed for {pr['repo']}#{pr['number']}: {exc}")

    since = since_yesterday(all_prs, prev_snap, resolved)
    html = build_html(cfg, all_prs, since, trend, stats, quiet_repos, errors)
    with open("digest.html", "w") as fh:
        fh.write(html)

    with open("last-sent.json", "w") as fh:
        json.dump({**current, "sent_at": cfg.now.isoformat()}, fh, indent=1)

    stale = sum(1 for p in all_prs if p["waiting"] >= STALE_DAYS)
    subject = f"PR digest — {len(all_prs)} open"
    if stale:
        subject += f", {stale} stale"

    # Surface state to the workflow: it owns sending, pushing, and pass/fail.
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a") as fh:
            fh.write(f"total={len(all_prs)}\n")
            fh.write(f"stale={stale}\n")
            fh.write(f"subject={subject}\n")
            fh.write(f"errors={'true' if errors else 'false'}\n")
            fh.write(f"dry_run={'true' if cfg.dry_run else 'false'}\n")
            fh.write(f"morning_run={'true' if cfg.now.hour == 3 else 'false'}\n")
            fh.write(f"should_send={'true' if send else 'false'}\n")

    print(f"{len(all_prs)} open PR(s), {stale} stale, {pinged} pinged, "
          f"{len(errors)} repo error(s)")
    # Never exit non-zero — the workflow's fail gate handles that.
    return 0


if __name__ == "__main__":
    sys.exit(main())
