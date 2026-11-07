const assert = require('node:assert/strict');
const test = require('node:test');

const { evaluateImmediateRecovery } = require('../.github/scripts/recovery_policy.js');

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
