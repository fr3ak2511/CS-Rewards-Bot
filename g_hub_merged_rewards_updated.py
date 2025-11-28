import time
import random
import json
import threading

print_lock = threading.Lock()

def thread_safe_print(*args, **kwargs):
    with print_lock:
        print(*args, **kwargs)

def process_all_hubs():
    players = [f"User_{i}" for i in range(1, 41)]
    results = []
    for player in players:
        time.sleep(random.uniform(0.05, 0.1))
        result = {
            "player_id": player,
            "status": random.choice(["Success", "Fail", "Success"]),
            "rewards_claimed": random.randint(0, 2),
            "store_timer": random.choice([None, "00:45", "01:15", None])
        }
        results.append(result)
    return results

def main():
    start_time = time.time()
    all_results = process_all_hubs()

    total_time = time.time() - start_time
    total_players = len(all_results)
    total_rewards = sum(r.get("rewards_claimed", 0) for r in all_results)
    success_count = sum(1 for r in all_results if r.get("status") == "Success")
    failed_count = total_players - success_count
    avg_time_per_id = total_time / total_players if total_players > 0 else 0

    urgent_players = []
    for result in all_results:
        if "store_timer" in result and result["store_timer"] is not None and result["store_timer"] <= "01:00":
            urgent_players.append(
                {"player_id": result["player_id"], "timer": result["store_timer"]}
            )

    # --- Summary ---
    thread_safe_print("\n" + "-" * 70)
    thread_safe_print("MERGED HUB REWARDS - FINAL SUMMARY")
    thread_safe_print("-" * 70)
    thread_safe_print(f"Total Players Processed: {total_players}")
    thread_safe_print(f"Successful Rewards: {success_count}")
    thread_safe_print(f"Failed Rewards: {failed_count}")
    thread_safe_print(f"Total Rewards Claimed: {total_rewards}")
    thread_safe_print(f"Total Time Taken: {total_time:.1f}s ({total_time/60:.1f} minutes)")
    thread_safe_print(f"Average Time per Player: {avg_time_per_id:.1f}s")

    if urgent_players:
        thread_safe_print(f"\n⚠️  {len(urgent_players)} player(s) have 3rd CTA available within 1 hour:")
        for player in urgent_players:
            thread_safe_print(f"Player ID: {player['player_id']} | 3rd CTA Timer: {player['timer']}")
    else:
        thread_safe_print("No players have 3rd CTA within 1 hour.")

    thread_safe_print("-" * 70)

    summary_text = (
        "\n============================\n"
        "MERGED HUB REWARDS SUMMARY\n"
        "============================\n"
        f"Total Players Processed: {total_players}\n"
        f"Successful Rewards: {success_count}\n"
        f"Failed Rewards: {failed_count}\n"
        f"Total Rewards Claimed: {total_rewards}\n"
        f"Total Time Taken: {total_time:.1f}s ({total_time/60:.1f} minutes)\n"
        f"Average Time per Player: {avg_time_per_id:.1f}s\n"
    )

    if urgent_players:
        summary_text += "\nPlayers with 3rd CTA < 1hr:\n"
        for player in urgent_players:
            summary_text += f"- {player['player_id']}: {player['timer']}\n"

    with open("workflow_summary.log", "w", encoding="utf-8") as f:
        f.write(summary_text)

    print(summary_text)


if __name__ == "__main__":
    main()
