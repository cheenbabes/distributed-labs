#!/usr/bin/env python3
"""
Seed the leaderboard with test data.

This script populates the leaderboard with a configurable number of players
with randomized scores to simulate a real gaming environment.

Usage:
    python seed-data.py [--players 10000] [--max-score 100000] [--batch-size 1000]
"""
import argparse
import asyncio
import random
import string
import time

import redis.asyncio as redis


async def generate_player_id() -> str:
    """Generate a realistic-looking player ID."""
    prefixes = ["player", "gamer", "user", "pro", "ninja", "legend", "elite"]
    prefix = random.choice(prefixes)
    suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{prefix}_{suffix}"


async def seed_leaderboard(
    redis_client: redis.Redis,
    num_players: int,
    max_score: int,
    batch_size: int,
    leaderboard_key: str = "leaderboard:all_time"
):
    """
    Seed the leaderboard with random players and scores.

    Uses Redis pipeline for efficient batch inserts.
    """
    print(f"Seeding {num_players:,} players to '{leaderboard_key}'...")
    print(f"Max score: {max_score:,}, Batch size: {batch_size:,}")

    start_time = time.time()
    total_inserted = 0

    # Generate and insert in batches
    for batch_start in range(0, num_players, batch_size):
        batch_end = min(batch_start + batch_size, num_players)
        batch_count = batch_end - batch_start

        # Create pipeline for batch insert
        pipe = redis_client.pipeline()

        for _ in range(batch_count):
            player_id = await generate_player_id()
            # Use exponential distribution for more realistic score distribution
            # Most players have lower scores, few have very high scores
            score = int(random.expovariate(1 / (max_score / 5)))
            score = min(score, max_score)  # Cap at max
            pipe.zadd(leaderboard_key, {player_id: score})

        await pipe.execute()
        total_inserted += batch_count

        # Progress update
        progress = (total_inserted / num_players) * 100
        elapsed = time.time() - start_time
        rate = total_inserted / elapsed if elapsed > 0 else 0
        print(f"Progress: {progress:.1f}% ({total_inserted:,}/{num_players:,}) - {rate:.0f} players/sec")

    total_time = time.time() - start_time
    final_count = await redis_client.zcard(leaderboard_key)

    print(f"\nSeeding complete!")
    print(f"Total time: {total_time:.2f} seconds")
    print(f"Average rate: {num_players / total_time:.0f} players/second")
    print(f"Total players in leaderboard: {final_count:,}")

    # Show some stats
    print("\n--- Leaderboard Stats ---")
    top_10 = await redis_client.zrevrange(leaderboard_key, 0, 9, withscores=True)
    print("Top 10 players:")
    for i, (player, score) in enumerate(top_10, 1):
        print(f"  {i}. {player}: {score:,.0f}")

    # Score distribution
    print("\nScore distribution (percentiles):")
    for percentile in [99, 95, 90, 75, 50, 25, 10]:
        idx = int(final_count * (100 - percentile) / 100)
        result = await redis_client.zrevrange(leaderboard_key, idx, idx, withscores=True)
        if result:
            print(f"  P{percentile}: {result[0][1]:,.0f}")


async def seed_multiple_leaderboards(
    redis_client: redis.Redis,
    num_players: int,
    max_score: int,
    batch_size: int
):
    """Seed all leaderboard types with data."""
    from datetime import datetime

    leaderboards = [
        "leaderboard:all_time",
        f"leaderboard:daily:{datetime.utcnow().strftime('%Y-%m-%d')}",
        f"leaderboard:weekly:{datetime.utcnow().isocalendar()[0]}-W{datetime.utcnow().isocalendar()[1]:02d}"
    ]

    for lb in leaderboards:
        await seed_leaderboard(redis_client, num_players, max_score, batch_size, lb)
        print("\n" + "=" * 50 + "\n")


async def main():
    parser = argparse.ArgumentParser(description="Seed leaderboard with test data")
    parser.add_argument(
        "--players", "-p",
        type=int,
        default=10000,
        help="Number of players to create (default: 10000)"
    )
    parser.add_argument(
        "--max-score", "-m",
        type=int,
        default=100000,
        help="Maximum possible score (default: 100000)"
    )
    parser.add_argument(
        "--batch-size", "-b",
        type=int,
        default=1000,
        help="Batch size for Redis pipeline (default: 1000)"
    )
    parser.add_argument(
        "--redis-host",
        type=str,
        default="localhost",
        help="Redis host (default: localhost)"
    )
    parser.add_argument(
        "--redis-port",
        type=int,
        default=6379,
        help="Redis port (default: 6379)"
    )
    parser.add_argument(
        "--all-types",
        action="store_true",
        help="Seed all leaderboard types (all_time, daily, weekly)"
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear existing leaderboard data before seeding"
    )

    args = parser.parse_args()

    # Connect to Redis
    print(f"Connecting to Redis at {args.redis_host}:{args.redis_port}...")
    redis_client = redis.Redis(
        host=args.redis_host,
        port=args.redis_port,
        decode_responses=True
    )

    try:
        await redis_client.ping()
        print("Connected to Redis successfully!\n")

        if args.clear:
            print("Clearing existing leaderboard data...")
            keys = await redis_client.keys("leaderboard:*")
            if keys:
                await redis_client.delete(*keys)
                print(f"Deleted {len(keys)} leaderboard keys\n")

        if args.all_types:
            await seed_multiple_leaderboards(
                redis_client,
                args.players,
                args.max_score,
                args.batch_size
            )
        else:
            await seed_leaderboard(
                redis_client,
                args.players,
                args.max_score,
                args.batch_size
            )

    finally:
        await redis_client.close()


if __name__ == "__main__":
    asyncio.run(main())
