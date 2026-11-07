'use strict';

const ACTIVE_CLAIM_STATUSES = new Set(['queued', 'in_progress']);

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

module.exports = { isBlockingClaimJob };
