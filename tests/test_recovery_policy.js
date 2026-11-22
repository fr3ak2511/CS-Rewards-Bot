const assert = require('node:assert/strict');
const test = require('node:test');

const {
  evaluateImmediateRecovery,
  evaluateImmediateProgressionQueueRecovery
} = require('../.github/scripts/recovery_policy.js');

const nowEpochMs = Date.parse('2026-11-07T02:00:00Z'); // 07:30 IST
const sourceRun = {
  updated_at: '2026-11-07T01:50:30Z' // 07:20:30 IST
};

function validLastRun(overrides = {}) {
  return {
    run_label: 'Primary Run',
    timestamp: '2026-11-07T07:20:00',
    duration_seconds: 600,
    retry_candidate_scope: 'full_claim_only',
    retry_player_ids: ['PLAYER-A'],
    retry_player_count: 1,
    full_claim_accounts: 1,
    backup_reconciliation_accounts: 0,
    promotional_only_accounts: 0,
    ...overrides
  };
}

function decide(lastRun, source = sourceRun) {
  return evaluateImmediateRecovery({ lastRun, sourceRun: source, nowEpochMs });
}

test('legacy metadata without full_claim_only scope is rejected', () => {
  const legacy = validLastRun();
  delete legacy.retry_candidate_scope;
  const result = decide(legacy);
  assert.equal(result.dispatch, false);
  assert.match(result.reason, /not marked full-claim-only/);
});

test('promotional-only run cannot dispatch full-claim recovery', () => {
  const promoOnly = validLastRun({
    retry_player_ids: ['PROMO-ONLY-ID'],
    retry_player_count: 1,
    full_claim_accounts: 0,
    backup_reconciliation_accounts: 0,
    promotional_only_accounts: 35
  });
  const result = decide(promoOnly);
  assert.equal(result.dispatch, false);
  assert.match(result.reason, /promotional-only scans cannot trigger full-claim recovery/);
});

test('empty candidate list is rejected', () => {
  const empty = validLastRun({ retry_player_ids: [], retry_player_count: 0 });
  const result = decide(empty);
  assert.equal(result.dispatch, false);
  assert.match(result.reason, /no full-claim retry candidates/);
});

test('candidate count mismatch is rejected', () => {
  const mismatch = validLastRun({
    retry_player_ids: ['PLAYER-A', 'PLAYER-B'],
    retry_player_count: 1
  });
  const result = decide(mismatch);
  assert.equal(result.dispatch, false);
  assert.match(result.reason, /count does not match/);
});

test('matching full-claim candidates permit one bounded recovery dispatch', () => {
  const result = decide(validLastRun());
  assert.equal(result.dispatch, true);
  assert.deepEqual(result.retryIds, ['PLAYER-A']);
  assert.match(result.reason, /1 unresolved full-claim player ID/);
});

test('explicit IST offset in saved metadata is parsed without double-appending a zone', () => {
  const result = decide(validLastRun({ timestamp: '2026-11-07T07:20:00+05:30' }));
  assert.equal(result.dispatch, true);
  assert.deepEqual(result.retryIds, ['PLAYER-A']);
});

test('source run timestamp must match the saved metadata timestamp', () => {
  const result = decide(validLastRun(), { updated_at: '2026-11-07T00:00:00Z' });
  assert.equal(result.dispatch, false);
  assert.match(result.reason, /stale, does not match this workflow/);
});

test('stale recovery candidates are rejected', () => {
  const result = decide(validLastRun({
    timestamp: '2026-11-07T01:00:00',
    duration_seconds: 600
  }));
  assert.equal(result.dispatch, false);
  assert.match(result.reason, /stale, does not match this workflow/);
});

test('scheduled Daily-only recovery does not trigger a second immediate recovery loop', () => {
  const result = decide(validLastRun({ daily_only_recovery: true }));
  assert.equal(result.dispatch, false);
  assert.match(result.reason, /next retry is reserved for a later backup slot/);
  assert.deepEqual(result.retryIds, []);
});


const progressionNowEpochMs = Date.parse('2026-11-22T00:35:00Z');
const progressionSourceRun = {
  id: 44964890496,
  event: 'workflow_dispatch',
  head_branch: 'prod',
  created_at: '2026-11-22T00:05:09Z',
  run_started_at: '2026-11-22T00:05:09Z',
  updated_at: '2026-11-22T00:29:00Z'
};
const progressionLastRun = {
  run_label: 'Primary Run',
  timestamp: '2026-11-22T05:58:33.009976',
  duration_seconds: 1373,
  accounts_processed: 36,
  full_claim_accounts: 36,
  configured_player_count: 36
};
const progressionPlayerIds = ['PLAYER-A', 'PLAYER-B'];
const progressionMeta = {
  progression_deferred_inspections: {
    'PLAYER-A': {
      status: 'deferred', attempt_reset_date: '2026-11-22',
      attempted_slots: ['Primary Run'], total_attempts: 1,
      last_source_run_id: '44964890496',
      requires_exact_target: false
    },
    'PLAYER-B': {
      status: 'deferred', attempt_reset_date: '2026-11-22',
      attempted_slots: ['Primary Run'], total_attempts: 1,
      last_source_run_id: '44964890496',
      requires_exact_target: false
    }
  }
};

function decideProgression(overrides = {}) {
  return evaluateImmediateProgressionQueueRecovery({
    lastRun: progressionLastRun,
    meta: progressionMeta,
    sourceRun: progressionSourceRun,
    playerIds: progressionPlayerIds,
    nowEpochMs: progressionNowEpochMs,
    ...overrides
  });
}

test('a completed full-roster Primary Run includes only current-reset queue entries from that source run', () => {
  const result = decideProgression();
  assert.equal(result.dispatch, true);
  assert.deepEqual(result.progressionIds, ['PLAYER-A', 'PLAYER-B']);
  assert.match(result.reason, /2 current-reset deferred Progression/);
});

test('Progression queue recovery rejects non-primary runs', () => {
  const result = decideProgression({
    lastRun: { ...progressionLastRun, run_label: 'Promotional Check #1' }
  });
  assert.equal(result.dispatch, false);
  assert.match(result.reason, /only after a normal Primary Run/);
});

test('Progression queue recovery requires complete full-roster coverage', () => {
  const result = decideProgression({
    lastRun: { ...progressionLastRun, full_claim_accounts: 35 }
  });
  assert.equal(result.dispatch, false);
  assert.match(result.reason, /did not complete the configured full roster/);
});

test('Progression queue recovery rejects stale or mismatched source runs', () => {
  const result = decideProgression({
    sourceRun: { ...progressionSourceRun, updated_at: '2026-11-21T00:29:00Z' }
  });
  assert.equal(result.dispatch, false);
  assert.match(result.reason, /stale, does not match this workflow/);
});

test('Progression queue recovery ignores stale reset entries and IDs outside the roster', () => {
  const result = decideProgression({
    meta: {
      progression_deferred_inspections: {
        'PLAYER-A': {
          status: 'deferred', attempt_reset_date: '2026-11-21',
          attempted_slots: ['Primary Run'], last_source_run_id: '44964890496',
          requires_exact_target: false
        },
        'NOT-IN-ROSTER': {
          status: 'deferred', attempt_reset_date: '2026-11-22',
          attempted_slots: ['Primary Run'], last_source_run_id: '44964890496',
          requires_exact_target: false
        }
      }
    }
  });
  assert.equal(result.dispatch, false);
  assert.deepEqual(result.progressionIds, []);
});

test('Progression queue recovery rejects entries not written by this Primary Run', () => {
  const result = decideProgression({
    meta: {
      progression_deferred_inspections: {
        'PLAYER-A': {
          status: 'deferred', attempt_reset_date: '2026-11-22',
          attempted_slots: ['Primary Run'], last_source_run_id: 'OTHER-RUN',
          requires_exact_target: false
        }
      }
    }
  });
  assert.equal(result.dispatch, false);
  assert.deepEqual(result.progressionIds, []);
});

test('Progression queue recovery rejects consumed slots and entries at the four-attempt cap', () => {
  const result = decideProgression({
    meta: {
      progression_deferred_inspections: {
        'PLAYER-A': {
          status: 'deferred', attempt_reset_date: '2026-11-22',
          attempted_slots: ['Primary Run', 'Immediate Progression Recovery'],
          last_source_run_id: '44964890496', requires_exact_target: false
        },
        'PLAYER-B': {
          status: 'deferred', attempt_reset_date: '2026-11-22',
          attempted_slots: ['Primary Run', 'Promotional Check #1', 'Promotional Check #2', 'Promotional Check #3'],
          last_source_run_id: '44964890496', requires_exact_target: false
        }
      }
    }
  });
  assert.equal(result.dispatch, false);
});

test('Progression queue recovery fails closed when an exact claim target was not saved', () => {
  const result = decideProgression({
    meta: {
      progression_deferred_inspections: {
        'PLAYER-A': {
          status: 'claim_unconfirmed', attempt_reset_date: '2026-11-22',
          attempted_slots: ['Primary Run'], last_source_run_id: '44964890496',
          requires_exact_target: true, target_card_signature: null
        }
      }
    }
  });
  assert.equal(result.dispatch, false);
});

