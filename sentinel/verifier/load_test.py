"""
verifier/load_test.py

Reproduces the original workload against the order-api and collects metrics.
Used both to confirm the failure (before fix) and to verify the fix (after fix).

Workload: 100 requests, 20 concurrent, cycling through order IDs 123–132.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

from config import settings

logger = logging.getLogger("sentinel.verifier.load_test")

ORDER_IDS = [str(i) for i in range(123, 133)]  # 10 IDs
TOTAL_REQUESTS = 100
CONCURRENCY = 20
REQUEST_TIMEOUT = 10.0


@dataclass
class LoadTestResult:
    total: int = 0
    successful: int = 0
    failed: int = 0
    latencies_ms: list = field(default_factory=list)
    status_counts: dict = field(default_factory=dict)
    pool_samples: list = field(default_factory=list)  # (used, max) tuples
    duration_s: float = 0.0

    @property
    def error_rate_pct(self) -> float:
        if self.total == 0:
            return 0.0
        return (self.failed / self.total) * 100

    @property
    def p95_latency_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        s = sorted(self.latencies_ms)
        return s[int(len(s) * 0.95)]

    @property
    def avg_latency_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        return sum(self.latencies_ms) / len(self.latencies_ms)

    @property
    def peak_pool_utilisation_pct(self) -> float:
        if not self.pool_samples:
            return 0.0
        peak_used = max(s[0] for s in self.pool_samples)
        max_size = self.pool_samples[0][1] if self.pool_samples else 10
        return (peak_used / max_size) * 100 if max_size else 0.0

    def summary(self) -> dict:
        return {
            "total_requests": self.total,
            "successful": self.successful,
            "failed": self.failed,
            "error_rate_pct": round(self.error_rate_pct, 1),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "p95_latency_ms": round(self.p95_latency_ms, 1),
            "peak_pool_utilisation_pct": round(self.peak_pool_utilisation_pct, 1),
            "status_counts": self.status_counts,
            "duration_s": round(self.duration_s, 2),
        }


async def _sample_pool(result: LoadTestResult, stop_event: asyncio.Event) -> None:
    """Continuously poll /metrics/raw to capture pool utilisation."""
    async with httpx.AsyncClient(timeout=2.0) as client:
        while not stop_event.is_set():
            try:
                r = await client.get(f"{settings.order_api_url}/metrics/raw")
                data = r.json()
                pool = data.get("pool", {})
                result.pool_samples.append(
                    (pool.get("used", 0), pool.get("max", 10))
                )
            except Exception:
                pass
            await asyncio.sleep(0.5)


async def _worker(
    client: httpx.AsyncClient,
    order_id: str,
    result: LoadTestResult,
    sem: asyncio.Semaphore,
) -> None:
    async with sem:
        start = time.perf_counter()
        try:
            r = await client.get(
                f"{settings.order_api_url}/orders/{order_id}",
                timeout=REQUEST_TIMEOUT,
            )
            elapsed_ms = (time.perf_counter() - start) * 1000
            result.latencies_ms.append(elapsed_ms)
            result.total += 1
            status = str(r.status_code)
            result.status_counts[status] = result.status_counts.get(status, 0) + 1
            if r.status_code >= 500:
                result.failed += 1
            else:
                result.successful += 1
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            result.latencies_ms.append(elapsed_ms)
            result.total += 1
            result.failed += 1
            result.status_counts["timeout"] = result.status_counts.get("timeout", 0) + 1
            logger.debug("request_error", extra={"order_id": order_id, "error": str(exc)})


async def run(
    total: int = TOTAL_REQUESTS,
    concurrency: int = CONCURRENCY,
) -> LoadTestResult:
    result = LoadTestResult()
    sem = asyncio.Semaphore(concurrency)
    stop_event = asyncio.Event()

    logger.info(
        "load_test_start",
        extra={"total": total, "concurrency": concurrency, "url": settings.order_api_url},
    )

    t_start = time.perf_counter()

    async with httpx.AsyncClient() as client:
        sampler = asyncio.create_task(_sample_pool(result, stop_event))

        # Round-robin across order IDs
        tasks = [
            _worker(client, ORDER_IDS[i % len(ORDER_IDS)], result, sem)
            for i in range(total)
        ]
        await asyncio.gather(*tasks)

    stop_event.set()
    await sampler

    result.duration_s = time.perf_counter() - t_start

    logger.info("load_test_complete", extra=result.summary())
    return result
