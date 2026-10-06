"""
In-process metrics store.
Kept deliberately simple — a dict that the health endpoint reads
and the Sentinel detector polls.
"""
import time
import threading
from collections import deque

_lock = threading.Lock()

_state: dict = {
    "total_requests": 0,
    "total_errors": 0,
    "latency_window": deque(maxlen=100),   # last 100 request durations (ms)
    "started_at": time.time(),
    "version": "1.0.0",
}


def record_request(duration_ms: float, is_error: bool) -> None:
    with _lock:
        _state["total_requests"] += 1
        if is_error:
            _state["total_errors"] += 1
        _state["latency_window"].append(duration_ms)


def snapshot() -> dict:
    with _lock:
        window = list(_state["latency_window"])
        total = _state["total_requests"]
        errors = _state["total_errors"]

    if window:
        sorted_w = sorted(window)
        p50 = sorted_w[int(len(sorted_w) * 0.50)]
        p95 = sorted_w[int(len(sorted_w) * 0.95)]
        avg = sum(sorted_w) / len(sorted_w)
    else:
        p50 = p95 = avg = 0.0

    error_rate = (errors / total * 100) if total else 0.0

    return {
        "total_requests": total,
        "total_errors": errors,
        "error_rate_pct": round(error_rate, 2),
        "latency_avg_ms": round(avg, 1),
        "latency_p50_ms": round(p50, 1),
        "latency_p95_ms": round(p95, 1),
        "uptime_seconds": round(time.time() - _state["started_at"], 1),
        "version": _state["version"],
    }


def set_version(v: str) -> None:
    with _lock:
        _state["version"] = v
