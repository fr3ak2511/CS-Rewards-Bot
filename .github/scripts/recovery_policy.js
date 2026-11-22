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
  if (lastRun && lastRun.daily_only_recovery === true) {
    return {
      dispatch: false,
      reason: 'scheduled Daily-only recovery already ran; the next retry is reserved for a later backup slot',
      retryIds: []
    };
  }

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



function evaluateImmediateProgressionQueueRecovery({
  lastRun,
  meta,
  sourceRun,
  playerIds,
  nowEpochMs = Date.now()
}) {
  const noDispatch = reason => ({
    dispatch: false,
    reason,
    progressionIds: []
  });

  if (!lastRun || typeof lastRun !== 'object' || Array.isArray(lastRun)) {
    return noDispatch('saved Primary Run metadata is invalid');
  }
  if (lastRun.run_label !== 'Primary Run' || lastRun.daily_only_recovery === true) {
    return noDispatch('Progression queue recovery is allowed only after a normal Primary Run');
  }
  if (!sourceRun || !['schedule', 'workflow_dispatch'].includes(sourceRun.event)) {
    return noDispatch('source workflow event is not an eligible Primary Run');
  }
  if (sourceRun.head_branch && sourceRun.head_branch !== 'prod') {
    return noDispatch('source Primary Run did not execute from prod');
  }

  const configuredCount = lastRun.configured_player_count;
  const fullClaimAccounts = lastRun.full_claim_accounts;
  if (!Number.isSafeInteger(configuredCount) || configuredCount <= 0
      || !Number.isSafeInteger(fullClaimAccounts)
      || fullClaimAccounts !== configuredCount
      || lastRun.accounts_processed !== configuredCount) {
    return noDispatch('source run did not complete the configured full roster');
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
  const validWindow = Number.isFinite(lastEpoch)
    && lastEpoch >= resetEpoch
    && lastEpoch <= nowEpochMs
    && Number.isFinite(runStartEpoch)
    && runStartEpoch >= resetEpoch
    && Number.isFinite(ageMs)
    && ageMs >= 0
    && ageMs <= 4 * 60 * 60 * 1000;
  if (!sameRun || !validWindow) {
    return noDispatch('saved Primary Run is stale, does not match this workflow, or began before today’s 05:30 IST reset');
  }

  const queue = meta && typeof meta === 'object' && !Array.isArray(meta)
    ? meta.progression_deferred_inspections
    : null;
  if (!queue || typeof queue !== 'object' || Array.isArray(queue)) {
    return noDispatch('no durable Progression inspection queue is available on runtime-state');
  }
  if (!Array.isArray(playerIds)) {
    return noDispatch('configured production roster could not be validated');
  }
  const roster = new Set(
    playerIds.filter(value => typeof value === 'string')
      .map(value => value.trim()).filter(Boolean)
  );
  if (!roster.size || roster.size !== playerIds.length) {
    return noDispatch('configured production roster could not be validated');
  }

  const resetIst = new Date(resetEpoch + 330 * 60 * 1000);
  const resetDate = [
    resetIst.getUTCFullYear(),
    String(resetIst.getUTCMonth() + 1).padStart(2, '0'),
    String(resetIst.getUTCDate()).padStart(2, '0')
  ].join('-');
  const slot = 'Immediate Progression Recovery';
  const progressionIds = Object.keys(queue).filter(pid => {
    if (!roster.has(pid)) return false;
    const entry = queue[pid];
    if (!entry || typeof entry !== 'object'
        || !['deferred', 'claim_unconfirmed'].includes(entry.status)
        || entry.attempt_reset_date !== resetDate) return false;
    if (String(entry.last_source_run_id || '') !== String(sourceRun.id || '')) return false;
    const attempted = Array.isArray(entry.attempted_slots)
      ? [...new Set(attemptedValues(entry.attempted_slots))]
      : [];
    if (attempted.includes(slot) || attempted.length >= 4) return false;
    if (entry.requires_exact_target) {
      const signature = String(entry.target_card_signature || '').trim();
      const fragments = Array.isArray(entry.target_card_text_fragments)
        ? entry.target_card_text_fragments.filter(x => typeof x === 'string' && x.trim())
        : [];
      if (!signature && !fragments.length) return false;
    }
    return true;
  });

  if (!progressionIds.length) {
    return noDispatch('no current-reset Progression queue entries are eligible for the immediate slot');
  }
  return {
    dispatch: true,
    reason: `${progressionIds.length} current-reset deferred Progression account(s); include a queue-only inspection pass`,
    progressionIds
  };
}

function attemptedValues(values) {
  return [...new Set(
    values.filter(value => typeof value === 'string')
      .map(value => value.trim()).filter(Boolean)
  )];
}

module.exports = { fullClaimRetryIds, evaluateImmediateRecovery, evaluateImmediateProgressionQueueRecovery };
