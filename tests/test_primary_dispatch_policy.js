'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  countConfiguredPlayers,
  hasCompleteFullRosterCoverage,
  isBlockingClaimJob,
} = require('../.github/scripts/primary_dispatch_policy');

test('external primary may pass a scheduled run that is only waiting for its randomized target', () => {
  assert.equal(isBlockingClaimJob({
    isExternalPrimary: true,
    claimJobStatus: 'queued',
    primaryDelayStatus: 'in_progress',
  }), false);
});

test('an actually running claim job always blocks an external primary', () => {
  assert.equal(isBlockingClaimJob({
    isExternalPrimary: true,
    claimJobStatus: 'in_progress',
    primaryDelayStatus: 'in_progress',
  }), true);
});

test('a queued claim job without an active primary wait blocks an external primary', () => {
  assert.equal(isBlockingClaimJob({
    isExternalPrimary: true,
    claimJobStatus: 'queued',
    primaryDelayStatus: 'completed',
  }), true);
});

test('ordinary scheduled events still treat a queued claim job as a blocker', () => {
  assert.equal(isBlockingClaimJob({
    isExternalPrimary: false,
    claimJobStatus: 'queued',
    primaryDelayStatus: 'in_progress',
  }), true);
});

test('completed, skipped, and absent claim jobs do not block', () => {
  for (const status of ['completed', 'skipped', '']) {
    assert.equal(isBlockingClaimJob({
      isExternalPrimary: true,
      claimJobStatus: status,
      primaryDelayStatus: 'completed',
    }), false);
  }
});

test('roster count requires a player_id column and unique non-empty IDs', () => {
  assert.equal(countConfiguredPlayers('player_id,has_loyalty\nA,true\nB,false\n'), 2);
  assert.equal(countConfiguredPlayers('name,has_loyalty\nA,true\n'), null);
  assert.equal(countConfiguredPlayers('player_id,has_loyalty\nA,true\nA,false\n'), null);
  assert.equal(countConfiguredPlayers('player_id,has_loyalty\n'), null);
});

const completeRun = {
  run_label: 'Primary Run',
  timestamp: '2026-11-08T06:41:02.812639',
  duration_seconds: 3932,
  player_count: 35,
  full_claim_accounts: 35,
};

test('a complete same-reset full-roster cycle suppresses the redundant native dispatcher', () => {
  assert.equal(hasCompleteFullRosterCoverage({
    fullRun: completeRun,
    playerCount: 35,
    now: new Date('2026-11-08T07:00:00+05:30'),
  }), true);
});

test('partial roster coverage does not suppress the native backup', () => {
  assert.equal(hasCompleteFullRosterCoverage({
    fullRun: { ...completeRun, player_count: 34, full_claim_accounts: 34 },
    playerCount: 35,
    now: new Date('2026-11-08T07:00:00+05:30'),
  }), false);
});

test('a full-roster label without full-claim account coverage is rejected', () => {
  assert.equal(hasCompleteFullRosterCoverage({
    fullRun: { ...completeRun, full_claim_accounts: 34 },
    playerCount: 35,
    now: new Date('2026-11-08T07:00:00+05:30'),
  }), false);
});

test('a cycle from before today's reset does not suppress the native backup', () => {
  assert.equal(hasCompleteFullRosterCoverage({
    fullRun: { ...completeRun, timestamp: '2026-11-07T06:41:02.812639' },
    playerCount: 35,
    now: new Date('2026-11-08T07:00:00+05:30'),
  }), false);
});

test('a run that has not finished yet does not suppress the native backup', () => {
  assert.equal(hasCompleteFullRosterCoverage({
    fullRun: completeRun,
    playerCount: 35,
    now: new Date('2026-11-08T06:30:00+05:30'),
  }), false);
});

test('unknown or malformed run metadata fails open to the native backup', () => {
  assert.equal(hasCompleteFullRosterCoverage({
    fullRun: { ...completeRun, run_label: 'Promo Only' },
    playerCount: 35,
    now: new Date('2026-11-08T07:00:00+05:30'),
  }), false);
  assert.equal(hasCompleteFullRosterCoverage({
    fullRun: { ...completeRun, timestamp: 'not-a-date' },
    playerCount: 35,
    now: new Date('2026-11-08T07:00:00+05:30'),
  }), false);
});
