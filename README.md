# pr-digest

PR Digest runs every 2 hours on weekdays (09:00–19:00 IST). It emails on change
— always at 09:00 — tracks review debt in daily JSON snapshots, and publishes
a small GitHub Pages dashboard.

Complements `pr-sentinel`, which *reviews* PRs. This one only *reports* on them.

## What it does

Each run reads all seven repos, skips drafts, and sorts each open PR by how
long it has been waiting — measured from the last human review, or from when
it opened if nobody has reviewed yet.

| Bucket | Threshold | Action |
|---|---|---|
| ⚫ Blocked | > 7 days | Top of the email **and** an auto-comment on the PR |
| 🔴 Stale | > 3 days | Flagged in the email |
| 🟡 Waiting | 1–3 days | Listed |
| 🟢 Fresh | < 24h | Listed last |

The email starts with **⚡ Waiting on you**: PRs requesting your review, or with
no reviewer assigned, that you have not reviewed. A PR returns to this queue
when its author pushes after your last review. GitHub removes you from requested
reviewers after a review, so the digest compares that commit with the current head.

Under the header, **Since yesterday** counts new, reviewed, merged, closed, and
slipped-bucket PRs. Fridays add median time-to-first-review, time-to-your-review,
and time-to-merge against the prior week. The 09:00 run rebuilds the dashboard.

The 09:00 run always emails. The other runs email only when something changed:
a new or resolved PR, a bucket move, a waiting-on-you arrival, or a repo error.
PRs where you are a requested reviewer get a **YOU** badge. Repos with no open
PRs collapse to a single line at the bottom.

The auto-comment is idempotent — a hidden marker in the comment body stops it
re-pinging the same PR every morning.

## Repos watched

Edit `DEFAULT_REPOS` in `scripts/digest.py`.

## One-time setup

```bash
git switch --orphan data && mkdir -p snapshots && echo '{}' > last-sent.json
git add . && git commit -m "Seed data branch" && git push -u origin data
git checkout main
git switch --orphan pages && echo '<html><title>PR digest</title></html>' > index.html
git add . && git commit -m "Seed pages branch" && git push -u origin pages
git checkout main
```

Then go to repo **Settings → Pages → Deploy from branch** and select `pages` /
`/ (root)`.

## Secrets required

| Secret | What |
|---|---|
| `PR_SCAN_TOKEN` | Classic PAT, scopes `repo` + `read:org`. Classic, not fine-grained — the repos span two owners (`Scikraft-Edu-Engg-Design` and `scikraft-eed`) and a fine-grained PAT is locked to one. |
| `MAIL_USER` | Gmail address used to send |
| `MAIL_PASS` | Gmail **app password**, not the account password |
| `MAIL_TO` | Where the digest lands |

The workflow pushes snapshots and the dashboard with the built-in
`GITHUB_TOKEN` (`contents: write`). `PR_SCAN_TOKEN` is used for reads only.

## Run it locally

```bash
PR_SCAN_TOKEN="$(gh auth token)" DRY_RUN=true DATA_DIR=./tmp-data python3 scripts/digest.py
```

This writes `digest.html`, `snapshot.json`, `last-sent.json`, and `index.html` to
the working directory, and pushes and posts nothing. It reads prior snapshots
and the send baseline from `DATA_DIR`.

Run the tests with `pip install -r requirements-dev.txt && pytest`.

To trigger in CI without waiting for the cron, use **Actions → PR review digest
→ Run workflow**; the manual run defaults to dry-run.

## Notes

- Ageing is in calendar days, so a PR opened Friday reads as 3 days old on Monday.
- The script never exits non-zero; the workflow's final step fails the run when
  any repo could not be read, after the email has had its chance to go out.
- A PR opened and merged between two runs is never recorded — an accepted blind
  spot that only under-counts resolved PRs.
