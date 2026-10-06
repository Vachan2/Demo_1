#!/usr/bin/env python3
"""
scripts/load_gen.py

Standalone load generator for the demo.
Fires 100 requests at 20 concurrency and prints a live summary.

Usage:
  python scripts/load_gen.py [--total 100] [--concurrency 20] [--url http://localhost:9000]
"""
import argparse
import asyncio
import sys
import time

import httpx

ORDER_IDS = [str(i) for i in range(123, 133)]


async def main(total: int, concurrency: int, base_url: str) -> None:
    sem = asyncio.Semaphore(concurrency)
    results = []

    async def worker(order_id: str) -> None:
        async with sem:
            start = time.perf_counter()
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.get(f"{base_url}/orders/{order_id}")
                    elapsed = (time.perf_counter() - start) * 1000
                    results.append((r.status_code, elapsed))
                    marker = "✓" if r.status_code < 500 else "✗"
                    print(f"  {marker}  GET /orders/{order_id}  →  {r.status_code}  ({elapsed:.0f}ms)")
            except Exception as exc:
                elapsed = (time.perf_counter() - start) * 1000
                results.append((503, elapsed))
                print(f"  ✗  GET /orders/{order_id}  →  TIMEOUT  ({elapsed:.0f}ms)")

    print(f"\n{'─'*60}")
    print(f"  Load generator: {total} requests × {concurrency} concurrent")
    print(f"  Target: {base_url}")
    print(f"{'─'*60}\n")

    t_start = time.perf_counter()
    tasks = [worker(ORDER_IDS[i % len(ORDER_IDS)]) for i in range(total)]
    await asyncio.gather(*tasks)
    duration = time.perf_counter() - t_start

    # Summary
    ok     = sum(1 for s, _ in results if s < 500)
    errors = sum(1 for s, _ in results if s >= 500)
    lats   = sorted(l for _, l in results)
    p95    = lats[int(len(lats) * 0.95)] if lats else 0
    avg    = sum(lats) / len(lats) if lats else 0
    err_rt = errors / total * 100 if total else 0

    print(f"\n{'─'*60}")
    print(f"  SUMMARY")
    print(f"{'─'*60}")
    print(f"  Total requests   {total}")
    print(f"  Successful       {ok}  ({100-err_rt:.1f}%)")
    print(f"  Failed           {errors}  ({err_rt:.1f}%)")
    print(f"  P95 latency      {p95:.0f}ms")
    print(f"  Avg latency      {avg:.0f}ms")
    print(f"  Duration         {duration:.1f}s")
    print(f"{'─'*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--total", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--url", default="http://localhost:9000")
    args = parser.parse_args()
    asyncio.run(main(args.total, args.concurrency, args.url))
