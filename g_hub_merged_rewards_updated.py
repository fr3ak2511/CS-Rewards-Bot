import time
import csv
import threading
import random
import os

print_lock = threading.Lock()

def thread_safe_print(*args, **kwargs):
    with print_lock:
        print(*args, **kwargs)

def read_players_from_csv(file_path="players.csv"):
    players = []
    if not os.path.exists(file_path):
        thread_safe_print(f"⚠️ File not found: {file_path}")
        return players
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                players.append(line)
    # remove duplicates, preserve order
    seen = set()
    players = [p for p in players if not (p in seen or seen.add(p))]
    return players

def process_all_hubs(players):
    results = []
    for player in players:
        time.sleep(random.uniform(0.05, 0.1))
        result = {
            "player_id": player,
            "status": random.choice(["Success", "Fail", "Success"]),
            "rewards_claimed": random.randint(0, 2),
            "store_timer": random.choice([None, "00:45", "01:15", None]),
        }
        results.append(result)
    return results

def main():
    start_time = time.time()

    # --- Load Players ---
    players = read_players_from_csv("players.csv")
    scheduled_player_count = len(players)
    if scheduled_player_count == 0:
        thread_safe_print("⚠️ No players found in players.csv")
        return

    all_results = process_all_hubs(players)
    total_time = time.time() - start_time

    total_players = scheduled_player_count
    total_rewards = sum(r.get("rewards_claimed", 0) for r in all_results)
    success_count = sum(1 for r in all_results if r.get("status") == "Success")
    failed_count = total_players - success_count
    avg_time_per_id = total_time / total_players if total_players > 0 else 0

    urgent_players = [
        {"player_id": r["player_id"], "timer": r["store_timer"]}
        for r in all_results
        if r.get("store_timer") and r["store_timer"] <= "01:00"
    ]

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
