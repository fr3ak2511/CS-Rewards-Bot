import unittest

from progression_dispatch_policy import is_valid_progression_queue_dispatch


class ProgressionDispatchPolicyTests(unittest.TestCase):
    def _manual(self, **overrides):
        args = {
            "event_name": "workflow_dispatch",
            "source_run_id": "44964890496",
            "recovery_slot": "Manual Run",
            "manual_progression_recovery": True,
            "manual_progression_player_id": "86CD85689FAE9549",
        }
        args.update(overrides)
        return is_valid_progression_queue_dispatch(**args)

    def test_manual_workflow_dispatch_accepts_exact_player_manual_slot(self):
        self.assertTrue(self._manual())

    def test_automated_repository_dispatch_remains_valid(self):
        self.assertTrue(is_valid_progression_queue_dispatch(
            event_name="repository_dispatch",
            source_run_id="44964890496",
            recovery_slot="Immediate Progression Recovery",
            manual_progression_recovery=False,
            manual_progression_player_id="",
        ))

    def test_manual_workflow_dispatch_requires_explicit_manual_mode(self):
        self.assertFalse(self._manual(manual_progression_recovery=False))

    def test_manual_workflow_dispatch_requires_exact_player_id(self):
        self.assertFalse(self._manual(manual_progression_player_id=""))

    def test_manual_workflow_dispatch_requires_numeric_source_run_id(self):
        self.assertFalse(self._manual(source_run_id="not-a-run-id"))

    def test_manual_workflow_dispatch_requires_manual_run_slot(self):
        self.assertFalse(self._manual(recovery_slot="Immediate Progression Recovery"))

    def test_manual_flags_cannot_be_smuggled_through_repository_dispatch(self):
        self.assertFalse(is_valid_progression_queue_dispatch(
            event_name="repository_dispatch",
            source_run_id="44964890496",
            recovery_slot="Immediate Progression Recovery",
            manual_progression_recovery=True,
            manual_progression_player_id="86CD85689FAE9549",
        ))

    def test_manual_player_id_without_manual_mode_is_rejected(self):
        self.assertFalse(is_valid_progression_queue_dispatch(
            event_name="repository_dispatch",
            source_run_id="44964890496",
            recovery_slot="Immediate Progression Recovery",
            manual_progression_recovery=False,
            manual_progression_player_id="86CD85689FAE9549",
        ))

    def test_unknown_dispatch_events_are_rejected(self):
        self.assertFalse(self._manual(event_name="schedule"))
        self.assertFalse(self._manual(event_name="push"))

    def test_daily_modes_are_rejected_for_progression_queue_only_execution(self):
        self.assertFalse(self._manual(daily_only_recovery=True))
        self.assertFalse(self._manual(manual_daily_recovery=True))
        self.assertFalse(self._manual(manual_daily_player_id="DAILYPLAYER1"))

    def test_repository_dispatch_also_rejects_daily_mode_conflicts(self):
        self.assertFalse(is_valid_progression_queue_dispatch(
            event_name="repository_dispatch",
            source_run_id="44964890496",
            recovery_slot="Immediate Progression Recovery",
            manual_progression_recovery=False,
            manual_progression_player_id="",
            daily_only_recovery=True,
        ))

    def test_empty_source_run_id_is_rejected_for_both_routes(self):
        self.assertFalse(self._manual(source_run_id=""))
        self.assertFalse(is_valid_progression_queue_dispatch(
            event_name="repository_dispatch",
            source_run_id="",
            recovery_slot="Immediate Progression Recovery",
            manual_progression_recovery=False,
            manual_progression_player_id="",
        ))


if __name__ == "__main__":
    unittest.main(verbosity=2)
