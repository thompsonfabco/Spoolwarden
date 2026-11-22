"""Health history view built from the event store's check conditions (docs/design 03 s4.2, 09 Stage 13 HD1)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import events


def _utc(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.strip().replace("Z", "+00:00")).astimezone(timezone.utc)


def _state_from_event(ev: dict) -> str:
    kind = ev.get("kind", "")
    if kind in ("check.ok", "condition.cleared"):
        return "ok"
    sev = ev.get("severity", "warn")
    return "bad" if sev == "bad" else "look"


def _check_key_of(ev: dict) -> str:
    kind = ev.get("kind", "")
    if kind == "condition.cleared":
        k = (ev.get("data") or {}).get("key", "")
        return k[len("check:"):] if k.startswith("check:") else ""
    cond = ev.get("condition") or {}
    ckey = cond.get("key", "")
    if ckey.startswith("check:"):
        return ckey[len("check:"):]
    return (ev.get("subject") or {}).get("id", "")


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
    for ev in all_evs:
        kind = ev.get("kind", "")
        if kind not in ("check.failed", "check.ok", "condition.cleared"):
            continue
        key = _check_key_of(ev)
        if not key:
            continue
        by_key.setdefault(key, []).append(ev)

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
        group = (current_by_key.get(key) or {}).get("group") or ""
        spans: list[dict] = []
        open_span: dict | None = None

        for ev in evs:
            ev_dt = _parse_iso(ev["at"])
            if ev_dt > to_dt:
                break
            ev_text = (ev.get("data") or {}).get("text") or (ev.get("subject") or {}).get("name") or text
            if ev_text:
                text = ev_text
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
