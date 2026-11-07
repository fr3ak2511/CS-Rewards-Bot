# Independent Morning Primary Scheduler

## Purpose

GitHub scheduled events are best-effort and have repeatedly arrived hours late. This external trigger starts the existing production `schedule.yml` workflow through GitHub's `workflow_dispatch` API at 05:30 IST. GitHub Actions then retains the existing randomized 05:35–06:00 IST wait, full-roster state check, claim concurrency group, reporting, and runtime-state commit.

The existing GitHub cron schedule and five-minute reset-window fallback remain enabled as backups.

## External service

Use cron-job.org (free tier). It supports a configured time zone, POST requests, custom request headers, a JSON body, and an execution history. Its punctuality is best-effort rather than guaranteed.

## GitHub token

Create a fine-grained personal access token with:

- Resource owner: the owner of `CS-Rewards-Bot`.
- Repository access: **Only select repositories** → `CS-Rewards-Bot`.
- Repository permissions: **Actions: Read and write**.
- No Contents, Administration, Pull requests, or other permissions.
- Set an expiry and store the token only in cron-job.org's custom `Authorization` header.

Do not commit the token to this repository, place it in a workflow file, or include it in screenshots or support messages. Rotate/revoke it if it is exposed.

## cron-job.org request

Create one enabled job with these settings:

- Title: `CS Rewards Bot — Morning Primary`
- URL: `https://api.github.com/repos/fr3ak2511/CS-Rewards-Bot/actions/workflows/schedule.yml/dispatches`
- Request method: `POST`
- Time zone: `Asia/Kolkata`
- Hours: `5`
- Minutes: `30`
- Days of month: every day
- Months: every month
- Days of week: every day
- Save execution responses and enable failure notifications.

Headers:

- `Accept: application/vnd.github+json`
- `Authorization: Bearer <FINE_GRAINED_GITHUB_TOKEN>`
- `X-GitHub-Api-Version: 2026-03-10`
- `Content-Type: application/json`

JSON body:

```json
{
  "ref": "prod",
  "inputs": {
    "external_primary": "true"
  },
  "return_run_details": true
}
```

A successful GitHub dispatch returns HTTP 200 with run details when requested (or HTTP 204 if the response body is not requested). A failed request must generate a failure notification.

## Duplicate-run and state safeguards

The external dispatch is explicitly marked with `external_primary=true`.

- It uses the existing `prod` branch only.
- It waits until the same deterministic 05:35–06:00 IST primary target as the existing scheduled dispatcher.
- If a scheduled dispatcher is merely sleeping in that wait, the external dispatch is allowed to proceed. Its dependent, queued claim job does not yet hold the claimer concurrency slot.
- A genuinely queued or running claim job blocks another external dispatch.
- The run-mode gate checks `bot_meta.json` and `players.csv` again after the wait. If a complete 35/35 full-claim roster cycle already started after today's 05:30 reset, it skips without claiming.
- The claimer labels this run `Primary Run`, not `Manual Run`.
- The existing GitHub scheduled dispatcher and fallback remain active. Their own run-mode checks suppress a second full roster cycle after the external run completes.

## Safe initial test

After this change has been merged into `prod`, execute the cron-job.org job once manually while a complete post-reset full-roster cycle is already recorded for the current day. The expected outcome is a successful workflow dispatch whose `Run Master Claimer` job reports that external primary was skipped because today's full-roster cycle already ran. It must not perform any reward claims during this test.

After confirming the dispatch and skip in GitHub Actions, leave the daily 05:30 IST external schedule enabled. Check the next morning's run and confirm that it starts within the existing 05:35–06:00 IST window.

## Operational caveat

cron-job.org states that it aims to minimize delays but does not guarantee punctual execution. Treat it as an independent second scheduling path, not a hard real-time service. Keep the native GitHub schedule and fallback enabled and monitor both execution histories.
