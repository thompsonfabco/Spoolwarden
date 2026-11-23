"""(carry 3): the hello route keeps what a Weigh Station tells the Hub about itself."""
import pytest
from starlette.testclient import TestClient

from filament_kit import pairing
from filament_kit.api import create_app
from filament_kit.spoolman import Spoolman

FP = "ab" * 32


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    for name in ("etc", "state", "opt"):
        (tmp_path / name).mkdir(exist_ok=True)
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))
    pairing.make_cert("fkhost", [], tmp_path / "etc")


@pytest.fixture
def hub(spoolman_url):
    sm = Spoolman(spoolman_url)
    sm.ensure_fields()
    return TestClient(create_app(sm=sm))


def station():
    tok = pairing.redeem_code(pairing.issue("station", scope="station").code)
    return {"Authorization": "Bearer " + tok}


def kept():
    return next(d for d in pairing._read("devices.json") if d.get("scope") == "station")


def test_the_three_values_are_kept_with_the_stations_pairing(hub):
    h = station()
    r = hub.get(f"/api/station/hello?fp={FP.upper()}&port=443&version=0.6.2", headers=h)
    assert r.status_code == 200 and r.json()["ok"] is True and set(r.json()["voice"]) == {"speech", "speaker", "speed", "volume"}
    d = kept()
    assert (d["fp"], d["port"], d["version"]) == (FP, 443, "0.6.2") and d["hello_at"]
    assert pairing.station_info()["fp"] == FP


def test_an_older_station_sends_nothing_and_is_still_answered(hub):
    r = hub.get("/api/station/hello", headers=station())
    assert r.status_code == 200 and r.json()["ok"] is True and set(r.json()["voice"]) == {"speech", "speaker", "speed", "volume"}
    assert "fp" not in kept()


@pytest.mark.parametrize("query", ["fp=zz&port=443&version=1", f"fp={'ab' * 31}&port=443&version=1",
                                   f"fp={FP}&port=0&version=1", f"fp={FP}&port=70000&version=1",
                                   f"fp={FP}&port=http&version=1", f"fp={FP}&port=443&version=" + "9" * 41,
                                   f"fp={FP}&version=1"])
def test_a_value_that_is_not_what_it_should_be_is_refused_and_nothing_is_kept(hub, query):
    r = hub.get("/api/station/hello?" + query, headers=station())
    assert r.status_code == 400 and r.json()["error"] == "bad_hello"
    assert "fp" not in kept()


def test_a_station_without_a_certificate_says_why(hub):
    r = hub.get("/api/station/hello?port=5003&version=0.6.2&plain=no+openssl", headers=station())
    assert r.status_code == 200
    assert pairing.station_plain_reason() == "no openssl" and "fp" not in kept()


def test_the_call_needs_the_stations_token(hub):
    assert hub.get(f"/api/station/hello?fp={FP}&port=443&version=1").status_code == 401


def test_a_phones_token_cannot_tell_the_hub_about_a_station(hub):
    phone = {"Authorization": "Bearer " + pairing.redeem_code(pairing.issue("Pixel").code)}
    r = hub.get(f"/api/station/hello?fp={FP}&port=443&version=1", headers=phone)
    assert r.status_code in (400, 403) and pairing.station_info() is None


# ---- the reason a plain-HTTP station gives is one clean line of bounded length

EVIL = "no\nopenssl\r\n\t\x1b[31mred\x1b[0m\x00\x07 ‮end " + "x" * 5000


def test_a_reason_is_cleaned_before_it_is_stored(hub):
    from urllib.parse import quote
    r = hub.get(f"/api/station/hello?port=5003&version=0.6.2&plain={quote(EVIL)}", headers=station())
    assert r.status_code == 200
    why = pairing.station_plain_reason()
    assert why.startswith("no openssl red end ") and "\n" not in why and "\x1b" not in why and "\x00" not in why
    assert "‮" not in why and len(why) <= 120


def test_a_normal_reason_is_unchanged(hub):
    hub.get("/api/station/hello?port=5003&version=0.6.2&plain=PermissionError:+key.pem", headers=station())
    assert pairing.station_plain_reason() == "PermissionError: key.pem"


def test_clean_line_bounds_and_cleans():
    from filament_kit.address_texts import clean_line
    out = clean_line(EVIL)
    assert len(out) == 200 and out.endswith("…") and out.isprintable()
    assert clean_line("a\nb\x1b]0;title\x07c") == "a b c" or clean_line("a\nb\x1b]0;title\x07c") == "a bc"
    assert clean_line(None) == "" and clean_line("  fine  ") == "fine"
