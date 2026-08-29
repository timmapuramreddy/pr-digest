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
    now: datetime


def load_config(now=None):
    """Read settings from the environment. Injectable `now` keeps tests deterministic."""
    return Config(
        token=os.environ["PR_SCAN_TOKEN"],
        viewer=os.environ.get("VIEWER_LOGIN", "timmapuramreddy"),
        dry_run=os.environ.get("DRY_RUN", "false").lower() == "true",
        repos=list(DEFAULT_REPOS),
        data_dir=Path(os.environ.get("DATA_DIR", "data")),
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
        d = api(cfg, f"/repos/{p['repo']}/pulls/{p['number']}") or {}
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


def since_yesterday(current_prs, prev_snapshot):
    """Change summary vs the previous day file. None when there is no baseline."""
    if prev_snapshot is None:
        return None
    prev = {f"{p['repo']}#{p['number']}": p for p in prev_snapshot.get("prs", [])}
    cur = {f"{p['repo']}#{p['number']}": p for p in current_prs}
    new = sorted(set(cur) - set(prev))
    resolved_by_key = {
        f"{r['repo']}#{r['number']}": r["resolution"]
        for r in prev_snapshot.get("resolved", [])
    }
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
        "merged": sum(1 for v in resolved_by_key.values() if v == "merged"),
        "closed": sum(1 for v in resolved_by_key.values() if v == "closed_unmerged"),
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
    return f"""
    <tr>
      <td style="padding:8px 10px;border-bottom:1px solid #e5e7eb">
        <a href="{pr['url']}" style="color:#1d4ed8;text-decoration:none;font-weight:600">
          {esc(pr['repo'].split('/')[-1])} #{pr['number']}</a>{badges}<br>
        <span style="color:#111827">{esc(pr['title'])}</span><br>
        <span style="color:#6b7280;font-size:12px">
          by {esc(pr['author'])} → {esc(pr['base'])} ·
          waiting {pr['waiting']:.1f}d · reviewers: {esc(reviewers)}{unreviewed}
        </span>
      </td>
    </tr>"""


def build_html(cfg, all_prs, since, quiet_repos, errors):
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

    if not all_prs:
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

    # Escalation pings — only for PRs past the threshold, once per window.
    pinged = 0
    for pr in all_prs:
        if pr["waiting"] < ESCALATE_DAYS:
            continue
        if cfg.dry_run:
            print(f"[dry-run] would ping {pr['repo']}#{pr['number']}")
            continue
        if not already_pinged(cfg, pr["repo"], pr["number"]):
            ping(cfg, pr["repo"], pr["number"], pr["waiting"])
            pinged += 1

    since = since_yesterday(all_prs, prev_snap)
    html = build_html(cfg, all_prs, since, quiet_repos, errors)
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
