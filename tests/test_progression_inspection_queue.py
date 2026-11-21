import unittest

from progression_inspection_queue import (
    get_pending_progression_inspection,
    mark_progression_inspection_attempt,
    record_progression_inspection_result,
)


class ProgressionInspectionQueueTests(unittest.TestCase):
    def setUp(self):
        self.meta = {
            "claim_history": {
                "PLAYER0001": {"progression": {"last_claim": "2026-11-20", "status": "claimed"}}
            },
            "full_claim_coverage": {
                "PLAYER0001": {"reset_date": "2026-11-21", "status": "complete", "retry_required": False}
            },
        }

    def _deferred(self, **extra):
        return {
            "progression_deferred": True,
            "progression_unconfirmed": False,
            "progression_checked": False,
            "progression_claim_attempted": False,
            "progression_deferred_reason": (
                "popup/overlay safety guard blocked inspection; popup_context={\"private\":true}"
            ),
            **extra,
        }

    def test_deferred_inspection_is_separate_from_claim_history_and_coverage(self):
        history_before = self.meta["claim_history"]["PLAYER0001"]["progression"].copy()
        coverage_before = self.meta["full_claim_coverage"]["PLAYER0001"].copy()
        action = record_progression_inspection_result(
            self.meta, "PLAYER0001", self._deferred(),
            run_label="Primary Run", now="2026-11-21T05:52:00+05:30",
            source_run_id="3894",
        )
        entry = get_pending_progression_inspection(self.meta, "PLAYER0001")
        self.assertEqual(action, "queued")
        self.assertEqual(entry["status"], "deferred")
        self.assertEqual(entry["first_source_run_id"], "3894")
        self.assertIn("popup/overlay safety guard", entry["last_reason"])
        self.assertNotIn("popup_context", entry["last_reason"])
        self.assertEqual(self.meta["claim_history"]["PLAYER0001"]["progression"], history_before)
        self.assertEqual(self.meta["full_claim_coverage"]["PLAYER0001"], coverage_before)

    def test_read_or_unrelated_result_does_not_create_empty_queue(self):
        self.assertIsNone(get_pending_progression_inspection(self.meta, "PLAYER0001"))
        self.assertNotIn("progression_deferred_inspections", self.meta)
        action = record_progression_inspection_result(
            self.meta, "PLAYER0001",
            {"status": "Promotional Check Complete", "progression_checked": False},
            run_label="Promotional Check #1", now="2026-11-21T12:05:00+05:30",
        )
        self.assertIsNone(action)
        self.assertNotIn("progression_deferred_inspections", self.meta)

    def test_queue_result_is_a_copy(self):
        record_progression_inspection_result(
            self.meta, "PLAYER0001", self._deferred(),
            run_label="Primary Run", now="2026-11-21T05:52:00+05:30",
        )
        returned = get_pending_progression_inspection(self.meta, "PLAYER0001")
        returned["status"] = "resolved"
        self.assertEqual(
            get_pending_progression_inspection(self.meta, "PLAYER0001")["status"],
            "deferred",
        )

    def test_backup_slot_is_reserved_once_and_survives_other_slots(self):
        record_progression_inspection_result(
            self.meta, "PLAYER0001", self._deferred(),
            run_label="Primary Run", now="2026-11-21T05:52:00+05:30",
        )
        first = mark_progression_inspection_attempt(
            self.meta, "PLAYER0001", slot_label="Promotional Check #1",
            reset_date="2026-11-21", now="2026-11-21T12:05:00+05:30",
        )
        self.assertIsNotNone(first)
        self.assertIsNone(mark_progression_inspection_attempt(
            self.meta, "PLAYER0001", slot_label="Promotional Check #1",
            reset_date="2026-11-21", now="2026-11-21T12:06:00+05:30",
        ))
        second = mark_progression_inspection_attempt(
            self.meta, "PLAYER0001", slot_label="Promotional Check #2",
            reset_date="2026-11-21", now="2026-11-21T18:05:00+05:30",
        )
        self.assertIsNotNone(second)
        self.assertEqual(second["attempted_slots"], ["Promotional Check #1", "Promotional Check #2"])

    def test_primary_and_manual_full_runs_can_reserve_a_distinct_queue_slot(self):
        record_progression_inspection_result(
            self.meta, "PLAYER0001", self._deferred(),
            run_label="Primary Run", now="2026-11-21T05:52:00+05:30",
        )
        primary = mark_progression_inspection_attempt(
            self.meta, "PLAYER0001", slot_label="Primary Run",
            reset_date="2026-11-21", now="2026-11-21T06:00:00+05:30",
        )
        manual = mark_progression_inspection_attempt(
            self.meta, "PLAYER0001", slot_label="Manual Run",
            reset_date="2026-11-21", now="2026-11-21T14:00:00+05:30",
        )
        duplicate_primary = mark_progression_inspection_attempt(
            self.meta, "PLAYER0001", slot_label="Primary Run",
            reset_date="2026-11-21", now="2026-11-21T14:05:00+05:30",
        )
        self.assertIsNotNone(primary)
        self.assertIsNotNone(manual)
        self.assertIsNone(duplicate_primary)
        self.assertEqual(
            manual["attempted_slots"],
            ["Primary Run", "Manual Run"],
        )

    def test_attempt_cap_is_four_distinct_slots_per_reset(self):
        record_progression_inspection_result(
            self.meta, "PLAYER0001", self._deferred(),
            run_label="Primary Run", now="2026-11-21T05:52:00+05:30",
        )
        for slot in ("Promotional Check #1", "Promotional Check #2", "Promotional Check #3", "Promotional Check #4"):
            self.assertIsNotNone(mark_progression_inspection_attempt(
                self.meta, "PLAYER0001", slot_label=slot,
                reset_date="2026-11-21", now="2026-11-21T18:05:00+05:30",
            ))
        self.assertIsNone(mark_progression_inspection_attempt(
            self.meta, "PLAYER0001", slot_label="Promotional Check #1",
            reset_date="2026-11-21", now="2026-11-21T19:00:00+05:30",
        ))

    def test_attempt_slots_reset_on_new_daily_cycle_but_preserve_target(self):
        record_progression_inspection_result(
            self.meta, "PLAYER0001", self._deferred(),
            run_label="Primary Run", now="2026-11-21T05:52:00+05:30",
        )
        entry = mark_progression_inspection_attempt(
            self.meta, "PLAYER0001", slot_label="Promotional Check #1",
            reset_date="2026-11-21", now="2026-11-21T12:05:00+05:30",
        )
        entry["target_card_signature"] = "Grenade reward 3"
        entry["requires_exact_target"] = True
        self.meta["progression_deferred_inspections"]["PLAYER0001"] = entry
        next_cycle = mark_progression_inspection_attempt(
            self.meta, "PLAYER0001", slot_label="Promotional Check #1",
            reset_date="2026-11-22", now="2026-11-22T12:05:00+05:30",
        )
        self.assertEqual(next_cycle["attempted_slots"], ["Promotional Check #1"])
        self.assertEqual(next_cycle["target_card_signature"], "Grenade reward 3")
        self.assertTrue(next_cycle["requires_exact_target"])

    def test_unconfirmed_click_keeps_exact_target_for_next_slot(self):
        record_progression_inspection_result(
            self.meta, "PLAYER0001", self._deferred(),
            run_label="Primary Run", now="2026-11-21T05:52:00+05:30",
        )
        action = record_progression_inspection_result(
            self.meta, "PLAYER0001",
            {
                "progression_unconfirmed": True,
                "progression_unconfirmed_card_signature": "Grenade reward 3",
                "progression_deferred": False,
            },
            run_label="Promotional Check #1", now="2026-11-21T12:05:00+05:30",
        )
        entry = get_pending_progression_inspection(self.meta, "PLAYER0001")
        self.assertEqual(action, "retained")
        self.assertEqual(entry["status"], "claim_unconfirmed")
        self.assertEqual(entry["target_card_signature"], "Grenade reward 3")
        self.assertTrue(entry["requires_exact_target"])

    def test_missing_exact_target_remains_exact_only_and_must_not_fall_back(self):
        record_progression_inspection_result(
            self.meta, "PLAYER0001", self._deferred(
                progression_unconfirmed=True,
                progression_unconfirmed_card_signature=None,
            ),
            run_label="Primary Run", now="2026-11-21T05:52:00+05:30",
        )
        entry = get_pending_progression_inspection(self.meta, "PLAYER0001")
        self.assertTrue(entry["requires_exact_target"])
        self.assertIsNone(entry["target_card_signature"])

    def test_unconfirmed_click_without_signature_requires_exact_target_and_fails_closed(self):
        record_progression_inspection_result(
            self.meta, "PLAYER0001", self._deferred(),
            run_label="Primary Run", now="2026-11-21T05:52:00+05:30",
        )
        action = record_progression_inspection_result(
            self.meta, "PLAYER0001",
            {"progression_deferred": False, "progression_unconfirmed": True,
             "progression_unconfirmed_card_signature": None},
            run_label="Promotional Check #1", now="2026-11-21T12:05:00+05:30",
        )
        entry = get_pending_progression_inspection(self.meta, "PLAYER0001")
        self.assertEqual(action, "retained")
        self.assertTrue(entry["requires_exact_target"])
        self.assertIsNone(entry["target_card_signature"])
        self.assertIsNone(mark_progression_inspection_attempt(
            self.meta, "PLAYER0001", slot_label="Promotional Check #2",
            reset_date="2026-11-21", now="2026-11-21T18:05:00+05:30",
        )["target_card_signature"])

    def test_successful_reinspection_archives_and_removes_queue_item(self):
        record_progression_inspection_result(
            self.meta, "PLAYER0001", self._deferred(),
            run_label="Primary Run", now="2026-11-21T05:52:00+05:30",
            source_run_id="3894",
        )
        action = record_progression_inspection_result(
            self.meta, "PLAYER0001",
            {"progression_checked": True, "progression_deferred": False,
             "progression_unconfirmed": False, "progression": 0},
            run_label="Promotional Check #2", now="2026-11-21T18:05:00+05:30",
            source_run_id="4002",
        )
        self.assertEqual(action, "resolved")
        self.assertIsNone(get_pending_progression_inspection(self.meta, "PLAYER0001"))
        archived = self.meta["progression_deferred_inspection_history"][-1]
        self.assertEqual(archived["resolution"], "inspection_completed")
        self.assertEqual(archived["resolution_run_id"], "4002")

    def test_login_or_processing_failure_does_not_resolve_pending_inspection(self):
        record_progression_inspection_result(
            self.meta, "PLAYER0001", self._deferred(),
            run_label="Primary Run", now="2026-11-21T05:52:00+05:30",
        )
        action = record_progression_inspection_result(
            self.meta, "PLAYER0001",
            {"status": "Login Failed", "progression_checked": True,
             "progression_deferred": False, "progression_unconfirmed": False},
            run_label="Promotional Check #2", now="2026-11-21T18:05:00+05:30",
        )
        self.assertIsNone(action)
        self.assertIsNotNone(get_pending_progression_inspection(self.meta, "PLAYER0001"))
        self.assertEqual(self.meta.get("progression_deferred_inspection_history", []), [])

    def test_successful_claim_during_reinspection_archives_as_confirmed(self):
        record_progression_inspection_result(
            self.meta, "PLAYER0001", self._deferred(),
            run_label="Primary Run", now="2026-11-21T05:52:00+05:30",
        )
        record_progression_inspection_result(
            self.meta, "PLAYER0001",
            {"progression_checked": True, "progression_deferred": False,
             "progression_unconfirmed": False, "progression": 1},
            run_label="Promotional Check #2", now="2026-11-21T18:05:00+05:30",
        )
        self.assertEqual(
            self.meta["progression_deferred_inspection_history"][-1]["resolution"],
            "claim_confirmed",
        )

    def test_repeated_defer_keeps_original_source_and_attempt_history(self):
        record_progression_inspection_result(
            self.meta, "PLAYER0001", self._deferred(),
            run_label="Primary Run", now="2026-11-21T05:52:00+05:30",
            source_run_id="3894",
        )
        mark_progression_inspection_attempt(
            self.meta, "PLAYER0001", slot_label="Promotional Check #1",
            reset_date="2026-11-21", now="2026-11-21T12:05:00+05:30",
        )
        record_progression_inspection_result(
            self.meta, "PLAYER0001", self._deferred(),
            run_label="Promotional Check #1", now="2026-11-21T12:10:00+05:30",
            source_run_id="4001",
        )
        entry = get_pending_progression_inspection(self.meta, "PLAYER0001")
        self.assertEqual(entry["first_source_run_id"], "3894")
        self.assertEqual(entry["attempted_slots"], ["Promotional Check #1"])
        self.assertEqual(entry["last_source_run_id"], "4001")


if __name__ == "__main__":
    unittest.main(verbosity=2)
