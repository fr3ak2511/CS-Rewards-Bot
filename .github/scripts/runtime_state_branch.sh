#!/usr/bin/env bash
# Load and persist the bot's four durable runtime JSON files on the dedicated
# runtime-state branch. Production source and roster remain checked out from prod.
set -euo pipefail
STATE_BRANCH="${RUNTIME_STATE_BRANCH:-runtime-state}"
STATE_FILES=("claim_history.json" "bot_meta.json" "grenade_balance_baselines.json" "portal_claim_audit.json")
fail() { echo "ERROR: $*" >&2; exit 1; }
fetch_state_branch() {
  git fetch --no-tags origin "+refs/heads/${STATE_BRANCH}:refs/remotes/origin/${STATE_BRANCH}"
}
load_state() {
  fetch_state_branch
  local remote_ref="refs/remotes/origin/${STATE_BRANCH}"
  for file in "${STATE_FILES[@]}"; do
    git cat-file -e "${remote_ref}:${file}" || fail "Required runtime-state file is missing: ${file}"
    local temp_file="${file}.runtime-state-tmp"
    git show "${remote_ref}:${file}" > "${temp_file}"
    mv -- "${temp_file}" "${file}"
  done
  python - <<'PY'
import json
from pathlib import Path
for name in ("claim_history.json", "bot_meta.json", "grenade_balance_baselines.json", "portal_claim_audit.json"):
    with Path(name).open("r", encoding="utf-8") as handle:
        json.load(handle)
PY
  echo "Loaded all four authoritative runtime-state files from ${STATE_BRANCH}."
}
save_state() {
  local source_root="${GITHUB_WORKSPACE:-$PWD}"
  local runner_temp="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
  local run_suffix="${GITHUB_RUN_ID:-$$}"
  local worktree="${runner_temp}/cs-rewards-runtime-state-${run_suffix}"
  local local_branch="runtime-state-save-${run_suffix}"
  mkdir -p -- "${runner_temp}"
  [[ ! -e "${worktree}" ]] || fail "Runtime-state worktree path already exists: ${worktree}"
  fetch_state_branch
  git worktree add --track -b "${local_branch}" "${worktree}" "refs/remotes/origin/${STATE_BRANCH}"
  cleanup_worktree() { git worktree remove --force "${worktree}" >/dev/null 2>&1 || true; }
  trap cleanup_worktree EXIT
  for file in "${STATE_FILES[@]}"; do
    [[ -f "${source_root}/${file}" ]] || fail "Updated runtime-state file is missing: ${source_root}/${file}"
    cp -- "${source_root}/${file}" "${worktree}/${file}"
  done
  git -C "${worktree}" config user.name "github-actions[bot]"
  git -C "${worktree}" config user.email "41898282+github-actions[bot]@users.noreply.github.com"
  git -C "${worktree}" add -- "${STATE_FILES[@]}"
  if git -C "${worktree}" diff --cached --quiet; then
    echo "No runtime-state changes to commit."
    exit 0
  fi
  git -C "${worktree}" commit -m "bot: update runtime state [skip ci]"
  for attempt in 1 2 3; do
    echo "Runtime-state push attempt ${attempt}/3"
    fetch_state_branch
    if ! git -C "${worktree}" rebase "refs/remotes/origin/${STATE_BRANCH}"; then
      echo "Rebase conflict while saving runtime state; aborting and reporting a visible failure." >&2
      git -C "${worktree}" rebase --abort || true
      exit 1
    fi
    if git -C "${worktree}" push origin "HEAD:refs/heads/${STATE_BRANCH}"; then
      echo "Runtime state committed to ${STATE_BRANCH}."
      exit 0
    fi
    echo "Push failed; refreshing ${STATE_BRANCH} before the next attempt."
  done
  fail "Could not save runtime state to ${STATE_BRANCH} after three attempts."
}
case "${1:-}" in
  load) load_state ;;
  save) save_state ;;
  *) fail "Usage: $0 {load|save}" ;;
esac
