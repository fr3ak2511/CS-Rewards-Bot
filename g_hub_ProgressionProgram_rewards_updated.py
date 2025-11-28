import time
import random
import json
import threading

print_lock = threading.Lock()

def thread_safe_print(*args, **kwargs):
    with print_lock:
        print(*args, **kwargs)

def process_batch(batch, batch_num):
    results = []
    for player in batch:
        time.sleep(random.uniform(0.05, 0.1))
        results.append({
            "player_id": player,
            "login_successful": random.choice([True, True, False]),
            "monthly_rewards": random.randint(0, 3)
        })
    thread_safe_print(f"Batch {batch_num}: processed {len(batch)} players")
    return results

def main():
    start_time = time.time()

    # --- Simulated Data ---
    players = [f"Player_{i}" for i in range(1, 51)]
    batch_size = 10
    batches = [players[i:i + batch_size] for i in range(0, len(players), batch_size)]

    all_results = []
    for batch_num, batch in enumerate(batches, 1):
        batch_results = process_batch(batch, batch_num)
        all_results.extend(batch_results)
        if batch_num < len(batches):
            time.sleep(0.5)

    total_time = time.time() - start_time
    total_players = len(all_results)
    successful_logins = sum(1 for r in all_results if r["login_successful"])
    total_monthly = sum(r["monthly_rewards"] for r in all_results)
    avg_time_per_id = total_time / total_players if total_players > 0 else 0

    # --- Summary ---
    thread_safe_print("\n" + "-" * 70)
    thread_safe_print("PROGRESSION PROGRAM - FINAL SUMMARY")
    thread_safe_print("-" * 70)
    thread_safe_print(f"Total Players: {total_players}")
    thread_safe_print(f"Total Successful Logins: {successful_logins}")
    thread_safe_print(f"Total Monthly Rewards Claimed: {total_monthly}")
    thread_safe_print(f"Total Time Taken: {total_time:.1f}s ({total_time/60:.1f} minutes)")
    thread_safe_print(f"Avg. Time per ID: {avg_time_per_id:.1f}s")
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
