"""R88 CO2: the checked-out routes of the kit API (`filter=checked_out` and `filters` on GET /api/spools, `POST /api/spools/{id}/return`,
`home` on PATCH /api/spools/{id}), the Weigh Station's three (tag, save with `return`, `POST /api/station/return`) and the web
app's twins. Real inventory over the fake Spoolman; the Klipper printer is a recorder.

The command line's requests (R682, co7-spools-cli 607c9268) are replayed at the end: every field it reads must be in the answer."""
from __future__ import annotations

import json

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from filament_kit import api, config, inventory, pairing, station_flow, storage, web_access
from filament_kit.api import create_app
from filament_kit.bridge import family
from filament_kit.spoolman import Spoolman
from filament_kit.web import mcp_tools
from filament_kit.web.intake_app import IntakeApp
from filament_kit.web.slots import INTAKE_DEVICE, SlotError
from test_inventory import SINCE, SLOT, _opened, _out, printers, store  # noqa: F401

SENTENCE = "This spool is not checked out."


def NEW() -> str:
    """A storage location the Hub lists, that is not "Rack 1": what a home is changed to."""
    return storage.current().choosable()[-1]


@pytest.fixture(autouse=True)
def fk_env(tmp_path, monkeypatch):
    for name in ("etc", "state", "opt"):
        (tmp_path / name).mkdir(exist_ok=True)
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))
    pairing.make_cert("fkhost", [], tmp_path / "etc")
    config.save({"placement": {"box_count": 2}})


@pytest.fixture(autouse=True)
def no_sink_left_behind():
    """`Tools(kit_writes=True)` and the app install the process-wide event sink: leave it as found."""
    from filament_kit import event_sink
    yield
    event_sink.set_sink(None)


@pytest.fixture
def sm(spoolman_url):
    s = Spoolman(spoolman_url)
    s.ensure_fields()
    return s


def _auth(name="Phone", role=None, scope=None):
    return {"Authorization": "Bearer " + pairing.redeem_code(pairing.issue(name, role=role, scope=scope).code)}


@pytest.fixture
def c(sm, printers, store):  # noqa: F811
    with TestClient(create_app(sm=sm), raise_server_exceptions=False) as client:
        client.headers.update(_auth())
        yield client


def _at_home(sm, **kw):
    return _opened(sm, 500, **kw)


# ---------- the list: filter, filters, total, the view ----------

def test_the_list_answers_filters_total_and_the_view_of_every_row(c, sm):
    out = _out(sm, name="Out one")
    home = _at_home(sm, name="At home")
    allrows = c.get("/api/spools").json()
    assert allrows["filters"] == ["sealed", "opened", "loaded", "low", "checked_out"] and allrows["total"] == 2
    views = {r["id"]: r["view"] for r in allrows["spools"]}                    # every list carries the view's three fields
    assert (views[out["id"]]["checked_out"], views[out["id"]]["home"], views[out["id"]]["checked_out_since"]) == (True, "Rack 1", SINCE)
    assert (views[home["id"]]["checked_out"], views[home["id"]]["home"], views[home["id"]]["checked_out_since"]) == (False, None, None)
    got = c.get("/api/spools?filter=checked_out").json()
    assert [r["id"] for r in got["spools"]] == [out["id"]] and got["total"] == 1       # the chip's count is `total`
    assert got["filters"] == allrows["filters"] and got["has_more"] is False and got["offset"] == 0


def test_the_filters_are_named_when_no_spool_is_returned(c):
    got = c.get("/api/spools?filter=checked_out").json()
    assert got["spools"] == [] and got["total"] == 0 and "checked_out" in got["filters"]


def test_the_other_filters_work_and_an_unknown_one_is_refused_with_its_field(c, sm):
    _out(sm, name="Out one")
    plain = _at_home(sm, name="At home")
    assert [r["id"] for r in c.get("/api/spools?filter=opened&q=home").json()["spools"]] == [plain["id"]]
    assert c.get("/api/spools?filter=loaded").json()["total"] == 1
    bad = c.get("/api/spools?filter=everything")
    assert bad.status_code == 400 and bad.json()["error"] == "bad_request" and bad.json()["field"] == "filter"


def test_a_spool_of_the_view_has_the_trio_in_get_spool(c, sm):
    out = _out(sm)
    v = c.get(f"/api/spools/{out['id']}").json()["view"]
    assert (v["checked_out"], v["home"], v["checked_out_since"]) == (True, "Rack 1", SINCE)


# ---------- POST /api/spools/{id}/return ----------

def test_return_sends_a_spool_home_and_answers_the_place_and_the_undo(c, sm, store):  # noqa: F811
    out = _out(sm, home="Rack 2")
    r = c.post(f"/api/spools/{out['id']}/return", json={})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["location"] == "Rack 2" and b["spool"]["location"] == "Rack 2" and b["view"]["checked_out"] is False
    assert isinstance(b["event_id"], int) and b["before"]["checked_out"] is True
    back = sm.get(f"spool/{out['id']}")
    assert back["location"] == "Rack 2" and inventory.prev_entry(back) == ("", None)
    ev = [e for e in store.all_events() if e["kind"] == "spool.returned"]
    assert len(ev) == 1 and ev[0]["data"]["before_location"] == SLOT and ev[0]["id"] == b["event_id"]
    # the answer needs no body at all
    again = _out(sm, home="Rack 1", name="Second")
    assert c.post(f"/api/spools/{again['id']}/return").status_code == 200


def test_return_of_a_spool_with_no_home_places_it_by_the_rules(c, sm):
    s = _out(sm, home="", name="No home")
    b = c.post(f"/api/spools/{s['id']}/return", json={}).json()
    assert b["location"] and b["location"] != "Sealed stock" and b["spool"]["location"] == b["location"]


def test_return_of_a_spool_that_is_not_checked_out_is_refused_409(c, sm):
    plain = _at_home(sm, name="At home")
    r = c.post(f"/api/spools/{plain['id']}/return", json={})
    assert r.status_code == 409 and r.json() == {"error": "not_checked_out", "message": SENTENCE}
    assert sm.get(f"spool/{plain['id']}")["location"] == "Rack 1"
    done = _out(sm, name="Once")
    assert c.post(f"/api/spools/{done['id']}/return", json={}).status_code == 200
    twice = c.post(f"/api/spools/{done['id']}/return", json={})                 # the second one finds it home
    assert twice.status_code == 409 and twice.json()["error"] == "not_checked_out"
    assert c.post("/api/spools/9999/return", json={}).status_code == 404


def test_return_caller_rules(sm, printers, store):  # noqa: F811
    app = create_app(sm=sm)
    phone, viewer = _auth("Phone"), _auth("Viewer", role=pairing.VIEWER_ROLE)
    home_web = _auth(INTAKE_DEVICE, role=web_access.WEB_ROLE)
    pub_web = _auth("web (MCP)", role=web_access.WEB_ROLE)
    station = _auth("station", scope="station")
    with TestClient(app, raise_server_exceptions=False) as client:
        s = _out(sm, name="Rules")
        url = f"/api/spools/{s['id']}/return"
        assert client.post(url, json={}).status_code == 401                      # no token
        assert client.post(url, json={}, headers=viewer).status_code == 403      # a viewer reads only
        assert client.post(url, json={}, headers=station).status_code == 403     # the station has its own route
        assert client.post(url, json={}, headers=pub_web).status_code == 200     # a web token (the spool-change rule)
        back = _out(sm, name="Rules 2")
        assert client.post(f"/api/spools/{back['id']}/return", json={}, headers=home_web).status_code == 200
        third = _out(sm, name="Rules 3")
        assert client.post(f"/api/spools/{third['id']}/return", json={}, headers=phone).status_code == 200
        # a viewer and the station may still not change a home, and a viewer reads the list and the spool
        assert client.patch(f"/api/spools/{_out(sm, name='Rules 4')['id']}", json={"home": NEW()}, headers=viewer).status_code == 403
        assert client.get("/api/spools?filter=checked_out", headers=viewer).status_code == 200
    assert ("POST", "/api/spools/{id}/return") in api.WEB_ROUTES and ("POST", "/api/spools/{id}/return") not in api.VIEWER_ROUTES


def test_return_and_home_have_the_callers_of_the_spools_move_and_weight(sm, printers, store):  # noqa: F811
    """Pins the orchestrator's ruling (CO2 follow-up 1): `return` and the `home` change are for the callers of the existing spool
    move / weight route (PATCH /api/spools/{id}). Compared by running both for every kind of token. The one difference is the
    web token, which the kit route refuses (the web app writes a move and a weight in its own process, through its twin) while
    the Return twin must reach the Hub, which tells a Klipper printer: that is why return is in WEB_ROUTES (HOME_WEB_ROUTES
    is for the voice routes). Any other divergence fails here."""
    app = create_app(sm=sm)
    tokens = {"viewer": _auth("Viewer", role=pairing.VIEWER_ROLE), "station": _auth("station", scope="station"),
              "phone": _auth("Phone"), "home_web": _auth(INTAKE_DEVICE, role=web_access.WEB_ROLE),
              "public_web": _auth("web (MCP)", role=web_access.WEB_ROLE)}
    allowed = {"return": set(), "home": set(), "move": set(), "weight": set()}
    with TestClient(app, raise_server_exceptions=False) as client:
        for who, h in tokens.items():
            ids = [_out(sm, name=f"{who} {i}")["id"] for i in range(4)]
            calls = {"return": client.post(f"/api/spools/{ids[0]}/return", json={}, headers=h),
                     "home": client.patch(f"/api/spools/{ids[1]}", json={"home": NEW()}, headers=h),
                     "move": client.patch(f"/api/spools/{ids[2]}", json={"location": "Rack 2"}, headers=h),
                     "weight": client.patch(f"/api/spools/{ids[3]}", json={"remaining": 400}, headers=h)}
            for name, r in calls.items():
                if r.status_code not in (401, 403):
                    allowed[name].add(who)
    assert allowed["home"] == allowed["move"] == allowed["weight"] == {"phone"}
    assert allowed["return"] == allowed["home"] | {"home_web", "public_web"}       # only the web token differs, and said so above
    assert "viewer" not in allowed["return"] | allowed["home"] and "station" not in allowed["return"] | allowed["home"]
    assert ("PATCH", "/api/spools/{id}") not in api.WEB_ROUTES and ("POST", "/api/spools/{id}/return") not in api.HOME_WEB_ROUTES


# ---------- a Klipper printer is told ----------

class FakeBambu:
    def state(self, pid):
        return "connected"

    def sync(self):
        pass


class FakeKlipper(FakeBambu):
    def __init__(self, sm):
        self.sm, self.calls = sm, []

    def return_spool(self, sm, pid, spool_id, *, grams=None, rules=None, dry=False):
        self.calls.append((pid, spool_id, grams, dry))
        return inventory.return_spool(sm, spool_id, grams=grams, rules=rules, dry=dry)


def test_a_spool_in_a_klipper_slot_goes_back_through_the_klipper_family(sm, printers, store):  # noqa: F811
    klipper = FakeKlipper(sm)
    app = create_app(sm, printers=family.Printers(bambu=FakeBambu(), klipper=klipper), camera_hub=object(), mapper=object(),
                     enabled_modules=frozenset({"server", "printers", "klipper"}))
    with TestClient(app, raise_server_exceptions=False) as client:
        client.headers.update(_auth())
        sk = _opened(sm, 600, name="On the Sovol")
        sm.patch(f"spool/{sk['id']}", {"location": "Big Sovol – T0", "extra": {**(sk.get("extra") or {}), **inventory.prev_extra("Rack 2", SINCE)}})
        r = client.post(f"/api/spools/{sk['id']}/return", json={})
        assert r.status_code == 200 and r.json()["location"] == "Rack 2"
        assert klipper.calls == [("K1", sk["id"], None, False)]
        bambu = _out(sm, name="On the H2D")                                       # a Bambu Lab slot: the Hub's record only
        assert client.post(f"/api/spools/{bambu['id']}/return", json={}).status_code == 200
        assert len(klipper.calls) == 1


# ---------- PATCH home ----------

def test_the_home_edit_previews_then_saves_and_has_no_event(c, sm):
    s = _out(sm, home="Rack 1")
    dry = c.patch(f"/api/spools/{s['id']}", json={"home": NEW(), "dry_run": True})
    assert dry.status_code == 200 and dry.json()["dry"] is True
    assert (dry.json()["before"]["home"], dry.json()["view"]["home"]) == ("Rack 1", NEW())
    assert inventory.prev_entry(sm.get(f"spool/{s['id']}"))[0] == "Rack 1"        # a preview writes nothing
    real = c.patch(f"/api/spools/{s['id']}", json={"home": NEW(), "dry_run": False})
    assert real.status_code == 200 and real.json()["dry"] is False
    assert (real.json()["before"]["home"], real.json()["view"]["home"]) == ("Rack 1", NEW())
    assert real.json()["view"]["checked_out"] is True and real.json()["view"]["checked_out_since"] == SINCE
    assert sm.get(f"spool/{s['id']}")["location"] == SLOT                          # still in the slot
    assert "event_id" not in real.json()                                           # a home change has no undo


def test_the_home_edit_refuses_a_spool_not_checked_out_and_an_unknown_place(c, sm):
    plain = _at_home(sm, name="At home")
    r = c.patch(f"/api/spools/{plain['id']}", json={"home": NEW(), "dry_run": False})
    assert r.status_code == 409 and r.json() == {"error": "not_checked_out", "message": SENTENCE}
    out = _out(sm)
    bad = c.patch(f"/api/spools/{out['id']}", json={"home": "Nowhere at all", "dry_run": False})
    assert bad.status_code == 400 and bad.json()["error"] == "bad_request" and bad.json()["field"] == "home"
    assert "Nowhere at all" in bad.json()["message"]
    assert c.patch(f"/api/spools/{out['id']}", json={"home": 5}).status_code == 400
    assert inventory.prev_entry(sm.get(f"spool/{out['id']}"))[0] == "Rack 1"
    # a PATCH without `home` is what it was
    w = c.patch(f"/api/spools/{plain['id']}", json={"remaining": 400, "dry_run": False})
    assert w.status_code == 200 and w.json()["spool"]["remaining_weight"] == 400


# ---------- the Weigh Station ----------

TAG = "a:b:c:d:e:f:1"


@pytest.fixture
def stc(sm, printers, store):  # noqa: F811
    with TestClient(create_app(sm=sm), raise_server_exceptions=False) as client:
        client.headers.update(_auth("station", scope="station"))
        yield client


def _own_tag(sm, s):
    """Give the spool the station's tag (the tag _opened linked is its own, by id)."""
    return inventory.tags.stored(sm.get(f"spool/{s['id']}"))


def test_the_tag_of_a_checked_out_spool_says_so_and_names_its_home(stc, sm):
    s = _out(sm, home="Rack 2", name="Out one")
    uid = _own_tag(sm, s)
    r = stc.get(f"/api/station/tag/{uid}").json()
    assert r["known"] is True and r["spool"]["checked_out"] is True and r["spool"]["home"] == "Rack 2"
    assert r["spool"]["slot"] == "A2"
    assert r["display"] == [r["spool"]["name"][:16], "Home: Rack 2"]
    nohome = _out(sm, home="", name="No home")
    d = stc.get(f"/api/station/tag/{_own_tag(sm, nohome)}").json()
    assert d["spool"]["checked_out"] is True and d["spool"]["home"] is None and d["display"][1] == "Home: not recorded"
    plain = _at_home(sm, name="At home")
    p = stc.get(f"/api/station/tag/{_own_tag(sm, plain)}").json()
    assert p["spool"]["checked_out"] is False and p["spool"]["home"] is None and p["spool"]["slot"] is None
    assert len(p["display"]) == 3 and "left" in p["display"][2]                      # the plain flow's lines are as they were


def test_save_with_return_weighs_then_returns_and_answers_the_four_rows(stc, sm):
    s = _out(sm, home="Rack 2", name="Out one")
    uid = _own_tag(sm, s)
    r = stc.post("/api/station/save", json={"uid": uid, "gross_g": 600, "return": True})
    assert r.status_code == 200, r.text
    b = r.json()
    tare = (s.get("filament") or {}).get("spool_weight") or s.get("spool_weight") or 0
    after = sm.get(f"spool/{s['id']}")
    assert after["location"] == "Rack 2" and inventory.prev_entry(after) == ("", None)
    assert after["remaining_weight"] == round(600 - tare, 1)                         # weighed, then returned
    assert b["returned"] is True and b["location"] == "Rack 2" and b["spool_id"] == s["id"] and isinstance(b["event_id"], int)
    left = f"{after['remaining_weight']:.0f} g left"
    assert b["display"] == ["Returned", after["filament"]["name"][:21], "to Rack 2", left]
    assert b["speak"] == "Returned to Rack 2."


def test_save_with_return_of_a_spool_that_is_not_checked_out_writes_nothing(stc, sm):
    plain = _at_home(sm, name="At home")
    uid = _own_tag(sm, plain)
    r = stc.post("/api/station/save", json={"uid": uid, "gross_g": 450, "return": True})
    assert r.status_code == 409 and r.json()["error"] == "not_checked_out" and r.json()["message"] == SENTENCE
    assert r.json()["display"] == ["Not checked out"]
    assert sm.get(f"spool/{plain['id']}")["remaining_weight"] == 500                  # not even the weight
    assert stc.post("/api/station/save", json={"uid": uid, "gross_g": 450, "return": "yes"}).status_code == 400
    ok = stc.post("/api/station/save", json={"uid": uid, "gross_g": 450})              # the plain save is as it was
    assert ok.status_code == 200 and "returned" not in ok.json()


def test_save_with_return_that_loses_a_race_writes_no_weight(stc, sm, monkeypatch):
    """The spool is sent home by someone else between the checked-out check and the return: 409, and the weight was not written."""
    s = _out(sm, home="Rack 2", name="Raced")
    uid = _own_tag(sm, s)
    before = sm.get(f"spool/{s['id']}")["remaining_weight"]
    real = inventory.return_spool

    def raced(sm_, spool_id, **kw):
        sm_.patch(f"spool/{spool_id}", {"location": "Rack 2", "extra": {**(sm_.get(f"spool/{spool_id}").get("extra") or {}), **inventory.prev_extra("")}})
        return real(sm_, spool_id, **kw)
    monkeypatch.setattr(inventory, "return_spool", raced)
    r = stc.post("/api/station/save", json={"uid": uid, "gross_g": 600, "return": True})
    assert r.status_code == 409 and r.json()["error"] == "not_checked_out"
    assert sm.get(f"spool/{s['id']}")["remaining_weight"] == before


def test_save_with_return_and_no_place_for_the_spool_says_returned_alone(stc, sm, monkeypatch):
    s = _out(sm, home="", name="No place")
    uid = _own_tag(sm, s)
    monkeypatch.setattr(inventory, "auto_place", lambda *a, **k: (_ for _ in ()).throw(inventory.SpoolEditError("full", "location")))
    b = stc.post("/api/station/save", json={"uid": uid, "gross_g": 600, "return": True}).json()
    assert b["location"] is None and b["speak"] == "Returned." and not any(x.startswith("to ") for x in b["display"])
    assert b["display"][0] == "Returned"


def test_the_station_return_route(stc, sm):
    s = _out(sm, home="Rack 2", name="Out one")
    uid = _own_tag(sm, s)
    r = stc.post("/api/station/return", json={"uid": uid, "spool_id": s["id"]})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["spool_id"] == s["id"] and b["location"] == "Rack 2" and b["display"][0] == "Returned" and b["display"][2] == "to Rack 2"
    assert len(b["display"]) == 3 and b["speak"] == "Returned to Rack 2."             # no weight row: nothing was weighed
    assert sm.get(f"spool/{s['id']}")["location"] == "Rack 2"
    gone = stc.post("/api/station/return", json={"uid": uid, "spool_id": s["id"]})   # now at home
    assert gone.status_code == 409 and gone.json()["error"] == "not_checked_out" and gone.json()["display"] == ["Not checked out"]
    assert gone.json()["message"] == SENTENCE


def test_the_station_return_route_refusals_and_callers(stc, sm):
    s = _out(sm, name="Out one")
    uid = _own_tag(sm, s)
    other = _out(sm, name="Other one")
    assert stc.post("/api/station/return", json={"uid": uid, "spool_id": other["id"]}).json()["error"] == "tag_mismatch"
    assert stc.post("/api/station/return", json={"uid": "0a:0b:0c:0d:0e:0f:99", "spool_id": s["id"]}).status_code == 409
    for body in ({}, {"uid": uid}, {"uid": uid, "spool_id": "7"}, {"uid": uid, "spool_id": True}, {"spool_id": 1}):
        assert stc.post("/api/station/return", json=body).status_code == 400, body
    assert sm.get(f"spool/{s['id']}")["location"] == SLOT
    with TestClient(create_app(sm=sm), raise_server_exceptions=False) as anon:
        assert anon.post("/api/station/return", json={}).status_code == 401
        assert anon.post("/api/station/return", json={"uid": uid, "spool_id": s["id"]}, headers=_auth("Viewer", role=pairing.VIEWER_ROLE)).status_code == 403
        assert anon.post("/api/station/return", json={"uid": uid, "spool_id": s["id"]}, headers=_auth(INTAKE_DEVICE, role=web_access.WEB_ROLE)).status_code == 403
        assert anon.post("/api/station/return", json={"uid": uid, "spool_id": s["id"]}, headers=_auth()).status_code == 200   # a paired phone or PC


def test_the_stations_screen_lines_stay_in_the_panels_width():
    out = station_flow.returned_answer("A very long spool name that does not fit", {"view": {"location": "Dry box 1 with a long place name"}}, 1234.4)
    assert out["display"][0] == "Returned" and all(len(x) <= 21 for x in out["display"][1:]) and out["display"][3] == "1234 g left"
    assert station_flow.returned_answer("Café", {"view": {"location": "Étagère"}})["display"][1:] == ["Cafe", "to Etagere"]


# ---------- the web app's twins ----------

class RecordingKit:
    """The Hub as the twins see it: records what they pass on, answers what it is given."""

    def __init__(self):
        self.calls, self.answer, self.refuse = [], {}, None

    def get(self, path):
        self.calls.append(("GET", path, None))
        return {}

    def post(self, path, body):
        self.calls.append(("POST", path, body))
        if self.refuse:
            raise self.refuse
        return self.answer

    def patch(self, path, body):
        self.calls.append(("PATCH", path, body))
        return {}


def _intake(sm, kit):
    tools = mcp_tools.Tools(sm, kit_api=kit, kit_writes=True)
    return Starlette(routes=IntakeApp(tools, public_url=None, lan_cidrs=["192.168.1.0/24"], push=False,
                                      host_ok=lambda r: True, kit_api=kit).routes())


def _web(sm, kit):
    return TestClient(_intake(sm, kit), base_url="https://hub.local:8710", client=("192.168.1.50", 1234))


ORIGIN = {"origin": "https://hub.local:8710"}


def test_the_list_twin_answers_what_the_hub_answers(sm, printers):  # noqa: F811
    out = _out(sm, name="Out one")
    _at_home(sm, name="At home")
    with _web(sm, RecordingKit()) as w:
        got = w.get("/intake/api/spools?filter=checked_out").json()
        assert [r["id"] for r in got["spools"]] == [out["id"]] and got["total"] == 1
        assert got["filters"] == ["sealed", "opened", "loaded", "low", "checked_out"]
        assert got["spools"][0]["view"]["home"] == "Rack 1" and got["spools"][0]["view"]["checked_out"] is True
        every = w.get("/intake/api/spools").json()
        assert every["total"] == 2 and "view" in every["spools"][0]
        assert w.get("/intake/api/spools?filter=nonsense").status_code == 400
    with TestClient(_intake(sm, RecordingKit()), base_url="https://hub.local:8710", client=("8.8.8.8", 1)) as away:
        assert away.get("/intake/api/spools").status_code == 403                  # the home network only


def test_the_return_twin_passes_the_hub_on_and_gives_its_refusal_as_the_kit_does(sm, printers):  # noqa: F811
    kit = RecordingKit()
    kit.answer = {"spool": {"id": 4, "location": "Rack 2"}, "view": {"id": 4, "location": "Rack 2", "checked_out": False},
                  "location": "Rack 2", "event_id": 41}
    with _web(sm, kit) as w:
        assert w.post("/intake/api/spools/4/return", json={}).status_code == 403            # the page's origin is needed
        r = w.post("/intake/api/spools/4/return", json={}, headers=ORIGIN)
        assert r.status_code == 200 and r.json()["event_id"] == 41 and r.json()["location"] == "Rack 2"
        assert r.json()["spool"]["view"]["location"] == "Rack 2"                          # the sheet reads spool.view
        assert w.post("/intake/api/spools/4/return", headers=ORIGIN).status_code == 200   # and no body is fine
        kit.refuse = SlotError(SENTENCE, code="not_checked_out", http=409)
        no = w.post("/intake/api/spools/4/return", json={}, headers=ORIGIN)
        assert no.status_code == 409 and no.json() == {"error": "not_checked_out", "message": SENTENCE}
        kit.refuse = SlotError("The Hub did not answer.", code="unreachable", http=None)
        assert w.post("/intake/api/spools/4/return", json={}, headers=ORIGIN).status_code == 502
    assert [x for x in kit.calls if x[0] == "POST"][0] == ("POST", "/api/spools/4/return", {})


def test_the_editing_twin_changes_a_home_and_refuses_as_the_hub_does(sm, printers):  # noqa: F811
    s = _out(sm, home="Rack 1")
    plain = _at_home(sm, name="At home")
    with _web(sm, RecordingKit()) as w:
        prev = w.post(f"/intake/api/spools/{s['id']}", json={"home": NEW()}, headers=ORIGIN).json()    # dry_run is the default
        assert prev["dry"] is True and (prev["before"]["home"], prev["view"]["home"]) == ("Rack 1", NEW())
        assert inventory.prev_entry(sm.get(f"spool/{s['id']}"))[0] == "Rack 1"
        saved = w.post(f"/intake/api/spools/{s['id']}", json={"home": NEW(), "dry_run": False}, headers=ORIGIN).json()
        assert saved["view"]["home"] == NEW() and saved["view"]["checked_out"] is True
        no = w.post(f"/intake/api/spools/{plain['id']}", json={"home": NEW(), "dry_run": False}, headers=ORIGIN)
        assert no.status_code == 409 and no.json() == {"error": SENTENCE, "code": "not_checked_out"}
        bad = w.post(f"/intake/api/spools/{s['id']}", json={"home": "Nowhere at all", "dry_run": False}, headers=ORIGIN)
        assert bad.status_code == 400 and bad.json()["field"] == "home"
        assert w.post(f"/intake/api/spools/{s['id']}", json={"home": 7}, headers=ORIGIN).status_code == 400
        assert w.post(f"/intake/api/spools/{plain['id']}", json={"remaining": 450, "dry_run": False}, headers=ORIGIN).status_code == 200


# ---------- the command line's requests (R682), replayed ----------

def test_the_command_lines_requests_find_every_field_they_read(c, sm):
    s = _out(sm, home="Rack 1", name="Out one")
    n = s["id"]
    # spools --checked-out: GET /api/spools?filter=checked_out, reads `total` (and `filters`)
    lst = c.get("/api/spools?filter=checked_out").json()
    assert isinstance(lst["total"], int) and isinstance(lst["filters"], list) and lst["spools"][0]["view"]["home"] == "Rack 1"
    # spool return: GET /api/spools/<n> (view.checked_out, view.home), then POST /api/spools/<n>/return with {} (spool.location)
    got = c.get(f"/api/spools/{n}").json()
    assert got["view"]["checked_out"] is True and got["view"]["home"] == "Rack 1"
    ret = c.post(f"/api/spools/{n}/return", json={}).json()
    assert ret["spool"]["location"] == "Rack 1"
    # a second return: 409 not_checked_out and the sentence the CLI prints
    again = c.post(f"/api/spools/{n}/return", json={})
    assert again.status_code == 409 and again.json()["error"] == "not_checked_out" and again.json()["message"] == SENTENCE
    # spool home: PATCH {home, dry_run: true} then {home, dry_run: false}: before.home and view.home
    t = _out(sm, home="Rack 1", name="Second")
    prev = c.patch(f"/api/spools/{t['id']}", json={"home": NEW(), "dry_run": True}).json()
    done = c.patch(f"/api/spools/{t['id']}", json={"home": NEW(), "dry_run": False}).json()
    for ans in (prev, done):
        assert ans["before"]["home"] == "Rack 1" and ans["view"]["home"] == NEW()
    # 400 for an unknown location carries the Hub's message
    bad = c.patch(f"/api/spools/{t['id']}", json={"home": "Nowhere", "dry_run": True})
    assert bad.status_code == 400 and isinstance(bad.json()["message"], str) and bad.json()["message"]
