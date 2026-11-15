#!/usr/bin/env bash
# Load and persist the bot's four durable runtime JSON files on the dedicated
# runtime-state branch. Production source and roster remain checked out from prod.
set -euo pipefail

STATE_BRANCH="${RUNTIME_STATE_BRANCH:-runtime-state}"
STATE_FILES=("claim_history.json" "bot_meta.json" "grenade_balance_baselines.json" "portal_claim_audit.json")

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

fetch_state_branch() {
  git fetch --no-tags origin "+refs/heads/${STATE_BRANCH}:refs/remotes/origin/${STATE_BRANCH}"
}

validate_state_files() {
  local root="$1"
  if ! python3 - "$root" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
names = (
    "claim_history.json",
    "bot_meta.json",
    "grenade_balance_baselines.json",
    "portal_claim_audit.json",
)
for name in names:
    with (root / name).open("r", encoding="utf-8") as handle:
        json.load(handle)
PY
  then
    fail "one or more runtime-state files are not valid JSON; refusing to continue"
  fi
}

load_state() {
  fetch_state_branch
  local remote_ref="refs/remotes/origin/${STATE_BRANCH}"
  local state_sha
  state_sha="$(git rev-parse "${remote_ref}")"

  for file in "${STATE_FILES[@]}"; do
    git cat-file -e "${remote_ref}:${file}" || fail "Required runtime-state file is missing: ${file}"
    local temp_file="${file}.runtime-state-tmp"
    git show "${remote_ref}:${file}" > "${temp_file}"
    mv -- "${temp_file}" "${file}"
  done

  validate_state_files "$PWD"

  # The claimer job uses this exact source SHA as an optimistic concurrency guard.
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    printf 'base_sha=%s\n' "${state_sha}" >> "${GITHUB_OUTPUT}"
  fi
  echo "Loaded all four authoritative runtime-state files from ${STATE_BRANCH} at ${state_sha}."
}

save_state() {
  local source_root="${GITHUB_WORKSPACE:-$PWD}"
  local runner_temp="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
  local run_suffix="${GITHUB_RUN_ID:-$$}"
  local worktree="${runner_temp}/cs-rewards-runtime-state-${run_suffix}"
  local local_branch="runtime-state-save-${run_suffix}"
  local expected_sha="${EXPECTED_RUNTIME_STATE_SHA:-}"
  local remote_ref="refs/remotes/origin/${STATE_BRANCH}"
  local current_sha

  [[ "${expected_sha}" =~ ^[0-9a-f]{40}$ ]] || fail "EXPECTED_RUNTIME_STATE_SHA is missing or invalid; refusing an unguarded state write"
  mkdir -p -- "${runner_temp}"
  [[ ! -e "${worktree}" ]] || fail "Runtime-state worktree path already exists: ${worktree}"

  fetch_state_branch
  current_sha="$(git rev-parse "${remote_ref}")"
  [[ "${current_sha}" == "${expected_sha}" ]] || fail "runtime-state changed since this claimer loaded it (loaded ${expected_sha}, current ${current_sha}); refusing a stale overwrite"

  validate_state_files "${source_root}"

  git worktree add -b "${local_branch}" "${worktree}" "${remote_ref}"
  cleanup_worktree() {
    git worktree remove --force "${worktree}" >/dev/null 2>&1 || true
  }
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

  # Refuse to merge a stale full-JSON snapshot over an unexpected concurrent
  # writer. A normal fast-forward push is the only accepted state update.
  fetch_state_branch
  current_sha="$(git rev-parse "${remote_ref}")"
  [[ "${current_sha}" == "${expected_sha}" ]] || fail "runtime-state advanced while the claimer was running (loaded ${expected_sha}, current ${current_sha}); refusing to overwrite newer state"

  if ! git -C "${worktree}" push origin "HEAD:refs/heads/${STATE_BRANCH}"; then
    fail "Push to ${STATE_BRANCH} was rejected; state was not force-pushed or merged over newer data. Inspect the branch before retrying."
  fi

  echo "Runtime state committed to ${STATE_BRANCH}."
}

case "${1:-}" in
  load) load_state ;;
  save) save_state ;;
  *) fail "Usage: $0 {load|save}" ;;
esac
