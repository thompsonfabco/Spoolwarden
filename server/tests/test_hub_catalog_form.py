"""Tests for custom catalog entry form endpoints and library token removal."""
from __future__ import annotations

from filament_kit import library, setup_helper
from filament_kit.profiles import builds
from test_setup_page import CODE, H, Helper, client, env, helper, make, post  # noqa: F401


def test_library_view_includes_materials(env, helper):
    app = make(env, helper, add={"library"})
    r = client(app).get("/setup/api/library")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data["materials"], list) and len(data["materials"]) > 0


def test_library_local_get_post_and_remove(env, helper, monkeypatch):
    rebuilds: list[str] = []
    monkeypatch.setattr(builds, "request_rebuild", lambda why: rebuilds.append(why))
    app = make(env, helper, add={"library"})
    c = client(app)

    empty = c.get("/setup/api/library/local")
    assert empty.status_code == 200 and empty.json() == {"entries": []}

    # Origin guard on POST library/local
    assert c.post("/setup/api/library/local", json={"brand": "X", "name": "Y", "material": "PLA"}).status_code == 403

    # Unknown material without nozzle/bed returns 400 with code=invalid_entry, error sentence, and problems list
    bad = post(c, "library/local", {"brand": "TestBrand", "name": "TestMat", "material": "Unobtanium"})
    assert bad.status_code == 400
    bad_json = bad.json()
    assert bad_json["code"] == "invalid_entry"
    assert isinstance(bad_json["error"], str) and bad_json["error"]
    fields = {p["field"] for p in bad_json["problems"]}
    assert "nozzle_min" in fields and "bed_min" in fields
    assert rebuilds == []

    # Valid entry creates row cleanly (201) and schedules preset rebuild
    ok = post(
        c,
        "library/local",
        {
            "brand": "TestBrand",
            "name": "TestMat",
            "material": "Unobtanium",
            "nozzle_min": "210",
            "nozzle_max": "225",
            "bed_min": "60",
            "density": "1.24",
            "notes": "Custom spool",
        },
    )
    assert ok.status_code == 201
    created = ok.json()
    assert created["ok"] is True
    row = created["entry"]
    assert row["brand"] == "TestBrand" and row["name"] == "TestMat" and row["material"] == "Unobtanium"
    assert row["id"] and row["added"]
    assert len(rebuilds) == 1

    listed = c.get("/setup/api/library/local").json()["entries"]
    assert len(listed) == 1 and listed[0]["id"] == row["id"]

    # Origin guard on remove
    assert c.post(f"/setup/api/library/local/{row['id']}/remove", json={}).status_code == 403

    # Missing id returns 404
    missing = post(c, "library/local/does-not--exist/remove", {})
    assert missing.status_code == 404 and "error" in missing.json()

    # Remove existing entry returns removed=True and schedules rebuild
    removed = post(c, f"library/local/{row['id']}/remove", {})
    assert removed.status_code == 200 and removed.json() == {"removed": True}
    assert len(rebuilds) == 2
    assert c.get("/setup/api/library/local").json() == {"entries": []}


def test_library_token_ops_removed_from_helper(env):
    assert "library_token_set" not in setup_helper.OPS
    assert "library_token_remove" not in setup_helper.OPS
    for op in ("library_token_set", "library_token_remove"):
        r = setup_helper.handle({"op": op, "code": CODE})
        assert r["ok"] is False and r["status"] == 400
