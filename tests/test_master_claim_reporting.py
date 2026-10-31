
import unittest

from master_claimer import (
    _player_claim_total,
    _summary_counts,
    build_promotional_section,
)


class ClaimCountAndEmailReportingTests(unittest.TestCase):
    @staticmethod
    def event_results():
        return [{
            "program_id": "pgp_eLiQHDMchen",
            "name": "Lost Soul Rewards",
            "slug": "halloween-2026",
            "points_label": "Lost Souls",
            "balance": 1100,
            "next_threshold": 1300,
            "tiers_total": 14,
            "tiers_delivered": 2,
            "claims_this_run": [
                {"threshold": 400, "reward": [], "status": "claimed_this_run"},
                {"threshold": 900, "reward": [], "status": "claimed_this_run"},
            ],
            "retry_required": False,
            "error": None,
        }]

    @classmethod
    def sample_player(cls):
        return {
            "pid": "TESTPLAYER0001",
            "display_name": "Halloween Test Player",
            "status": "Success",
            "daily": 0,
            "store": 0,
            "store_temp": 0,
            "progression": 0,
            "loyalty": 0,
            "daily_skipped": False,
            "store_skipped": [False, False, False],
            "has_loyalty": False,
            "loyalty_skipped": False,
            "promotions_observed": [],
            "promotion_detection_failed": False,
            "retry_required": False,
            "event_rewards": cls.event_results(),
        }

    def test_event_claims_are_counted_once_in_player_total(self):
        result = {
            "daily": 0,
            "store": 0,
            "store_temp": 0,
            "progression": 0,
            "loyalty": 0,
            "event_rewards": self.event_results(),
        }
        self.assertEqual(_player_claim_total(result), 2)

    def test_player_total_combines_all_reward_categories_once(self):
        result = {
            "daily": 1,
            "store": 2,
            "store_temp": 1,
            "progression": 3,
            "loyalty": 1,
            "event_rewards": self.event_results(),
        }
        self.assertEqual(_player_claim_total(result), 10)

    def test_combined_email_section_reports_halloween_progress_and_claims(self):
        player = self.sample_player()
        section = build_promotional_section([player])
        counts = _summary_counts([player])

        self.assertEqual(counts["claims_made"], 2)
        self.assertIn("Trick &amp; Treat + Limited-Time Event Rewards", section)
        self.assertIn("Lost Soul Rewards", section)
        self.assertIn("1,100 Lost Souls", section)
        self.assertIn("next tier: 1,300", section)
        self.assertIn("2/14 delivered", section)
        self.assertIn("400 points", section)
        self.assertIn("900 points", section)
        self.assertIn("Event tiers claimed:", section)
        self.assertIn("<strong>2</strong>", section)
        self.assertIn("Reward details unavailable", section)


if __name__ == "__main__":
    unittest.main()
