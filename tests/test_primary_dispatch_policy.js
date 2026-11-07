'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { isBlockingClaimJob } = require('../.github/scripts/primary_dispatch_policy');

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
