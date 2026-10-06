#!/usr/bin/env python3
"""
scripts/demo_runner.py

Interactive demo script — walks through all 10 stages with prompts.
Run this in a terminal alongside the dashboard open in a browser.

Usage:
  python scripts/demo_runner.py
"""
import asyncio
import sys
import time

import httpx

ORDER_API  = "http://localhost:9000"
SENTINEL   = "http://localhost:8001"
DASHBOARD  = "http://localhost:9000"   # served statically; adjust if separate


def title(text: str) -> None:
    print(f"\n{'═'*64}")
    print(f"  {text}")
    print(f"{'═'*64}\n")


def info(text: str) -> None:
    print(f"  ℹ  {text}")


def success(text: str) -> None:
    print(f"  ✓  {text}")


def warn(text: str) -> None:
    print(f"  ⚠  {text}")


def prompt(text: str = "Press Enter to continue...") -> None:
    input(f"\n  ▶  {text}")


async def get(url: str) -> dict:
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(url)
        return r.json()


async def post(url: str, **kwargs) -> dict:
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.post(url, **kwargs)
        return r.json()


async def wait_for_status(incident_id: str, target_status: str, timeout: int = 120) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = await get(f"{SENTINEL}/api/v1/incidents/{incident_id}")
        status = data.get("status", "")
        print(f"\r  ↻  {incident_id} status: {status:<35}", end="", flush=True)
        if status == target_status or status == "RESOLVED" or status == "FAILED":
            print()
            return status == target_status or status == "RESOLVED"
        await asyncio.sleep(2)
    print()
    return False


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------

async def stage1_healthy():
    title("STAGE 1 — Everything is healthy")
    data = await get(f"{ORDER_API}/health")
    m = data["metrics"]
    p = data["db_pool"]
    print(f"""
  ┌──────────────────────────────────┐
  │  E-Commerce API                  │
  │                                  │
  │  Status:        {data['status']:<17} │
  │  Version:       {data['version']:<17} │
  │  Requests:      {m['total_requests']:<17} │
  │  Errors:        {m['total_errors']:<17} │
  │  DB Connections:{p['used']}/{p['max']}               │
  │  P95 Latency:   {m['latency_p95_ms']:.0f}ms              │
  └──────────────────────────────────┘""")

    prompt("Make a sample request: GET /orders/123")
    r = await get(f"{ORDER_API}/orders/123")
    success(f"Order 123: {r}")

    prompt("Run a small warm-up (10 requests)...")
    for i in range(10):
        await get(f"{ORDER_API}/orders/{123 + (i % 10)}")
        print(f"\r  ✓  {i+1}/10 requests complete", end="", flush=True)
    print()
    success("All requests successful. System is healthy.")


async def stage2_inject():
    title("STAGE 2 — Deploy the buggy version (v1.1.0)")
    print("""  Diff:

    - async with pool.acquire() as conn:
    + conn = await pool.acquire()
    +
      try:
          return await conn.fetchrow(...)
      except Exception:
          raise
    # Missing: await pool.release(conn)
  """)
    prompt("Activate failure mode: DEMO_FAILURE_MODE=connection_leak")
    r = await post(f"{ORDER_API}/admin/failure?mode=connection_leak")
    warn(f"Failure mode activated: {r['failure_mode']}")
    info("The API now leaks one connection per request to /orders/{id}")


async def stage3_load():
    title("STAGE 3 — Generate traffic (100 req × 20 concurrent)")
    prompt("Start load generator...")

    ORDER_IDS = [str(i) for i in range(123, 133)]
    sem = asyncio.Semaphore(20)
    results = []

    async def worker(order_id: str) -> None:
        async with sem:
            start = time.perf_counter()
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.get(f"{ORDER_API}/orders/{order_id}")
                    elapsed = (time.perf_counter() - start) * 1000
                    results.append((r.status_code, elapsed))
                    marker = "✓" if r.status_code < 500 else "✗"
                    print(f"  {marker}  GET /orders/{order_id}  {r.status_code}  ({elapsed:.0f}ms)")
            except Exception:
                elapsed = (time.perf_counter() - start) * 1000
                results.append((503, elapsed))
                print(f"  ✗  GET /orders/{order_id}  TIMEOUT  ({elapsed:.0f}ms)")

    tasks = [worker(ORDER_IDS[i % len(ORDER_IDS)]) for i in range(100)]
    await asyncio.gather(*tasks)

    ok     = sum(1 for s, _ in results if s < 500)
    errors = sum(1 for s, _ in results if s >= 500)
    lats   = sorted(l for _, l in results)
    p95    = lats[int(len(lats) * 0.95)] if lats else 0
    err_rt = errors / 100 * 100

    print(f"""
  ┌──────────────────────────────────┐
  │  Load Test Results               │
  │                                  │
  │  Successful:  {ok}/100            │
  │  Failed:      {errors}/100            │
  │  Error rate:  {err_rt:.1f}%               │
  │  P95 latency: {p95:.0f}ms               │
  └──────────────────────────────────┘""")

    data = await get(f"{ORDER_API}/metrics/raw")
    p = data["pool"]
    warn(f"DB Pool: {p['used']}/{p['max']} connections used")


async def stage4_detect():
    title("STAGE 4 — Sentinel detects the incident")
    info("Sentinel receives metrics and checks thresholds...")
    await asyncio.sleep(3)

    try:
        data = await get(f"{SENTINEL}/api/v1/incidents")
        incidents = data.get("incidents", [])
        if incidents:
            inc = incidents[-1]
            print(f"""
  ┌──────────────────────────────────────────────────┐
  │  INCIDENT CREATED                                │
  │                                                  │
  │  ID:       {inc['incident_id']:<38} │
  │  Service:  {inc['service']:<38} │
  │  Severity: {inc['severity']:<38} │
  │                                                  │
  │  Symptoms:""")
            for s in inc.get("symptoms", []):
                print(f"  │    • {s[:44]:<44} │")
            print(f"  └──────────────────────────────────────────────────┘")
            return inc["incident_id"]
        else:
            warn("No incident detected yet — metrics may not have crossed threshold")
            warn("Try: POST http://localhost:8001/api/v1/incidents?service=order-api")
    except Exception as e:
        warn(f"Sentinel unreachable: {e}")
    return None


async def stage5_to_10(incident_id: str | None):
    if not incident_id:
        warn("Skipping pipeline stages — no incident ID")
        return

    title("STAGE 5–6 — Sentinel investigates (context + RCA)")
    info(f"Pipeline running for {incident_id}...")
    await wait_for_status(incident_id, "ROOT_CAUSE_IDENTIFIED", timeout=60)

    data = await get(f"{SENTINEL}/api/v1/incidents/{incident_id}")
    if data.get("rca"):
        print(f"""
  ┌──────────────────────────────────────────────────┐
  │  ROOT CAUSE ANALYSIS                             │
  │                                                  │
  │  Confidence: {(data['rca_confidence'] or 0)*100:.0f}%                              │
  │                                                  │
  │  {data['rca'][:46]:<46} │""")
        for c in (data.get("candidate_causes") or [])[:4]:
            line = f"{c['cause'][:36]:<36}  {c['confidence_pct']}%"
            print(f"  │  {line:<48} │")
        print("  └──────────────────────────────────────────────────┘")

    title("STAGE 7 — Generating fix branch + PR")
    await wait_for_status(incident_id, "REMEDIATION_CREATED", timeout=60)
    data = await get(f"{SENTINEL}/api/v1/incidents/{incident_id}")
    if data.get("fix_branch"):
        success(f"Branch: {data['fix_branch']}")
        success(f"PR:     {data['fix_pr_url']}")

    title("STAGE 8 — Validation")
    await wait_for_status(incident_id, "PATCH_VALIDATED", timeout=120)
    data = await get(f"{SENTINEL}/api/v1/incidents/{incident_id}")
    vr = data.get("validation_result") or {}
    print(f"""
  Unit tests:          {vr.get('unit_tests',{}).get('passed',0)} PASS / {vr.get('unit_tests',{}).get('failed',0)} FAIL
  Integration tests:   {vr.get('integration_tests',{}).get('passed',0)} PASS / {vr.get('integration_tests',{}).get('failed',0)} FAIL
  Lint:                {'PASS' if (vr.get('lint') or {}).get('passed') else 'FAIL'}
  Connection leak test:{'PASS' if (vr.get('connection_leak_test') or {}).get('passed') else 'FAIL'}
    """)

    title("STAGE 9 — PR on GitHub (or local branch)")
    info(f"PR #{data.get('fix_pr_number')} — {data.get('fix_pr_url')}")

    title("STAGE 10 — Verification")
    info("Resetting failure mode to 'none' to simulate deployment of fix...")
    await post(f"{ORDER_API}/admin/failure?mode=none")
    await wait_for_status(incident_id, "RESOLVED", timeout=180)

    data = await get(f"{SENTINEL}/api/v1/incidents/{incident_id}")
    vr2 = data.get("verification_result") or {}
    b = vr2.get("before") or {}
    a = vr2.get("after") or {}

    print(f"""
  {'':20} {'BEFORE':>10}   {'AFTER':>10}
  {'─'*42}
  5xx error rate   {str(b.get('error_rate_pct','?'))+'%':>10}   {str(a.get('error_rate_pct','?'))+'%':>10}
  P95 latency      {str(b.get('p95_latency_ms','?'))+'ms':>10}   {str(a.get('p95_latency_ms','?'))+'ms':>10}
  DB pool usage    {str(b.get('peak_pool_utilisation_pct','?'))+'%':>10}   {str(a.get('peak_pool_utilisation_pct','?'))+'%':>10}
    """)

    if data.get("status") == "RESOLVED":
        success(f"✓  Incident {incident_id} RESOLVED")
    else:
        warn(f"Incident status: {data.get('status')}")

    title("DEMO COMPLETE")
    print("""
  INC-001
  DETECTED
     ↓
  INVESTIGATING
     ↓
  ROOT CAUSE IDENTIFIED
     ↓
  REMEDIATION CREATED
     ↓
  PATCH VALIDATED
     ↓
  DEPLOYED
     ↓
  VERIFICATION PASSED
     ↓
  RESOLVED
    """)


async def main():
    print("\n  ╔══════════════════════════════════════════════════════╗")
    print("  ║              SENTINEL DEMO RUNNER                   ║")
    print("  ╚══════════════════════════════════════════════════════╝")
    print("\n  Dashboard: open dashboard/index.html in your browser")
    print(f"  Order API:  {ORDER_API}")
    print(f"  Sentinel:   {SENTINEL}")
    prompt("Ready to begin? Press Enter...")

    await stage1_healthy()
    await stage2_inject()
    await stage3_load()
    incident_id = await stage4_detect()
    await stage5_to_10(incident_id)


if __name__ == "__main__":
    asyncio.run(main())
