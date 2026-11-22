"""Fail-closed validation for Progression queue-only execution entry points."""


def is_valid_progression_queue_dispatch(
    *,
    event_name: str,
    source_run_id: str,
    recovery_slot: str,
    manual_progression_recovery: bool,
    manual_progression_player_id: str,
    daily_only_recovery: bool = False,
    manual_daily_recovery: bool = False,
    manual_daily_player_id: str = "",
) -> bool:
    """Validate the dispatch route before a queue-only Progression browser run.

    The repository_dispatch route remains the automated recovery entry point.
    A workflow_dispatch is allowed only for the separately validated, exact
    one-player Manual Run mode. Queue membership, source-run binding, roster
    membership, and slot eligibility are rechecked by the claimer after it
    acquires the shared rewards-claimer lock.
    """
    event = str(event_name or "").strip()
    source = str(source_run_id or "").strip()
    slot = str(recovery_slot or "").strip()
    manual_player = str(manual_progression_player_id or "").strip()
    daily_player = str(manual_daily_player_id or "").strip()

    if daily_only_recovery or manual_daily_recovery or daily_player:
        return False

    if event == "repository_dispatch":
        return bool(source) and not manual_progression_recovery and not manual_player

    if event == "workflow_dispatch":
        return (
            manual_progression_recovery is True
            and bool(manual_player)
            and source.isdigit()
            and slot == "Manual Run"
        )

    return False


__all__ = ["is_valid_progression_queue_dispatch"]
