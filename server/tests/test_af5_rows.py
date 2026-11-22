"""AF5: the Checks rows of the satellites (R82 section 2 points 4 and 5, section 6 point 5; 06 9.1)."""
from __future__ import annotations

import pytest

from filament_kit import config, local_reader, pairing, station_client, status, tagsources

STATION = "http://station.local:5003"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    for k, d in (("ETC", "etc"), ("STATE", "state"), ("OPT", "opt")):
        monkeypatch.setenv(f"FK_{k}", str(tmp_path / d))
        (tmp_path / d).mkdir()


def rows(cfg=None):
    cfg = cfg or {"station": {"url": STATION}, "tag_sources": []}
    return list(status.satellite_rows(cfg))


@pytest.mark.parametrize("reason", [None, "\n\x1b[31m\x1b[0m\x00"])
def test_a_station_without_a_certificate_reason_falls_back_to_its_address(monkeypatch, reason):
    monkeypatch.setattr(station_client, "station_https", lambda url: "plain")
    monkeypatch.setattr(pairing, "station_plain_reason", lambda: reason)
    (r,) = rows()
    assert (r.state, r.group, r.key) == ("fail", "Devices", "devices.station-https")
    assert r.text == "The Weigh Station at station.local:5003 is not using HTTPS."
    assert r.fix == "Update it: on that machine run sudo spoolwarden update."


def test_a_station_that_could_not_make_its_certificate_says_why_as_one_clean_line(monkeypatch):
    classifications = iter(["plain", "https"])
    reasons = iter(["no\nopenssl \x1b[31mred\x1b[0m\x00" + "x" * 5000, None])
    monkeypatch.setattr(station_client, "station_https", lambda url: next(classifications))
    monkeypatch.setattr(pairing, "station_plain_reason", lambda: next(reasons))
    (r,) = rows()
    assert r.text.startswith("The Weigh Station at station.local:5003 is not using HTTPS: it could not make its "
                             "certificate (no openssl red x")
    assert "\n" not in r.text and "\x1b" not in r.text and "\x00" not in r.text and len(r.text) <= 200
    assert r.text.endswith("…")
    assert r.fix == "Put right what the reason names on that machine, then restart the Weigh Station."
    assert next(classifications) == "https" and next(reasons) is None


def test_a_pinned_station_with_another_certificate_says_the_error_and_the_fix(monkeypatch):
    monkeypatch.setattr(station_client, "station_https", lambda url: "changed")
    (r,) = rows()
    assert r.state == "fail" and r.text == station_client.TEXT_STATION_CERT_CHANGED
    assert r.fix == station_client.TEXT_STATION_CERT_FIX and r.group == "Devices"


@pytest.mark.parametrize("seen", [None, "https", "unreachable"])
def test_no_row_for_a_station_on_https_or_one_that_does_not_answer_or_none(monkeypatch, seen):
    monkeypatch.setattr(station_client, "station_https", lambda url: seen)
    assert rows() == []


def test_the_tag_source_rows_use_the_words_of_the_design(monkeypatch):
    monkeypatch.setattr(station_client, "station_https", lambda url: None)
    monkeypatch.setattr(tagsources, "issues", lambda cfg: [
        {"name": "sv08", "url": "http://sv08.local:5002/last-tag", "kind": "plain",
         "text": tagsources.TEXT_NOT_HTTPS.format(address="sv08.local:5002"), "fix": None},
        {"name": "xi", "url": "https://xi.local:443/last-tag", "kind": "changed",
         "text": tagsources.TEXT_CERT_CHANGED.format(address="xi.local:443"), "fix": tagsources.TEXT_CERT_FIX}])
    a, b = rows({"tag_sources": [1]})
    assert (a.state, a.group, a.text, a.fix) == ("fail", "Devices", "The tag source sv08.local:5002 is not using HTTPS.", "")
    assert (b.state, b.text, b.fix) == ("fail", "The certificate of the tag source xi.local:443 has changed.",
                                        "Save the tag source again to trust the new certificate.")
    assert a.key != b.key


def test_the_rows_are_part_of_the_checks_when_a_station_is_set(monkeypatch):
    monkeypatch.setattr(station_client, "station_https", lambda url: "plain")
    monkeypatch.setattr(pairing, "station_plain_reason", lambda: None)
    cfg = config.load()
    cfg["modules"] = {"spoolman": True, "server": True, "station": True}
    cfg["station"] = {"url": STATION}
    called = []
    monkeypatch.setattr(status, "_station_rows", lambda url: called.append(url) or iter(()))
    got = list(status.rows(cfg, live_key=False))
    assert called == [STATION] and any(r.key == "devices.station-https" for r in got)


def test_the_checks_include_a_configured_plain_tag_source_without_a_station_url(monkeypatch):
    monkeypatch.setenv("FK_NO_SYSTEMD", "1")
    cfg = config.load()
    cfg["modules"] = {}
    cfg["station"] = {"url": None}
    cfg["tag_sources"] = [{"name": "sv08", "url": "http://sv08.local:5002/last-tag"}]
    got = list(status.rows(cfg, live_key=False))
    assert [r for r in got if r.group == "Devices"] == [
        status.Row("fail", "The tag source sv08.local:5002 is not using HTTPS.", group="Devices",
                   key="devices.tag-source-https-1"),
    ]


def test_the_remote_reader_http_row_is_a_note_with_no_fix_until_the_reader_serves_https(monkeypatch):
    cfg = {"modules": {"local_intake": True}, "local_intake": {"url": "http://reader.local:7915"}}
    monkeypatch.setattr(local_reader, "health", lambda c: {"ok": True, "url": "u", "version": "1", "api_version": "1.0",
                                                           "tiers": {}, "degraded": False, "last_read_at": None})
    monkeypatch.setattr(local_reader, "ENFORCE_HTTPS", False)
    r = next(x for x in status.local_reader_rows(cfg) if x.key == "label-reader.https")
    assert (r.state, r.group, r.fix) == ("info", "Label Reader", "")
    assert r.text == "The Label Reader at reader.local:7915 is not using HTTPS. A later Label Reader version adds it."
    monkeypatch.setattr(local_reader, "ENFORCE_HTTPS", True)
    r = next(x for x in status.local_reader_rows(cfg) if x.key == "label-reader.https")
    assert r.state == "fail" and r.text == "The Label Reader at reader.local:7915 is not using HTTPS."
    assert r.fix.startswith("Update the Label Reader on that machine")


def test_no_http_row_for_a_reader_on_this_machine_or_on_https(monkeypatch):
    monkeypatch.setattr(local_reader, "health", lambda c: {"ok": False, "url": "u", "error": "down"})
    for url in ("http://127.0.0.1:7915", "https://reader.local:7915"):
        cfg = {"modules": {"local_intake": True}, "local_intake": {"url": url}}
        assert not any(x.key == "label-reader.https" for x in status.local_reader_rows(cfg))


@pytest.mark.parametrize("url", ["http://127.0.0.1:5003", "http://127.0.0.2:5003", "http://localhost:5003",
                                "http://[::1]:5003", "http://[0:0:0:0:0:0:0:1]:5003"])
def test_a_plain_station_on_this_machine_has_no_not_on_https_row(monkeypatch, url):
    monkeypatch.setattr(station_client, "station_https", lambda u: "plain")
    assert rows({"station": {"url": url}, "tag_sources": []}) == []


def test_a_station_on_another_machine_still_gets_the_row_and_an_https_one_none(monkeypatch):
    monkeypatch.setattr(pairing, "station_plain_reason", lambda: None)
    for seen, n in (("plain", 1), ("https", 0)):
        monkeypatch.setattr(station_client, "station_https", lambda u, _s=seen: _s)
        assert len(rows({"station": {"url": "http://192.0.2.5:5003"}, "tag_sources": []})) == n
