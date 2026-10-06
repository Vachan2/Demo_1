"""
Simple in-memory incident store with an auto-incrementing ID counter.
In production this would be backed by a database.
"""
import threading
from models import Incident

_lock = threading.Lock()
_incidents: dict[str, Incident] = {}
_counter: int = 0


def next_id() -> str:
    global _counter
    with _lock:
        _counter += 1
        return f"INC-{_counter:03d}"


def save(incident: Incident) -> None:
    with _lock:
        _incidents[incident.incident_id] = incident


def get(incident_id: str) -> Incident | None:
    with _lock:
        return _incidents.get(incident_id)


def all_incidents() -> list[Incident]:
    with _lock:
        return list(_incidents.values())


def latest() -> Incident | None:
    with _lock:
        if not _incidents:
            return None
        return max(_incidents.values(), key=lambda i: i.created_at)
