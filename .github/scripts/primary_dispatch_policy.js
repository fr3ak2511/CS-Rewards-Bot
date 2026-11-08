'use strict';

const ACTIVE_CLAIM_STATUSES = new Set(['queued', 'in_progress']);
const ALLOWED_FULL_ROSTER_RUN_LABELS = new Set([
  'Primary Run',
  'Manual Run',
  'Promotional Check #1',
  'Promotional Check #2',
  'Promotional Check #3',
  'Promotional Check #4',
  'Backup Run #1',
  'Backup Run #2',
  'Backup Run #3',
  'Backup Run #4',
]);

/**
 * Decide whether an already-active workflow run must block another claimer.
 *
 * The external morning dispatch is allowed to pass a scheduled dispatcher
 * that is still sleeping in primary_delay. Its dependent claim job can appear
 * as queued even though it does not hold the rewards-claimer concurrency slot.
 * Every genuinely queued/running claim job remains a blocker.
 */
function isBlockingClaimJob({
  isExternalPrimary = false,
  claimJobStatus = '',
  primaryDelayStatus = '',
} = {}) {
  if (!ACTIVE_CLAIM_STATUSES.has(claimJobStatus)) {
    return false;
  }

  if (
    isExternalPrimary &&
    claimJobStatus === 'queued' &&
    primaryDelayStatus === 'in_progress'
  ) {
    return false;
  }

  return true;
}

/** Count unique configured IDs from the simple, header-based players.csv file. */
function countConfiguredPlayers(csvText) {
  const rows = String(csvText || '').split(/\r?\n/).filter(line => line.trim());
  if (rows.length < 2) return null;

  const headers = rows.shift().split(',').map(value => value.trim().replace(/^"|"$/g, ''));
  const playerIndex = headers.indexOf('player_id');
  if (playerIndex < 0) return null;

  const ids = rows.map(line => {
    const cells = line.split(',');
    return String(cells[playerIndex] || '').trim().replace(/^"|"$/g, '');
  }).filter(Boolean);

  if (!ids.length || new Set(ids).size !== ids.length) return null;
  return ids.length;
}

function parseIstTimestamp(value) {
  if (typeof value !== 'string' || !value.trim()) return null;
  let source = value.trim();
  // bot_meta.json stores naive timestamps as IST wall-clock values.
  if (!/(?:Z|[+-]\d{2}:\d{2})$/i.test(source)) source += '+05:30';
  const parsed = Date.parse(source);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * Match recovery_policy.has_complete_full_roster_coverage for the lightweight
 * preflight. This lets the native 03:00 dispatcher exit before its long wait
 * when the external primary has already committed today's complete run.
 */
function hasCompleteFullRosterCoverage({ fullRun, playerCount, now = new Date() } = {}) {
  if (!fullRun || typeof fullRun !== 'object' || Array.isArray(fullRun)) return false;
  if (!ALLOWED_FULL_ROSTER_RUN_LABELS.has(fullRun.run_label)) return false;

  const expectedCount = Number(playerCount);
  const recordedCount = Number(fullRun.player_count);
  const rawFullClaimCount = fullRun.full_claim_accounts;
  const fullClaimCount = rawFullClaimCount === undefined
    ? recordedCount
    : Number(rawFullClaimCount);
  const durationSeconds = Number(fullRun.duration_seconds || 0);
  const endMs = parseIstTimestamp(fullRun.timestamp);
  const nowMs = now instanceof Date ? now.getTime() : Number(now);

  if (
    !Number.isInteger(expectedCount) || expectedCount < 1 ||
    !Number.isInteger(recordedCount) || !Number.isInteger(fullClaimCount) ||
    !Number.isFinite(durationSeconds) || durationSeconds < 0 ||
    endMs === null || !Number.isFinite(nowMs)
  ) {
    return false;
  }

  const startMs = endMs - durationSeconds * 1000;
  const istNow = new Date(nowMs + 330 * 60 * 1000);
  const resetAtUtcMs = Date.UTC(
    istNow.getUTCFullYear(),
    istNow.getUTCMonth(),
    istNow.getUTCDate(),
    5, 30, 0, 0
  ) - 330 * 60 * 1000;

  return Boolean(
    startMs >= resetAtUtcMs &&
    endMs <= nowMs &&
    recordedCount === expectedCount &&
    fullClaimCount === expectedCount
  );
}

module.exports = {
  countConfiguredPlayers,
  hasCompleteFullRosterCoverage,
  isBlockingClaimJob,
};
