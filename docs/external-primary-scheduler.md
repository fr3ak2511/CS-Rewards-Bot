# Independent Morning Primary Scheduler

## Purpose

GitHub scheduled events are best-effort and have repeatedly arrived hours late. The independent external trigger starts the existing production `schedule.yml` workflow through GitHub's `workflow_dispatch` API at **05:35 IST**, allowing five minutes after the 05:30 server reboot.

For this external path, GitHub Actions proceeds after its preflight checks without the randomized wait. The existing native GitHub dispatcher retains its randomized 05:35–06:00 IST wait, and the five-minute reset-window fallback remains enabled as a backup. Both paths share the same full-roster coverage gate and claim concurrency protection.

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
- Minutes: `35`
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
- It is dispatched at 05:35 IST, five minutes after the server reboot, and proceeds after preflight rather than waiting for the native dispatcher's randomized window.
- If a scheduled dispatcher is merely sleeping in that wait, the external dispatch is allowed to proceed. Its dependent, queued claim job does not yet hold the claimer concurrency slot.
- A genuinely queued or running claim job blocks another external dispatch.
- The external run-mode gate rechecks production state immediately before the claim job starts; if today's post-reset full-roster cycle is already complete, it skips safely.
- The run-mode gate uses `bot_meta.json` and `players.csv` to verify the current configured roster against today's 05:30 reset; if that cycle is already complete, it skips without claiming.
- The native 03:00 dispatcher now performs the same committed-coverage check before starting its long randomized wait. If today's full-roster cycle has already completed, it exits during preflight instead of creating a redundant delayed run.
- The native preflight also checks queued/running claim jobs, including an active external primary. If runtime state is unreadable or malformed, it fails open and retains the native backup; the in-job run-mode gate remains the final safety check.
- The external primary is still allowed to pass a native dispatcher whose claim job is queued only because its randomized delay is actively running.
- The claimer labels the external run `Primary Run`, not `Manual Run`.
- The native GitHub dispatcher and reset-window fallback remain enabled when the external trigger has not completed a full-roster cycle.

## Safe initial test

After this change has been merged into `prod`, execute the cron-job.org job once manually while a complete post-reset full-roster cycle is already recorded for the current day. The expected outcome is a successful workflow dispatch whose `Run Master Claimer` job reports that external primary was skipped because today's full-roster cycle already ran. It must not perform any reward claims during this test.

After confirming the dispatch and skip in GitHub Actions, leave the daily 05:35 IST external schedule enabled. Check the next morning's run and confirm that the claim job starts shortly after the external dispatch and preflight.

## Operational caveat

cron-job.org states that it aims to minimize delays but does not guarantee punctual execution. Treat it as an independent second scheduling path, not a hard real-time service. Keep the native GitHub schedule and fallback enabled and monitor both execution histories.
