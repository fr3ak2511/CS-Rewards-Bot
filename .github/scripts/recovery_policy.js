// Shared immediate-recovery decision policy. Kept separate so Node tests exercise
// the exact function called by the GitHub Actions workflow.
const ALLOWED_RECOVERY_RUN_LABELS = new Set([
  'Primary Run',
  'Manual Run',
  'Promotional Check #1',
  'Promotional Check #2',
  'Promotional Check #3',
  'Promotional Check #4',
  'Backup Run #1',
  'Backup Run #2',
  'Backup Run #3',
  'Backup Run #4'
]);

function fullClaimRetryIds(lastRun) {
  if (!lastRun || typeof lastRun !== 'object' || Array.isArray(lastRun)) {
    return { retryIds: [], reason: 'saved run metadata is invalid' };
  }
  if (lastRun.retry_candidate_scope !== 'full_claim_only') {
    return { retryIds: [], reason: 'saved retry candidates are not marked full-claim-only; legacy metadata fails closed' };
  }
  if (!Array.isArray(lastRun.retry_player_ids)) {
    return { retryIds: [], reason: 'full-claim retry candidate list is missing' };
  }

  const retryIds = [...new Set(
    lastRun.retry_player_ids
      .filter(value => typeof value === 'string')
      .map(value => value.trim())
      .filter(Boolean)
  )];
  if (!retryIds.length) {
    return { retryIds: [], reason: 'no full-claim retry candidates were recorded' };
  }
  if (!Number.isSafeInteger(lastRun.retry_player_count)
      || lastRun.retry_player_count !== retryIds.length) {
    return { retryIds: [], reason: 'retry candidate count does not match unique candidate IDs' };
  }

  const fullClaimAccounts = lastRun.full_claim_accounts;
  const backupAccounts = lastRun.backup_reconciliation_accounts;
  if (!Number.isSafeInteger(fullClaimAccounts) || fullClaimAccounts < 0
      || !Number.isSafeInteger(backupAccounts) || backupAccounts < 0) {
    return { retryIds: [], reason: 'full-claim run-mode counts are missing or invalid' };
  }
  if (fullClaimAccounts + backupAccounts <= 0) {
    return { retryIds: [], reason: 'latest run contained no full-claim or backup-reconciliation accounts; promotional-only scans cannot trigger full-claim recovery' };
  }

  return { retryIds, reason: '' };
}

function parseIstTimestamp(value) {
  const stamp = String(value || '');
  if (!stamp) return NaN;
  const hasZone = /(?:[zZ]|[+-]\d{2}:\d{2})$/.test(stamp);
  return Date.parse(hasZone ? stamp : `${stamp}+05:30`);
}

function istResetEpoch(nowEpochMs) {
  const istNow = new Date(nowEpochMs + 330 * 60 * 1000);
  let year = istNow.getUTCFullYear();
  let month = istNow.getUTCMonth();
  let day = istNow.getUTCDate();
  const clock = [
    String(istNow.getUTCHours()).padStart(2, '0'),
    String(istNow.getUTCMinutes()).padStart(2, '0'),
    String(istNow.getUTCSeconds()).padStart(2, '0')
  ].join(':');

  if (clock < '05:30:00') {
    const previous = new Date(Date.UTC(year, month, day) - 24 * 60 * 60 * 1000);
    year = previous.getUTCFullYear();
    month = previous.getUTCMonth();
    day = previous.getUTCDate();
  }
  const date = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
  return Date.parse(`${date}T05:30:00+05:30`);
}

function evaluateImmediateRecovery({ lastRun, sourceRun, nowEpochMs = Date.now() }) {
  const candidateCheck = fullClaimRetryIds(lastRun);
  if (!candidateCheck.retryIds.length) {
    return { dispatch: false, reason: candidateCheck.reason, retryIds: [] };
  }

  const lastEpoch = parseIstTimestamp(lastRun.timestamp);
  const sourceEpoch = Date.parse(
    sourceRun.updated_at || sourceRun.run_started_at || sourceRun.created_at || ''
  );
  const sameRun = Number.isFinite(lastEpoch) && Number.isFinite(sourceEpoch)
    && Math.abs(sourceEpoch - lastEpoch) <= 15 * 60 * 1000;

  const durationSeconds = Math.max(0, Number(lastRun.duration_seconds) || 0);
  const runStartEpoch = Number.isFinite(lastEpoch)
    ? lastEpoch - durationSeconds * 1000
    : NaN;
  const resetEpoch = istResetEpoch(nowEpochMs);
  const ageMs = nowEpochMs - lastEpoch;
  const validLabel = ALLOWED_RECOVERY_RUN_LABELS.has(lastRun.run_label);
  const validWindow = Number.isFinite(lastEpoch)
    && lastEpoch >= resetEpoch
    && lastEpoch <= nowEpochMs
    && Number.isFinite(runStartEpoch)
    && runStartEpoch >= resetEpoch
    && Number.isFinite(ageMs)
    && ageMs >= 0
    && ageMs <= 4 * 60 * 60 * 1000;

  if (!validLabel || !sameRun || !validWindow) {
    return {
      dispatch: false,
      reason: 'saved result is stale, does not match this workflow, or the run began before the 05:30 IST reset',
      retryIds: []
    };
  }

  return {
    dispatch: true,
    reason: `${candidateCheck.retryIds.length} unresolved full-claim player ID(s); dispatch one bounded recovery pass`,
    retryIds: candidateCheck.retryIds
  };
}

module.exports = { fullClaimRetryIds, evaluateImmediateRecovery };
