# PR Digest v2 — trends, 2-hour cadence, and de-bottlenecking

Date: 2026-08-29
Status: approved design, review round 1 applied, pending implementation plan
Approach: B (git-as-DB + GitHub Pages dashboard), extended with the 2-hour cadence and a "waiting on you" priority queue.

Changelog:
- 2026-08-29 r1 — script no longer pushes or exits early (workflow owns both);
  waiting-on-you survives re-reviews via review `commit_id` vs `head.sha`;
  `resolved` array added for merged/closed PRs; CI sourced from check-runs
  (second extra call); config/NOW moved into `main()` for testability; pushes
  use `GITHUB_TOKEN`, PAT is read-only; dashboard rebuilt on the morning run
  only; send triggers trimmed; DRY_RUN skips pushes, `DATA_DIR` for local runs.

## Context

`pr-digest` is a personal triage tool. It runs on a weekday cron (09:00 IST),
ages open non-draft PRs across seven repos into four buckets, emails an HTML
digest, and auto-comments once on PRs waiting more than 7 days. It is
stateless and stdlib-only.

Two problems with the current shape:

1. **No memory.** The digest says what is open right now, never whether review
   debt is growing, which repo is the bottleneck, or whether review latency is
   improving.
2. **Once a day is too slow.** Developers raise PRs and wait for Mohan's
   review. If a PR lands at 09:15, he does not see it until the next morning.
   He does not want to be the bottleneck.

## Goals

- Run every 2 hours during working hours; email only when something changed.
- Record a daily snapshot so trends are computable from git history.
- Put "PRs waiting on Mohan" at the top of every email, keep it accurate
  after re-reviews, and measure his own review latency.
- Publish a zero-server dashboard (GitHub Pages, inline SVG, no JS libraries)
  built from the snapshot history.
- Keep everything stdlib-only Python, one script, no external services.

## Non-goals

- Per-developer personalized digests (audience is Mohan only).
- Slack/Teams/Telegram delivery.
- Any database or third-party analytics service.
- Changes to `pr-sentinel` (stays a separate reviewer).

## Architecture

One script (`scripts/digest.py`, grown but still single-file) and one workflow.
All persistence lives on branches, never in `main`. **The script computes and
writes files to the workspace only — it performs no git pushes and never
exits non-zero early. The workflow owns every push and every pass/fail
decision.** This ordering guarantee is what lets the email go out even when a
repo read or a push fails.

```
main
├── scripts/digest.py
└── .github/workflows/pr-digest.yml

data branch  (git-as-DB)
├── snapshots/YYYY-MM-DD.json        ← one file per day, rewritten each run
└── last-sent.json                   ← baseline for send-on-change

pages branch (GitHub Pages)
└── index.html                       ← generated dashboard, inline SVG
```

The workflow checks out `main` at the root and the `data` branch into `data/`
(checkout action, `path: data`). The script reads its baseline from
`DATA_DIR` (default `data`, set to any directory for local runs).

### Run order per invocation

1. **Fetch** — open PRs for all configured repos, plus the extra fields
   listed under "Snapshot schema" (reviews, detail, check-runs per PR).
2. **Diff** — compare current state against `data/last-sent.json` (missing
   file = everything changed) and decide `should_send`.
3. **Write to workspace** — `digest.html`, today's snapshot as
   `snapshot.json`, regenerated dashboard as `index.html`. Emit outputs:
   `should_send`, `subject`, counts, `errors>0`. Exit 0 regardless of repo
   errors; the errors count is carried in an output.
4. **Push snapshot** (workflow step, skipped in DRY_RUN) — commit
   `snapshot.json` into `data/snapshots/` on the data branch. Uses
   `GITHUB_TOKEN` with `contents: write`. A failure here does not skip later
   steps.
5. **Email** (workflow step) — gated on
   `if: always() && steps.build.outputs.should_send == 'true'`.
6. **Update baseline** (workflow step, skipped in DRY_RUN) — only when the
   mail step succeeded, commit `last-sent.json` to the data branch. A failed
   send leaves the baseline stale, so the next run re-sends — at-least-once
   delivery, no lost alerts.
7. **Dashboard** (workflow step, skipped in DRY_RUN) — push `index.html` to
   the pages branch, morning run only (script emits `morning_run=true/false`;
   data is one file per day, so six deploys a day change nothing).
8. **Fail gate** (final workflow step, `if: always()`) — fail the job when
   `steps.build.outputs.errors == 'true'`, so a revoked token still fails the
   run loudly after the email had its chance.

### Send-on-change policy

The morning run (09:00 IST) always sends. The other five runs send only when
one of these changed since the last **sent** digest:

- PR count changed (new PR, or a PR merged / closed / disappeared)
- Any PR moved bucket
- **The waiting-on-you queue gained a PR** (new PR requesting Mohan's
  review, or an existing one needing re-review)
- Any repo read error

No change → silent run (email skipped, snapshot still written).

### Workflow changes

- Cron: `30 3,5,7,9,11,13 * * 1-5` (six runs, 09:00–19:00 IST, weekdays).
- Add `concurrency: { group: pr-digest, cancel-in-progress: false }`.
- `permissions: contents: read` → `contents: write` (approved) — used by
  `GITHUB_TOKEN` for the data and pages branch pushes. `PR_SCAN_TOKEN` stays
  **read-only in practice**: reads only, never a push identity. A PAT push
  would trigger other repos' workflows and carries `repo` scope across both
  orgs; `GITHUB_TOKEN` pushes trigger no recursive workflow runs.
- One-time manual setup, outside the workflow: create orphan `data` and
  `pages` branches (an `actions/checkout` with `ref: data` fails if the
  branch does not exist), and enable GitHub Pages on the pages branch in repo
  settings.

## Snapshot schema

One JSON file per day, last-write-wins across the day's runs. Two top-level
collections:

```json
{
  "date": "2026-08-29",
  "generated_at": "2026-08-29T04:03:11Z",
  "prs": [
    {
      "repo": "Scikraft-Edu-Engg-Design/xperimentor-backend-v3.0",
      "number": 123,
      "title": "...",
      "url": "https://github.com/...",
      "author": "someone",
      "base": "main",
      "head_sha": "abc123...",
      "created_at": "2026-08-25T09:00:00Z",
      "age_days": 4.2,
      "waiting_days": 2.1,
      "first_review_at": null,
      "last_review_at": null,
      "your_last_review_at": null,
      "your_last_review_sha": null,
      "reviewers": ["timmapuramreddy"],
      "yours": true,
      "ci": "failure",
      "additions": 120,
      "deletions": 30,
      "mergeable": null,
      "bucket": "warm"
    }
  ],
  "resolved": [
    {
      "repo": "Scikraft-Edu-Engg-Design/tiqer-standalone-backend",
      "number": 88,
      "resolution": "merged",
      "merged_at": "2026-08-28T14:10:00Z"
    }
  ]
}
```

Field notes:

- `head_sha` comes free from the PR list response.
- `first_review_at` / `last_review_at` / `your_last_review_at` /
  `your_last_review_sha` all come from the reviews endpoint already being
  fetched — the review object carries both `submitted_at` and `commit_id`, so
  re-review detection needs no extra call.
- **Two extra calls per PR**, not one: the PR detail endpoint supplies
  additions/deletions/mergeable (and confirms head_sha), while CI status
  requires `GET /repos/{repo}/commits/{head_sha}/check-runs`. Still far under
  rate limits.
- `mergeable` is frequently `null` while GitHub computes it — render
  "unknown", never assume clean.
- PRs in yesterday's snapshot but absent today are resolved with one API call
  each and recorded in `resolved` as `merged` or `closed_unmerged`.
- **Blind spot, stated plainly:** a PR opened and merged between two runs is
  never seen by any snapshot. At a 2-hour cadence this is rare and only
  under-counts `resolved`; the open-PR data is unaffected.

## Waiting-on-you definition

A PR is in the ⚡ queue when:

- Mohan is a requested reviewer **or** no reviewer is assigned, **and**
- he has never reviewed it, **or** his last review's `commit_id`
  (`your_last_review_sha`) differs from the current `head_sha` — i.e. the
  author pushed after his review.

This matters because GitHub removes a reviewer from `requested_reviewers`
the moment they review; without the sha comparison, a PR with "changes
requested" would leave the queue forever and the queue would rot. Edge case
accepted: if a PR has other reviewers *and* Mohan already reviewed it, he is
neither requested nor unassigned, so it stays out of the queue until
re-requested.

## Email layout (priority order)

1. **Header + since-yesterday strip:** `+new · ✅ reviewed · 🔀 merged ·
   ❌ closed · ⬇ slipped bucket` vs the previous day-file.
2. **⚡ Waiting on you** — per the definition above, sorted by wait time.
   Leads the email; arrivals in this section always trigger a send.
3. Bucket sections ⚫🔴🟡🟢 as today, each row gaining CI / +adds−dels /
   conflict badges (mergeable `null` renders "unknown") and a "you reviewed"
   tick.
4. **Trend line:** open and stale counts vs this day last week (▲/▼), oldest
   open PR.
5. **Friday only — weekly deep-dive**, computed from the last seven
   snapshots: median time-to-first-review, median time-to-Mohan's-review,
   time-to-merge (from `resolved`), this week vs prior week; stuck-PR
   breakdown by repo and by author.
6. Footer: dashboard link, quiet repos, unreadable-repo errors.

## Dashboard (Phase 3)

Generated `index.html` on the pages branch from all snapshots; pure inline
SVG, no JS dependencies. Panels:

- Open-PR count curve and review-debt (stale+escalate) area chart over time
- Per-repo stacked bars of current debt
- Median time-to-first-review and time-to-your-review trends
- 30-day table of PRs that crossed the stale line most often

The email footer links to it.

## Error handling

- **The script never exits non-zero and never pushes.** Repo read errors set
  the `errors` output and are listed in the email body; the run continues so
  the digest still goes out.
- **Email ordering is guaranteed by the workflow**, not the script: mail runs
  with `if: always()`, and the fail gate is the last step. A failed snapshot
  push or a failed baseline commit cannot suppress the email.
- A repo read error forces `should_send=true` — a broken digest is a change
  worth alerting on.
- `last-sent.json` missing (first run, or fresh `DATA_DIR`) → treated as
  "everything changed".
- DRY_RUN skips all pushes and the baseline update; the local baseline is
  whatever exists in `DATA_DIR`.

## Testing and verification

- **Phase 0 prerequisite:** move config (`PR_SCAN_TOKEN`, `VIEWER`, `DRY_RUN`,
  `DATA_DIR`) and `NOW` out of module scope into `main()`, with `NOW`
  injectable — `scripts/digest.py:37-41` currently reads env at import time
  and pytest cannot import the module.
- Pure functions (diff/should-send logic, bucketing, waiting-on-you predicate,
  medians, snapshot read/write) get pytest tests in `tests/` — no network in
  tests, no env vars required to import.
- `DRY_RUN=true DATA_DIR=./tmp-data python3 scripts/digest.py` locally against
  the real token: verifies the email body, the would-send decision, the
  snapshot file, and that nothing is pushed or committed.
- Manual workflow_dispatch run (dry-run default) to verify CI end-to-end,
  including the orphan-branch prerequisites.
- First live run: confirm data branch contents, email, and (Phase 3) Pages URL.

## Phasing

1. **Phase 0** — testability refactor (config/NOW into `main()`) + cadence +
   concurrency + send-on-change + `last-sent.json` baseline + workflow step
   split (push / mail / baseline / dashboard / fail gate). Independent value:
   near-real-time alerts on his review queue.
2. **Phase 1** — snapshot store + extended PR fields (`first_review_at`,
   `your_last_review_*`, CI via check-runs, size, mergeability, `resolved`).
3. **Phase 2** — email sections: since-yesterday header, waiting-on-you with
   re-review detection, badges, trend line, Friday deep-dive.
4. **Phase 3** — Pages dashboard, rebuilt on the morning run only.

Each phase lands and verifies separately; Phases 0–1 are prerequisites for 2;
Phase 3 depends on 1.

## Risks and trade-offs

- **`contents: write` on the workflow** — approved. Only job in the repo with
  push rights; pushes use `GITHUB_TOKEN`, which cannot trigger recursive
  workflow runs and is scoped to this repo. The PAT degrades to read-only.
- **Up to 6 emails on a very noisy day** — acceptable; triggers are coarse
  (state-level, not per-PR-field) and no longer include self-inflicted
  changes (your own review, reviewer-list edits).
- **GitHub cron jitter** (scheduled runs can start minutes late) — harmless at
  a 2-hour cadence.
- **Snapshot rewrites** create one commit per run on the data branch — noisy
  git history but correct data; the branch exists for the machine, not for
  humans.
- **Two extra API calls per PR** — six runs × 7 repos × (list + reviews +
  detail + check-runs) stays far under the 5,000/hour authenticated limit.
- **Blind spot for sub-2-hour-lived PRs** — accepted; see schema notes.
