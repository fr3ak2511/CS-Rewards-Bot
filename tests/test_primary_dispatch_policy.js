'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  countConfiguredPlayers,
  hasCompleteFullRosterCoverage,
  isBlockingClaimJob,
  evaluateScheduledBackupFreshness,
  evaluateExternalPromotionalBackupFreshness,
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

test("a cycle from before today's reset does not suppress the native backup", () => {
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


test('fresh scheduled backup events pass within their allowed window', () => {
  const result = evaluateScheduledBackupFreshness({
    cron: '5 4 * * *',
    createdAt: '2026-11-13T04:08:00Z',
    now: new Date('2026-11-13T04:20:00Z'),
  });
  assert.equal(result.fresh, true);
  assert.equal(result.createdDelayMinutes, 3);
  assert.equal(result.processingDelayMinutes, 15);
});

test('scheduled backup event created hours late is rejected', () => {
  const result = evaluateScheduledBackupFreshness({
    cron: '5 4 * * *',
    createdAt: '2026-11-13T12:03:42Z',
    now: new Date('2026-11-13T12:03:50Z'),
  });
  assert.equal(result.fresh, false);
  assert.match(result.reason, /stale scheduled backup event/i);
});

test('a backup claim job that waited too long after a fresh trigger is rejected', () => {
  const result = evaluateScheduledBackupFreshness({
    cron: '5 8 * * *',
    createdAt: '2026-11-13T08:06:00Z',
    now: new Date('2026-11-13T09:00:00Z'),
  });
  assert.equal(result.fresh, false);
  assert.match(result.reason, /reached execution/i);
});

test('unknown cron and malformed trigger timestamps fail closed', () => {
  assert.equal(evaluateScheduledBackupFreshness({
    cron: '5 20 * * *',
    createdAt: '2026-11-13T20:05:00Z',
    now: new Date('2026-11-13T20:06:00Z'),
  }).fresh, false);
  assert.equal(evaluateScheduledBackupFreshness({
    cron: '5 4 * * *',
    createdAt: 'not-a-timestamp',
    now: new Date('2026-11-13T04:06:00Z'),
  }).fresh, false);
});


test('external promotional slot 1 accepts a fresh 12:05 IST dispatch', () => {
  const result = evaluateExternalPromotionalBackupFreshness({
    slot: '1',
    createdAt: '2026-11-13T06:38:00Z',
    now: new Date('2026-11-13T06:50:00Z'),
  });
  assert.equal(result.fresh, true);
  assert.equal(result.createdDelayMinutes, 3);
  assert.equal(result.processingDelayMinutes, 15);
});

test('external promotional slot 2 accepts a fresh 18:05 IST dispatch', () => {
  const result = evaluateExternalPromotionalBackupFreshness({
    slot: '2',
    createdAt: '2026-11-13T12:40:00Z',
    now: new Date('2026-11-13T12:50:00Z'),
  });
  assert.equal(result.fresh, true);
  assert.equal(result.createdDelayMinutes, 5);
  assert.equal(result.processingDelayMinutes, 15);
});

test('external promotional dispatch created more than 45 minutes late is rejected', () => {
  const result = evaluateExternalPromotionalBackupFreshness({
    slot: '1',
    createdAt: '2026-11-13T07:21:00Z',
    now: new Date('2026-11-13T07:22:00Z'),
  });
  assert.equal(result.fresh, false);
  assert.match(result.reason, /created too late/i);
});

test('external promotional claim job delayed after a fresh dispatch is rejected', () => {
  const result = evaluateExternalPromotionalBackupFreshness({
    slot: '2',
    createdAt: '2026-11-13T12:40:00Z',
    now: new Date('2026-11-13T13:30:00Z'),
  });
  assert.equal(result.fresh, false);
  assert.match(result.reason, /execution too late/i);
});

test('invalid external promotional slot and malformed timestamps fail closed', () => {
  assert.equal(evaluateExternalPromotionalBackupFreshness({
    slot: '3',
    createdAt: '2026-11-13T06:35:00Z',
    now: new Date('2026-11-13T06:36:00Z'),
  }).fresh, false);
  assert.equal(evaluateExternalPromotionalBackupFreshness({
    slot: '1',
    createdAt: 'not-a-timestamp',
    now: new Date('2026-11-13T06:36:00Z'),
  }).fresh, false);
});
