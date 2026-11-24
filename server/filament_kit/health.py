"""Health history view built from the event store's check conditions (the design, Hub page s4.2, 09 Stage 13 )."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import events
from .alerts_api import say

# the conditions the history follows: a check row, a printer offline, a device down, storage low; each
# kind's condition key starts with its base, and the group names what the row is about
GROUPS = {"check": "checks", "printer-offline": "printers", "device-down": "devices", "storage-low": "storage"}


def _utc(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.strip().replace("Z", "+00:00")).astimezone(timezone.utc)


def _base(key: str) -> str:
    return key.split(":", 1)[0]


def _condition_key(ev: dict) -> str:
    """The key of the condition an event raised or cleared, "" for an event that has none."""
    if ev.get("kind") == "condition.cleared":
        return (ev.get("data") or {}).get("key", "")
    return (ev.get("condition") or {}).get("key", "")


def _state_from_event(ev: dict) -> str:
    if (ev.get("condition") or {}).get("state") == "cleared" or ev.get("kind") == "condition.cleared":
        return "ok"
    sev = ev.get("severity", "warn")
    return "bad" if sev == "bad" else "look"


def _check_key_of(ev: dict) -> str:
    """The row an event belongs to: a check's key without its "check:", another condition's whole key, "" for none."""
    ckey = _condition_key(ev)
    if _base(ckey) not in GROUPS:
        return ""
    return ckey[len("check:"):] if ckey.startswith("check:") else ckey


def _row_text(ev: dict, fallback: str) -> str:
    """What a row is called: a check's own text, else the name of the printer or device it is about."""
    base = _base(_condition_key(ev))
    if ev.get("kind") == "condition.cleared":
        return fallback
    if base == "storage-low":
        return say("health_row_storage")
    if base == "check":
        return (ev.get("data") or {}).get("text") or (ev.get("subject") or {}).get("name") or fallback
    sub = ev.get("subject") or {}
    return sub.get("name") or sub.get("id") or fallback


def health_view(days: int = 7, *, store=None, now: datetime | None = None,
                current_checks: list[dict] | None = None) -> dict:
    """Build the health history view for the last `days` (7 or 30)."""
    days = 30 if int(days or 7) == 30 else 7
    to_dt = _utc(now)
    from_dt = to_dt - timedelta(days=days)
    from_iso = from_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    to_iso = to_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    st = store if store is not None else events.store()
    all_evs = st.all_events()
    since = all_evs[0]["at"] if all_evs else None

    by_key: dict[str, list[dict]] = {}
    group_of: dict[str, str] = {}
    for ev in all_evs:
        key = _check_key_of(ev)
        if not key:
            continue
        by_key.setdefault(key, []).append(ev)
        group_of[key] = GROUPS[_base(_condition_key(ev))]

    current_by_key: dict[str, dict] = {}
    for row in (current_checks or ()):
        if row.get("key"):
            current_by_key[row["key"]] = row

    all_keys = sorted(set(by_key) | set(current_by_key))
    checks: list[dict] = []
    changes: list[dict] = []

    for key in all_keys:
        evs = sorted(by_key.get(key, ()), key=lambda e: (_parse_iso(e["at"]), e["id"]))
        state = "ok"
        text = (current_by_key.get(key) or {}).get("text") or key
        group = (current_by_key.get(key) or {}).get("group") or group_of.get(key, "")
        spans: list[dict] = []
        open_span: dict | None = None

        for ev in evs:
            ev_dt = _parse_iso(ev["at"])
            if ev_dt > to_dt:
                break
            text = _row_text(ev, text) or text
            new_state = _state_from_event(ev)
            if ev_dt < from_dt:
                state = new_state
                continue
            if open_span is None and state in ("look", "bad"):
                open_span = {"from": from_iso, "state": state}
            if new_state != state:
                changes.append({"at": ev["at"], "key": key, "text": text, "from": state, "to": new_state})
                if open_span is not None:
                    open_span["to"] = ev["at"]
                    spans.append(open_span)
                    open_span = None
                if new_state in ("look", "bad"):
                    open_span = {"from": ev["at"], "state": new_state}
                state = new_state

        if open_span is None and state in ("look", "bad"):
            open_span = {"from": from_iso, "state": state}
        if open_span is not None:
            open_span["to"] = to_iso
            spans.append(open_span)

        now_state = state
        if key in current_by_key:
            cur_s = current_by_key[key].get("now") or current_by_key[key].get("state")
            if cur_s in ("ok", "look", "bad"):
                now_state = cur_s

        checks.append({
            "key": key,
            "group": group,
            "text": text,
            "now": now_state,
            "spans": spans,
        })

    changes.sort(key=lambda c: c["at"], reverse=True)
    out: dict = {"from": from_iso, "to": to_iso, "checks": checks, "changes": changes}
    if since:
        out["since"] = since
    return out
