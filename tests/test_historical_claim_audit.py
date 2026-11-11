import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from historical_claim_audit import (
    _record_snapshots,
    _reconcile_runs,
    _resolve_unconfirmed_flags,
    _run_window_integrity,
    _summarise_record_events,
    build_account_coverage,
    run_audit,
)


PLAYER_A = "A1B2C3D4E5F60708"
PLAYER_OLD = "OLD9OLD9OLD9OLD9"
COMMIT_1 = "1" * 40
COMMIT_2 = "2" * 40
COMMIT_3 = "3" * 40


def snapshot(commit, timestamp, data):
    return {
        "commit": commit,
        "commit_time_utc": timestamp,
        "data": data,
    }


def base_history():
    return {
        PLAYER_A: {
            "daily": {
                "last_claim": "2026-05-31T05:36:00",
                "status": "claimed",
            },
            "store": {
                "reward_1": {"last_claim": None, "status": "unknown"},
                "reward_2": {"last_claim": None, "status": "unknown"},
                "reward_3": {"last_claim": None, "status": "unknown"},
                "reward_4": {"last_claim": None, "status": "unknown"},
            },
            "progression": {"last_claim": None, "last_count": 0},
            "loyalty": {"last_claim": None, "status": "unknown"},
            "promotions": {},
            "event_rewards": {},
        },
        PLAYER_OLD: {
            "daily": {"last_claim": "2026-05-30T05:35:00", "status": "claimed"}
        },
    }


def updated_history():
    data = base_history()
    player = data[PLAYER_A]
    player["daily"] = {
        "last_claim": "2026-06-01T05:36:00",
        "status": "claimed",
    }
    player["store"]["reward_1"] = {
        "last_claim": "2026-06-01T05:36:05",
        "status": "claimed",
    }
    player["store"]["reward_2"] = {
        "last_claim": None,
        "status": "portal_claimed",
        "portal_observed_at": "2026-06-01T05:36:25",
    }
    player["progression"] = {
        "last_claim": "2026-06-01T05:36:10",
        "last_count": 2,
        "status": "claimed",
    }
    player["promotions"] = {
        "promo_abc": {
            "title": "Temporary Store Reward",
            "status": "claimed",
            "last_claim": "2026-06-01T05:36:20",
        }
    }
    player["event_rewards"] = {
        "program-1": {
            "name": "Sample Event",
            "claim_ledger": [
                {
                    "cycle": 1,
                    "threshold": 400,
                    "claimed_at": "2026-06-01T05:36:30",
                    "verification": "delivered",
                    "reward": ["Sample reward"],
                }
            ],
        }
    }
    return data


class HistoricalClaimAuditTests(unittest.TestCase):
    def test_claim_timeline_separates_claims_from_portal_observations(self):
        first = snapshot(COMMIT_1, "2026-05-31T00:10:00Z", base_history())
        second = snapshot(COMMIT_2, "2026-06-01T00:10:00Z", updated_history())
        third_data = updated_history()
        third_data[PLAYER_A]["daily"] = {
            "last_claim": None,
            "status": "unconfirmed",
            "last_attempt_at": "2026-06-01T06:00:00",
        }
        # The ledger is persistent and must not be counted again in later snapshots.
        third = snapshot(COMMIT_3, "2026-06-01T00:20:00Z", third_data)

        events, observations, anomalies, coverage = _summarise_record_events(
            [first, second, third]
        )
        by_category = {}
        for event in events:
            by_category.setdefault(event["category"], []).append(event)

        self.assertEqual(coverage["snapshot_count"], 3)
        self.assertEqual(len(by_category["daily"]), 3)  # Two accounts in baseline + one new transition.
        self.assertEqual(len(by_category["store_daily"]), 1)
        self.assertEqual(by_category["progression"][0]["claim_units"], 2)
        self.assertEqual(by_category["promotions"][0]["classification"], "bot_confirmed")
        self.assertEqual(by_category["event_rewards"][0]["classification"], "portal_confirmed_delivered")
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["status"], "portal_claimed")
        self.assertNotIn("portal_claimed", [event["classification"] for event in events])
        self.assertIn(
            "same_record_claim_cleared_as_unconfirmed",
            [item["type"] for item in anomalies],
        )
        self.assertIn("record_became_unconfirmed", [item["type"] for item in anomalies])

    def test_run_counters_match_history_transitions_in_run_window(self):
        _, _, _, _ = _summarise_record_events(
            [snapshot(COMMIT_1, "2026-05-31T00:10:00Z", base_history())]
        )
        events, _, _, _ = _summarise_record_events(
            [
                snapshot(COMMIT_1, "2026-05-31T00:10:00Z", base_history()),
                snapshot(COMMIT_2, "2026-06-01T00:10:00Z", updated_history()),
            ]
        )
        runs = [
            {
                "timestamp": "2026-06-01T05:36:50",
                "run_label": "Primary Run",
                "duration_seconds": 100,
                "per_type": {
                    "daily": 1,
                    "store_daily": 1,
                    "progression": 2,
                    "promotions": 1,
                    "event_rewards": 1,
                },
            }
        ]
        result = _reconcile_runs(runs, events)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "matched")
        self.assertEqual(result[0]["mismatches"], [])

    def test_legacy_progression_without_status_is_bot_confirmed_when_count_is_positive(self):
        first_history = {
            PLAYER_A: {
                "progression": {
                    "last_claim": "2026-05-31T05:36:00",
                    "last_count": 2,
                    "last_visit": "2026-05-31T05:36:30",
                }
            }
        }
        second_history = {
            PLAYER_A: {
                "progression": {
                    "last_claim": "2026-06-01T05:36:00",
                    "last_count": 3,
                    "last_visit": "2026-06-01T05:36:30",
                }
            }
        }
        events, _, _, _ = _summarise_record_events(
            [
                snapshot(COMMIT_1, "2026-05-31T00:10:00Z", first_history),
                snapshot(COMMIT_2, "2026-06-01T00:10:00Z", second_history),
            ]
        )
        self.assertEqual(len(events), 2)
        self.assertEqual(events[-1]["classification"], "bot_confirmed_legacy_schema")
        self.assertEqual(events[-1]["record_status"], "legacy_claimed_no_status")
        self.assertEqual(events[-1]["claim_units"], 3)

    def test_run_window_overlap_integrity_is_reported(self):
        runs = [
            {"timestamp": "2026-06-01T05:10:00", "duration_seconds": 600},
            {"timestamp": "2026-06-01T05:15:00", "duration_seconds": 600},
        ]
        events = [{
            "classification": "bot_confirmed_legacy_schema",
            "recorded_at": "2026-06-01T05:07:00",
        }]
        result = _run_window_integrity(runs, events)
        self.assertEqual(result["overlapping_run_window_pairs"], 1)
        self.assertEqual(result["claim_events_assigned_to_multiple_run_windows"], 1)

    def test_account_coverage_preserves_historical_only_accounts(self):
        result = build_account_coverage(
            {PLAYER_A: {}, PLAYER_OLD: {}},
            [PLAYER_A, "NEWPLAYER0000001"],
        )
        self.assertEqual(result["history_account_count"], 2)
        self.assertEqual(result["configured_roster_count"], 2)
        self.assertEqual(result["historical_only_count"], 1)
        self.assertEqual(result["configured_accounts_missing_from_history_count"], 1)
        self.assertTrue(result["historical_only_accounts"][0].endswith("…OLD9"))

    def test_event_ledger_entry_is_deduplicated_across_snapshots(self):
        first = updated_history()
        second = updated_history()
        events, _, _, _ = _summarise_record_events(
            [
                snapshot(COMMIT_1, "2026-06-01T00:10:00Z", first),
                snapshot(COMMIT_2, "2026-06-01T00:20:00Z", second),
            ]
        )
        delivered = [
            event for event in events
            if event["category"] == "event_rewards"
            and event["classification"] == "portal_confirmed_delivered"
        ]
        self.assertEqual(len(delivered), 1)

    def test_git_snapshot_reader_streams_first_parent_versions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.name", "Audit Test"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.email", "audit@example.test"],
                check=True,
            )
            path = repo / "claim_history.json"
            for state in ({"account": {"daily": {"last_claim": None}}}, {
                "account": {"daily": {"last_claim": "2026-06-01T05:30:00"}}
            }):
                path.write_text(json.dumps(state), encoding="utf-8")
                subprocess.run(
                    ["git", "-C", str(repo), "add", "claim_history.json"], check=True
                )
                subprocess.run(
                    ["git", "-C", str(repo), "commit", "-q", "-m", "history snapshot"],
                    check=True,
                )
            snapshots = list(_record_snapshots(repo, "HEAD", "claim_history.json"))
            self.assertEqual(len(snapshots), 2)
            self.assertIsNone(snapshots[0]["data"]["account"]["daily"]["last_claim"])
            self.assertEqual(
                snapshots[1]["data"]["account"]["daily"]["last_claim"],
                "2026-06-01T05:30:00",
            )

    def test_unconfirmed_attempt_can_resolve_from_later_portal_observation(self):
        anomalies = [{
            "type": "record_became_unconfirmed",
            "account": "…E6D9",
            "category": "daily",
            "last_attempt_at": "2026-11-11T05:36:58",
        }]
        observations = [{
            "account": "…E6D9",
            "category": "daily",
            "observed_at": "2026-11-11T17:19:20",
            "status": "portal_claimed",
        }]
        result = _resolve_unconfirmed_flags(anomalies, [], observations)
        self.assertEqual(result["unconfirmed_transitions_subsequently_confirmed"], 0)
        self.assertEqual(result["unconfirmed_transitions_subsequently_portal_observed"], 1)
        self.assertEqual(result["unconfirmed_transitions_open"], 0)
        self.assertEqual(
            anomalies[0]["resolution_status"],
            "subsequently_portal_observed_claimed",
        )
        self.assertEqual(
            anomalies[0]["resolved_by_portal_observation_at"],
            "2026-11-11T17:19:20",
        )

    def test_unconfirmed_attempt_before_portal_observation_remains_open(self):
        anomalies = [{
            "type": "record_became_unconfirmed",
            "account": "…E6D9",
            "category": "daily",
            "last_attempt_at": "2026-11-11T18:00:00",
        }]
        observations = [{
            "account": "…E6D9",
            "category": "daily",
            "observed_at": "2026-11-11T17:19:20",
            "status": "portal_claimed",
        }]
        result = _resolve_unconfirmed_flags(anomalies, [], observations)
        self.assertEqual(result["unconfirmed_transitions_subsequently_portal_observed"], 0)
        self.assertEqual(result["unconfirmed_transitions_open"], 1)

    def test_full_read_only_audit_writes_three_reports(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.name", "Audit Test"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.email", "audit@example.test"],
                check=True,
            )
            (repo / "claim_history.json").write_text(
                json.dumps({PLAYER_A: {"daily": {"last_claim": "2026-06-01T05:36:00", "status": "claimed"}}}),
                encoding="utf-8",
            )
            (repo / "bot_meta.json").write_text(
                json.dumps({"last_run": {"timestamp": "2026-06-01T05:37:00", "run_label": "Primary Run", "duration_seconds": 90, "per_type": {"daily": 1}}}),
                encoding="utf-8",
            )
            (repo / "players.csv").write_text(
                "player_id,has_loyalty\n" + PLAYER_A + ",false\n",
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "-C", str(repo), "add", "."], check=True
            )
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-q", "-m", "initial audit fixture"],
                check=True,
            )
            out_dir = Path(temp_dir) / "output"
            report = run_audit(repo, "HEAD", out_dir)
            self.assertTrue(report["scope"]["read_only"])
            expected_sha = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
            ).strip()
            self.assertEqual(report["source_commit_sha"], expected_sha)
            self.assertEqual(report["account_coverage"]["history_account_count"], 1)
            self.assertTrue((out_dir / "historical_claim_audit.json").exists())
            self.assertTrue((out_dir / "historical_claim_audit.csv").exists())
            self.assertTrue((out_dir / "historical_claim_audit.md").exists())
            self.assertFalse((repo / "claim_history.json").read_text().startswith("AUDIT"))


if __name__ == "__main__":
    unittest.main()
