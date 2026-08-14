# pr-digest

Daily email digest of open PRs across the Scikraft repos, aged into buckets so
nothing sits unreviewed. Runs as a scheduled GitHub Action.

Complements `pr-sentinel`, which *reviews* PRs. This one only *reports* on them.

## What it does

Every weekday at **09:00 IST** it reads all seven repos, skips drafts, and
sorts each open PR by how long it has been waiting — measured from the last
human review, or from when it opened if nobody has reviewed yet.

| Bucket | Threshold | Action |
|---|---|---|
| ⚫ Blocked | > 7 days | Top of the email **and** an auto-comment on the PR |
| 🔴 Stale | > 3 days | Flagged in the email |
| 🟡 Waiting | 1–3 days | Listed |
| 🟢 Fresh | < 24h | Listed last |

PRs where you are a requested reviewer get a **YOU** badge. Repos with no open
PRs collapse to a single line at the bottom.

The auto-comment is idempotent — a hidden marker in the comment body stops it
re-pinging the same PR every morning.

## Repos watched

Edit `REPOS` in `scripts/digest.py`.

## Secrets required

| Secret | What |
|---|---|
| `PR_SCAN_TOKEN` | Classic PAT, scopes `repo` + `read:org`. Classic, not fine-grained — the repos span two owners (`Scikraft-Edu-Engg-Design` and `scikraft-eed`) and a fine-grained PAT is locked to one. |
| `MAIL_USER` | Gmail address used to send |
| `MAIL_PASS` | Gmail **app password**, not the account password |
| `MAIL_TO` | Where the digest lands |

## Run it locally

```bash
PR_SCAN_TOKEN="$(gh auth token)" DRY_RUN=true python3 scripts/digest.py
```

`DRY_RUN=true` builds `digest.html` and prints which PRs *would* be pinged
without posting anything. Drop it to post for real.

To trigger in CI without waiting for the cron, use **Actions → PR review digest
→ Run workflow**; the manual run defaults to dry-run.

## Notes

- Ageing is in calendar days, so a PR opened Friday reads as 3 days old on Monday.
- The script exits non-zero if any repo cannot be read, so a revoked token
  fails the run loudly instead of emailing a silently short list.
