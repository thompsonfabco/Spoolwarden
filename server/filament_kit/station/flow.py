"""The intake flow on the button panel (the owner's spec, 2026-11-06): red = cancel, yellow = advance, long yellow =
save. Ported from filament-ops' intake_station.py (panel sync 5).

idle -> (tag) lookup -> camera (new tag) -> (yellow) recognising -> weigh -> (yellow) ready -> (long yellow) saving
-> done. A linked tag skips the camera (lookup -> weigh). "phone": the server or its AI can't help, use the phone app.

The kit server does the thinking, statelessly (filament_kit/station_flow.py on the server): `server_api.ServerApi`
looks the tag up, recognises the photo (Claude) and saves; every answer carries `display`, ready-made
OLED lines. The station keeps the flow: which step, the spool or recognised filament, the photo and the taken weight.
A sequence number drops any server answer that comes back after red or a new scan.

Timeouts: any step goes back to idle after STATION_FLOW_TIMEOUT (600 s) with no progress, and done after
DONE_SECONDS; a recognition waits up to STATION_RECOGNISE_TIMEOUT (90 s).
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
import unicodedata

from . import beep, camera, scale, server_api, speech
from . import state as st

log = logging.getLogger("intake-station")

# Lamps (yellow = Hold, red = Discard), modes 0 off, 1 on, 2 blink, 3 three blinks, 4 rapid blink
STEP_LAMPS = {"pairing": (2, 0), "idle": (0, 0), "lookup": (4, 1), "camera": (2, 1), "recognising": (4, 1), "weigh": (2, 1),
              "ready": (1, 1), "saving": (4, 1), "done": (3, 0), "phone": (0, 1)}
LAMP_CHOICES = ("off", "yellow", "red", "both")  # what the station's page offers for a step (settings.py)
DONE_SECONDS = 10  # done -> idle
TITLE_WIDTH, LINE_WIDTH = 16, 21  # the OLED's title row (bold) and body rows (6x10 font)

flow = st.flow


def ascii_text(text, width=LINE_WIDTH):
    """The panel's fonts are Latin-1 and its lines 21 characters: plain ASCII, cut to width."""
    return unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()[:width]


# ---- the kit server: one ServerApi, built again when `spoolwarden station pair` rewrites the pairing files -------

_api: dict = {"key": None, "api": None}


def _mtime(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def api() -> server_api.ServerApi:
    key = (_mtime(server_api.server_file()), _mtime(server_api.token_file()))
    if _api["api"] is None or _api["key"] != key:
        _api.update(key=key, api=server_api.ServerApi(timeout=st.settings["recognise_timeout"]))
    return _api["api"]


def spawn(fn, *args):
    """Run a server call off the reader / panel thread (tests run it inline)."""
    threading.Thread(target=fn, args=args, daemon=True).start()


# ---- the steps -----------------------------------------------------------------------------------------------------

def go(step, seq=None, **kw):
    """Move the flow to step (only if it is still on attempt seq, when given: a red press or a new tag since then
    drops the late result). Returns False if dropped."""
    with st.lock:
        if seq is not None and seq != flow["seq"]:
            return False
        flow.update(step=step, at=time.time(), message=kw.pop("message", ""), **kw)
    st.panel_dirty.set()
    return True


def computer_scale() -> bool:
    """The computer has a scale: it has sent a reading, or its USB device is there (a DYMO asleep sends nothing)."""
    with st.lock:
        seen = st.state["scale"] is not None
    return seen or scale.find_scale() is not None


def has_scale(computer) -> bool:
    """Is there a scale at all: the computer's (`computer`, from computer_scale), else the panel's, which the panel
    service keeps in st.state["panel_scale"] ({"grams", "status", "unit"} as "scale", or None) while a panel sends `W`
    lines (R88 4.2). The computer's scale wins when both exist."""
    return computer or st.state.get("panel_scale") is not None


def scale_reading(computer):
    """The reading of the scale in use (see has_scale), or None (callers hold the lock)."""
    return st.state["scale"] if computer else st.state.get("panel_scale")


def weight_now(computer):
    """The scale's reading if it is a settled spool (stable, over MIN_GRAMS), else None (callers hold the lock)."""
    sc = scale_reading(computer)
    return sc["grams"] if sc and sc["status"] == "stable" and sc["grams"] >= st.MIN_GRAMS else None


def on_tag(nfc_id):
    """A tap, in any step: start over with this tag and ask the server about it."""
    with st.lock:
        flow["seq"] += 1
        seq = flow["seq"]
        st.state.update(tag=nfc_id, tag_at=time.time(), held=None, held_at=None, photo=None, photo_at=None)
        flow.update(spool=None, filament=None, display=[], returning=False)
    log.info("tag %s", nfc_id)
    beep.beep("scan")
    go("lookup")
    spawn(lookup, seq, nfc_id)


def lookup(seq, nfc_id):
    """Is the tag on a spool already? Known: straight to weighing. New: show the spool to the camera."""
    status, r = api().tag_lookup(nfc_id)
    d = r.get("display") or []
    if status == 200 and r.get("known"):
        sp = r.get("spool") or {}
        back = sp.get("checked_out") is True  # an older Hub sends no `checked_out`: the plain flow (R88 1.8)
        if back:  # the Hub's display is [name, "Home: <home>"]; built from the spool when it is missing
            d = d[:2] if len(d) >= 2 else [sp.get("name") or "", "Home: " + (sp.get("home") or "not recorded")]
        if go("weigh", seq, spool=sp, display=d, returning=back):
            speech.say("Checked out spool. Press yellow to weigh it, or hold yellow to skip weighing." if back
                       else "Known spool. Put it on the scale.")
    elif status == 200 and not has_scale(computer_scale()):  # nothing could weigh a new spool: the phone can (R88 4.2)
        if go("phone", seq, display=["No scale here"]):
            speech.say("No scale here. Use the phone.")
    elif status == 200:
        if go("camera", seq, display=[]):
            speech.say("New tag. Show the spool to the camera.")
    elif go("phone", seq, display=d):
        speech.say("The server can't help. Use the phone.")


def recognise(seq, nfc_id):
    """Picture -> the server's recognition (Claude): the filament, or why not."""
    with st.lock:
        if seq != flow["seq"]:  # red or a new tag since yellow: nothing to recognise
            return
    jpeg, err = camera.grab_photo()
    if not jpeg:
        if err == "no camera":
            go("phone", seq, display=["No camera"])
        else:
            go("camera", seq, message="Camera failed, retry")
        beep.beep("nothing")
        return
    with st.lock:
        if seq != flow["seq"]:
            return
        st.state.update(photo=jpeg, photo_at=time.time())
    status, r = api().recognize(jpeg, nfc_id)
    d = r.get("display") or []
    if status == 200:  # low confidence too: its "?" is in the display, and the save still goes through
        if go("weigh", seq, filament=r.get("item"), display=d):
            beep.beep("hold")
            # the server's `speak` (the material spelled for the voice); an older Hub sends none: the display lines
            said = r.get("speak") or " ".join(d[:2])
            speech.say(said + ". Put it on the scale." if said else "Recognised. Put it on the scale.")
    elif status == 409:  # no recognizer: ai_intake is off
        if go("phone", seq, display=d):
            speech.say("No recognition available. Use the phone.")
    elif go("camera", seq, message=d[0] if d else "No match, retry"):  # no match, failed, cap, unreachable
        beep.beep("nothing")
        speech.say("Not recognised. Try again.")


def save(seq):
    """The long press: the server updates the linked spool's weight, or adds the recognised one and links the tag."""
    with st.lock:
        if seq != flow["seq"]:  # red or a new tag since the long press: nothing to save
            return
        sp = flow["spool"] or {}
        body = {"uid": st.state["tag"], "gross_g": st.state["held"]}
        # the filament exactly as recognize gave it (its `item`); the server takes "filament" or "item"
        body.update({"spool_id": sp["id"]} if sp.get("id") else {"filament": flow["filament"]})
        back = bool(flow.get("returning"))
        if back:  # weigh it, then send it back (R88 1.5)
            body["return"] = True
    status, r = api().save(body)
    d = r.get("display") or []
    if status == 200 and back:
        if go("done", seq, display=d or returned_display(sp, r)):
            beep.beep("hold")
            speech.say(returned_said(r, d))
    elif status == 200:
        if go("done", seq, display=d or ["Saved"]):
            beep.beep("hold")
            speech.say("Saved.")
    elif r.get("error") == "tag_not_linked" and r.get("spool_id"):
        # the spool was made, its tag not linked: keep it, so the next long press links that one (never a second)
        with st.lock:
            if seq == flow["seq"]:
                flow["spool"] = {**(flow["filament"] or {}), "id": r["spool_id"]}
        if go("ready", seq, message=d[0] if d else "Saved, not linked"):
            beep.beep("nothing")
            speech.say("Saved, but the tag is not linked. Hold yellow to try again.")
    elif go("ready", seq, message=why(r, "Save failed")):
        beep.beep("nothing")
        speech.say("Save failed.")


def why(r, default):
    """The Hub's sentence for a refusal: its first display line, else its message."""
    d = r.get("display") or []
    return d[0] if d else r.get("message") or default


def returned_display(sp, r):
    """The done screen when the Hub's answer carries no display: title, name, and the place when it names one."""
    where = r.get("location")
    return ["Returned", sp.get("name") or ""] + (["to " + where] if where else [])


def returned_said(r, d):
    """What the voice says after a return: the Hub's `speak`, else "Returned to <place>." from the answer's location (or
    its "to <place>" row); plain "Returned." when the answer names no place (R88 02:05)."""
    if r.get("speak"):
        return r["speak"]
    where = r.get("location") or next((x[3:] for x in d[1:3] if x.startswith("to ")), "")
    return f"Returned to {where}." if where else "Returned."


def send_back(seq):
    """The long press on a checked-out spool: return it without weighing."""
    with st.lock:
        if seq != flow["seq"]:
            return
        sp = flow["spool"] or {}
        uid = st.state["tag"]
    status, r = api().return_spool(uid, sp.get("id"))
    d = r.get("display") or []
    if status == 200:
        if go("done", seq, display=d or returned_display(sp, r)):
            beep.beep("hold")
            speech.say(returned_said(r, d))
    elif go("weigh", seq, message=why(r, "Return failed")):  # 409 not_checked_out: the Hub's sentence
        beep.beep("nothing")
        speech.say(r.get("message") or "Return failed.")


def on_yellow(long_press):
    """Yellow: take the picture (new tag), take the weight, or (long press, weight taken) save."""
    with st.lock:
        step, seq, nfc_id = flow["step"], flow["seq"], st.state["tag"]
    log.info("yellow %s at %s", "long" if long_press else "press", step)
    if long_press and step == "idle" and not api().paired:
        ask_to_pair()
        return
    with st.lock:
        back = bool(flow.get("returning"))
    if long_press and step == "ready":
        if go("saving", seq):  # only for the attempt the press was on
            spawn(save, seq)
    elif long_press and step == "weigh" and back:  # a checked-out spool: skip weighing, return it
        if go("saving", seq):
            spawn(send_back, seq)
    elif not long_press and step == "camera":
        if go("recognising", seq):
            spawn(recognise, seq, nfc_id)
    elif not long_press and step in ("weigh", "ready") and not has_scale(computer_scale()):
        go(step, seq, message="No scale here")  # also in returning mode: the long press still returns it
        beep.beep("nothing")
        speech.say("No scale here.")
    elif not long_press and step in ("weigh", "ready"):
        computer = computer_scale()
        with st.lock:
            grams = weight_now(computer)
            if grams is not None:
                st.state.update(held=grams, held_at=time.time())
        if grams is None:
            with st.lock:
                sc = scale_reading(computer)
            go(step, seq, message="Scale unit?" if sc and sc["status"].startswith("unit ") else "Scale not settled")
            beep.beep("nothing")
            speech.say("Put the spool on the scale and wait for it to settle.")
        else:
            go("ready", seq)
            beep.beep("hold")
            speech.say(f"{grams:.0f} grams. Hold yellow to save.")
    else:  # a long press outside ready, or a press while waiting on the server
        beep.beep("nothing")
        if step in ("idle", "done", "phone"):
            go(step, seq, message="Scan a tag first")


def reset():
    """Forget the scan, weight, picture and flow (callers hold the lock)."""
    flow["seq"] += 1
    st.state.update(tag=None, tag_at=None, held=None, held_at=None, photo=None, photo_at=None)
    flow.update(spool=None, filament=None, display=[], returning=False)


def on_red():
    """Red: start over, from any step."""
    with st.lock:
        busy = flow["step"] != "idle"
        reset()
        st.state["blink_until"] = time.time() + 0.7 if busy else 0
    log.info("red: reset")
    go("idle")
    beep.beep("discard" if busy else "nothing")
    if busy:
        speech.say("Cancelled.")


def flow_tick(now):
    """Timeouts: back to idle after STATION_FLOW_TIMEOUT of no progress, or DONE_SECONDS after a save."""
    with st.lock:
        step, at = flow["step"], flow["at"]
        if step in ("idle", "pairing") or now - at <= (DONE_SECONDS if step == "done" else st.settings["flow_timeout"]):
            return
        reset()
    log.info("flow timeout at %s", step)
    go("idle")


def tick_loop():
    """The flow's timeouts once a second, panel or not: a tap's flow (and /state's tag) goes back to idle."""
    while True:
        time.sleep(1)
        try:
            flow_tick(time.time())
        except Exception:  # noqa: BLE001 - one bad tick must not end the timeouts for the rest of the run
            log.exception("flow tick")


def ask_to_pair() -> bool:
    """Not paired: ask the server in STATION_SERVER (pair_request.py); the screen shows the code to compare."""
    from . import pair_request

    def show():
        with st.lock:
            on = flow["step"] in ("idle", "pairing")
        if on:
            go("pairing" if pair_request.status["state"] in ("asking", "code") else "idle",
               message=pair_request.status["message"])
    started = pair_request.ask(name=socket.gethostname(), say=speech.say, show=show)
    if not started:
        beep.beep("nothing")
        go("idle", message="No server set" if pair_request.server_address() is None else "Already asking")
    return started


def lamp_default(step) -> str:
    """The lamp the table above lights for `step`: "off", "yellow", "red" or "both"."""
    y, r = STEP_LAMPS[step]
    return "both" if y and r else "yellow" if y else "red" if r else "off"


def lamp_modes(step, choice=None):
    """(yellow, red) lamp modes for `step` when the owner chose `choice` for it on the station's page (R83 section 9: one
    lamp per step of the intake). The table's own pattern stays (a blink is still a blink); only which lamp shows it
    changes: with one lamp chosen that lamp takes the step's pattern (the yellow lamp's, else the red's), "both" gives each
    lamp its own pattern (or the shared one where the table leaves it dark), "off" none. No choice: the table."""
    y, r = STEP_LAMPS[step]
    if choice not in LAMP_CHOICES or choice == lamp_default(step):
        return y, r
    pattern = y or r
    return {"off": (0, 0), "yellow": (pattern, 0), "red": (0, pattern), "both": (y or pattern, r or pattern)}[choice]


def lamps(now):
    """(yellow, red) lamp modes for the step; red blinks three times after a cancel."""
    with st.lock:
        y, r = lamp_modes(flow["step"], st.lamp_map.get(flow["step"]))
        if now < st.state["blink_until"]:
            r = 3
    return y, r


def screen():
    """The display's five rows (title, 4 body lines) for the current step; the panel adds room air at the bottom.
    The server's display lines fill the title and body rows 1-2; rows 3-4 are the station's hint and message."""
    paired = api().paired  # off the lock: it looks at the pairing files
    with st.lock:
        f, held = dict(flow), st.state["held"]
    what = f["spool"] or f["filament"] or {}
    d = [x for x in f["display"] if x] or [
        "Spool #%s" % what["id"] if f["spool"] and what.get("id") else "Recognised",
        " ".join(x for x in (what.get("vendor"), what.get("name")) if x), what.get("material") or ""]
    d += ["", "", "", ""]
    step, msg = f["step"], f["message"]
    back = bool(f.get("returning"))
    from . import pair_request
    code = pair_request.status["code"]
    rows = {
        "pairing": ["Pair code", f"{code[:3]} {code[3:]}" if code else "Asking...",
                    "Compare it on the" if code else "", "setup page: Devices" if code else "", msg],
        "idle": ["Ready", "Scan a spool's tag", "", "", msg] if paired else
                ["Not paired", "Hold yellow to ask", "the kit server, or", "run: station pair", msg],
        "lookup": ["Tag scanned", "Looking it up...", "", "", ""],
        "camera": ["New Tag Scanned", "Show the spool to", "the camera, then", "press yellow", msg],
        "recognising": ["Recognising...", "Please wait", "", "", "Red cancels"],
        "weigh": ["Returning", d[0], d[1], "Yellow: weigh it", msg or "Hold: skip weighing"] if back else
                 [d[0], d[1], d[2], "Scale, then yellow", msg],
        "ready": ["%.0f g" % held if held is not None else "-", d[0], d[1], "Hold yellow: save", msg],
        "saving": ["Saving...", d[0], d[1], "", ""],
        "done": [d[0], d[1], d[2], d[3] if back else "", ""],
        "phone": ["Use phone"] + [x for x in d if x.lower() != "use phone"][:2] + ["Red: start over", ""],
    }[step]
    return [ascii_text(rows[0], TITLE_WIDTH)] + [ascii_text(r) for r in rows[1:]]

