"""Event undo rules and execution (docs/design 03 section 2.9, 09 Stage 13 HD1)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from . import event_sink, events, tags

UNDOABLE_KINDS: frozenset[str] = frozenset({
    "spool.weighed",
    "spool.moved",
    "spool.emptied",
    "spool.opened",
    "spool.loaded",
    "spool.unloaded",
    "slot.cleared",
})

UNDO_WINDOW = timedelta(days=7)
LOAD_UNLOAD_NOTE = "The printer still shows what it was set to."

WHY_NOT_TEXTS: dict[str, str] = {
    "changed_since": "This changed again since, so it cannot be undone.",
    "too_old": "This is older than 7 days, so it cannot be undone.",
    "already_undone": "This was already undone.",
    "not_undoable": "This cannot be undone.",
}


class EventNotFound(LookupError):
    """No event with this id exists in the store."""


class UndoError(Exception):
    """The event cannot be undone; `.code` is one of the `why_not` values."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        self.message = message or WHY_NOT_TEXTS.get(code, "This cannot be undone.")
        super().__init__(self.message)


def _utc(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.strip().replace("Z", "+00:00")).astimezone(timezone.utc)


def _slots_of(ev: dict) -> set[str]:
    d = ev.get("data") or {}
    return {v for k in ("slot", "before_slot", "after_slot") if (v := d.get(k))}


def _matches_current_spool(ev: dict, cur: dict) -> bool:
    d = ev.get("data") or {}
    if "after_location" in d and (cur.get("location") or "") != d["after_location"]:
        return False
    if "after_grams" in d and d["after_grams"] != "":
        try:
            cur_g = round(float(cur.get("remaining_weight") or 0), 1)
            if cur_g != round(float(d["after_grams"]), 1):
                return False
        except (TypeError, ValueError):
            pass
    if "after_tag" in d and (tags.stored(cur) or "") != d["after_tag"]:
        return False
    if ev["kind"] == "spool.emptied" and not cur.get("archived"):
        return False
    return True


def evaluate(ev: dict, *, all_evs: list[dict] | None = None, undone_ids: set[int] | None = None,
             now: datetime | None = None, sm=None) -> dict:
    """Compute `{possible, until?, why_not?}` for one event."""
    if ev.get("kind") not in UNDOABLE_KINDS or ev.get("undo_of") is not None:
        return {"possible": False, "why_not": "not_undoable"}
    data = ev.get("data") or {}
    if ev["kind"] == "slot.cleared" and not (data.get("spool") or (ev.get("subject") or {}).get("id")):
        return {"possible": False, "why_not": "not_undoable"}

    if undone_ids is None and all_evs is not None:
        undone_ids = {e["undo_of"] for e in all_evs if e.get("undo_of") is not None}
    if undone_ids and ev["id"] in undone_ids:
        return {"possible": False, "why_not": "already_undone"}

    now_dt = _utc(now)
    ev_dt = _parse_iso(ev["at"])
    until_dt = ev_dt + UNDO_WINDOW
    if now_dt > until_dt:
        return {"possible": False, "why_not": "too_old"}

    subj = ev.get("subject") or {}
    sid = subj.get("id") if subj.get("type") == "spool" else ""
    my_slots = _slots_of(ev)

    if all_evs is not None:
        for later in all_evs:
            if later["id"] <= ev["id"] or later.get("kind") not in UNDOABLE_KINDS:
                continue
            lsubj = later.get("subject") or {}
            if sid and lsubj.get("type") == "spool" and lsubj.get("id") == sid:
                return {"possible": False, "why_not": "changed_since"}
            if my_slots and (my_slots & _slots_of(later)):
                return {"possible": False, "why_not": "changed_since"}

    if sm is not None and sid:
        try:
            cur = sm.get(f"spool/{sid}")
        except Exception:  # noqa: BLE001
            cur = None
        if isinstance(cur, dict) and not _matches_current_spool(ev, cur):
            return {"possible": False, "why_not": "changed_since"}

    return {"possible": True, "until": until_dt.strftime("%Y-%m-%dT%H:%M:%SZ")}


def enrich(evs: list[dict], *, all_evs: list[dict] | None = None,
           now: datetime | None = None, sm=None) -> list[dict]:
    """Attach `undone` and `undo` to each event dict in `evs`."""
    if all_evs is None:
        all_evs = events.store().all_events()
    undone_ids = {e["undo_of"] for e in all_evs if e.get("undo_of") is not None}
    for ev in evs:
        ev["undone"] = ev["id"] in undone_ids
        ev["undo"] = evaluate(ev, all_evs=all_evs, undone_ids=undone_ids, now=now, sm=sm)
    return evs


def undo_event(event_id: int, sm, *, store=None, now: datetime | None = None, actor=None) -> dict:
    """Undo event `event_id` in Spoolman `sm` and record the compensating event in `store`."""
    st = store if store is not None else events.store()
    try:
        eid = int(event_id)
    except (TypeError, ValueError):
        raise EventNotFound(f"there is no event {event_id}.") from None
    all_evs = st.all_events()
    ev = next((e for e in all_evs if e["id"] == eid), None)
    if ev is None:
        raise EventNotFound(f"there is no event {event_id}.")

    res = evaluate(ev, all_evs=all_evs, now=now, sm=sm)
    if not res["possible"]:
        raise UndoError(res["why_not"])

    kind = ev["kind"]
    subj = ev["subject"]
    sid = subj["id"]
    data = ev.get("data") or {}
    cur = sm.get(f"spool/{sid}")
    patch: dict = {}
    new_data: dict = {"spool": str(sid)}

    if kind == "spool.weighed":
        patch["remaining_weight"] = float(data["before_grams"])
        new_data.update(before_grams=data.get("after_grams"), after_grams=data["before_grams"])
    elif kind == "spool.moved":
        before_loc = data.get("before_location", "")
        patch["location"] = before_loc
        new_data.update(before_location=data.get("after_location"), after_location=before_loc)
    elif kind == "spool.emptied":
        before_arch = str(data.get("before_archived", "false")).lower() in ("true", "1", "yes")
        patch["remaining_weight"] = float(data["before_grams"])
        patch["archived"] = before_arch
        new_data.update(before_grams=data.get("after_grams"), after_grams=data["before_grams"],
                        before_archived="true")
    elif kind == "spool.opened":
        if "before_location" in data:
            patch["location"] = data["before_location"]
            new_data.update(before_location=data.get("after_location"), after_location=data["before_location"])
        if "before_grams" in data and data["before_grams"] != "":
            patch["remaining_weight"] = float(data["before_grams"])
            new_data.update(before_grams=data.get("after_grams"), after_grams=data["before_grams"])
        if "before_tag" in data:
            patch["extra"] = {**(cur.get("extra") or {}), "nfc_id": json.dumps(data["before_tag"])}
            new_data.update(before_tag=data.get("after_tag"), after_tag=data["before_tag"])
    elif kind == "spool.loaded":
        before_loc = data.get("before_location", "")
        patch = {"location": before_loc, "extra": {**(cur.get("extra") or {}), "prev_location": json.dumps("")}}
        new_data.update(before_location=data.get("after_location"), after_location=before_loc,
                        before_slot=data.get("after_slot", ""), after_slot=data.get("before_slot", ""))
    elif kind == "spool.unloaded":
        before_loc = data.get("before_location", "")
        after_loc = data.get("after_location", "")
        patch = {"location": before_loc,
                 "extra": {**(cur.get("extra") or {}), "prev_location": json.dumps(after_loc)}}
        new_data.update(before_location=after_loc, after_location=before_loc,
                        before_slot=data.get("after_slot", ""), after_slot=data.get("before_slot", ""))
    elif kind == "slot.cleared":
        before_loc = data.get("before_location", "")
        after_loc = data.get("after_location", "")
        patch = {"location": before_loc,
                 "extra": {**(cur.get("extra") or {}), "prev_location": json.dumps(after_loc)}}
        new_data.update(slot=data.get("slot", ""), before_location=after_loc, after_location=before_loc)

    sm.patch(f"spool/{sid}", patch)
    new_ev = st.write(kind, subj, actor or event_sink.current_actor(), new_data, undo_of=ev["id"])
    new_ev["undone"] = False
    new_ev["undo"] = {"possible": False, "why_not": "not_undoable"}
    out: dict = {"undone": True, "event": new_ev}
    if kind in ("spool.loaded", "spool.unloaded"):
        out["note"] = LOAD_UNLOAD_NOTE
    return out
