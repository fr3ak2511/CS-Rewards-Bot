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

def process_batch(batch, batch_num):
    results = []
    for player in batch:
        time.sleep(random.uniform(0.05, 0.1))  # simulate processing time
        result = {
            "player_id": player,
            "login_successful": random.choice([True, True, False]),
            "monthly_rewards": random.randint(0, 3),
        }
        results.append(result)
    thread_safe_print(f"✅ Batch {batch_num} processed ({len(batch)} players)")
    return results

def main():
    start_time = time.time()

    # --- Load Players ---
    players = read_players_from_csv("players.csv")
    scheduled_player_count = len(players)
    if scheduled_player_count == 0:
        thread_safe_print("⚠️ No players found in players.csv")
        return

    batch_size = 5
    batches = [players[i:i + batch_size] for i in range(0, len(players), batch_size)]

    all_results = []
    by_player = {}

    for batch_num, batch in enumerate(batches, 1):
        batch_results = process_batch(batch, batch_num)
        for r in batch_results:
            pid = r.get("player_id")
            if not pid:
                continue
            prev = by_player.get(pid)
            if prev is None or (r.get("login_successful") and not prev.get("login_successful")):
                by_player[pid] = r
        if batch_num < len(batches):
            time.sleep(0.5)

    all_results = [by_player[pid] for pid in players if pid in by_player]
    total_time = time.time() - start_time

    total_players = scheduled_player_count
    successful_logins = sum(1 for r in all_results if r.get("login_successful"))
    total_monthly = sum(r.get("monthly_rewards", 0) for r in all_results)
    avg_time_per_id = total_time / total_players if total_players > 0 else 0

    # --- Summary ---
    thread_safe_print("\n" + "-" * 70)
    thread_safe_print("PROGRESSION PROGRAM - FINAL SUMMARY")
    thread_safe_print("-" * 70)
    thread_safe_print(f"Total Players: {total_players}")
    thread_safe_print(f"Successful Logins: {successful_logins}")
    thread_safe_print(f"Total Monthly Rewards Claimed: {total_monthly}")
    thread_safe_print(f"Total Time Taken: {total_time:.1f}s ({total_time/60:.1f} minutes)")
    thread_safe_print(f"Avg Time per ID: {avg_time_per_id:.1f}s")
    thread_safe_print("-" * 70)

    summary_text = (
        "\n============================\n"
        "PROGRESSION PROGRAM SUMMARY\n"
        "============================\n"
        f"Total Players: {total_players}\n"
        f"Successful Logins: {successful_logins}\n"
        f"Total Monthly Rewards Claimed: {total_monthly}\n"
        f"Total Time Taken: {total_time:.1f}s ({total_time/60:.1f} minutes)\n"
        f"Avg Time per ID: {avg_time_per_id:.1f}s\n"
    )

    with open("workflow_summary.log", "w", encoding="utf-8") as f:
        f.write(summary_text)

    print(summary_text)

if __name__ == "__main__":
    main()
