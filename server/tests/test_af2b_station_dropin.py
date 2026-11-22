"""AF2b: the Weigh Station's drop-in follows station.page_from (R83 01:14), is rewritten when it must be, says its
closing line once, and the installer's closing address is the https one."""
import json
import subprocess

import pytest

from filament_kit import apply, cli, config, modules, pairing
from filament_kit.modules import ModuleError

HOME = " ".join(apply.HOME_RANGES)


def _cfg(page_from=None, allow=None):
    cfg = modules.set_mode(config._merge(config.DEFAULTS, {}), "all-in-one")
    if page_from is not None:
        cfg["station"]["page_from"] = page_from
    if allow is not None:
        cfg["station"]["allow"] = allow
    return cfg


def _line(text: str) -> str:
    return next(ln for ln in text.splitlines() if ln.startswith("IPAddressAllow="))


def _nobody(monkeypatch):
    monkeypatch.setattr(apply, "paired_server_ips", lambda: [])


def test_the_default_is_home():
    assert config.DEFAULTS["station"]["page_from"] == "home"


def test_home_admits_loopback_the_private_ranges_and_the_allow_list(monkeypatch):
    _nobody(monkeypatch)
    text = apply.station_dropin(_cfg("home"), ["127.0.0.1", "100.64.0.9"])
    assert _line(text) == f"IPAddressAllow=localhost {HOME} 127.0.0.1 100.64.0.9"
    assert HOME == "10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 fc00::/7 fe80::/10"
    assert "# page_from=home\n" in text


def test_a_station_with_no_page_from_set_is_home(monkeypatch):
    _nobody(monkeypatch)
    cfg = _cfg()
    del cfg["station"]["page_from"]
    assert _line(apply.station_dropin(cfg, ["127.0.0.1"])) == f"IPAddressAllow=localhost {HOME} 127.0.0.1"


def test_subnets_admits_the_machines_own_subnets_and_the_allow_list(monkeypatch):
    _nobody(monkeypatch)
    text = apply.station_dropin(_cfg("subnets"), ["127.0.0.1", "100.64.0.9"],
                                subnets=lambda: ["192.0.2.0/24", "2001:db8::/64"])
    assert _line(text) == "IPAddressAllow=localhost 192.0.2.0/24 2001:db8::/64 127.0.0.1 100.64.0.9"
    assert "# page_from=subnets\n" in text
    assert '"STATION_PAGE_RANGES=192.0.2.0/24 2001:db8::/64"' in text


@pytest.mark.parametrize("unread", [None, []])
def test_subnets_that_cannot_be_read_fall_back_to_home(monkeypatch, unread):
    _nobody(monkeypatch)
    text = apply.station_dropin(_cfg("subnets"), ["127.0.0.1"], subnets=lambda: unread)
    assert _line(text) == f"IPAddressAllow=localhost {HOME} 127.0.0.1"
    assert apply.PAGE_UNREAD in text


def test_allowed_admits_only_the_allow_list(monkeypatch):
    _nobody(monkeypatch)
    text = apply.station_dropin(_cfg("allowed"), ["127.0.0.1", "192.0.2.10"])
    assert _line(text) == "IPAddressAllow=localhost 127.0.0.1 192.0.2.10"
    assert "# page_from=allowed\n" in text
    assert "STATION_PAGE_RANGES= " in text


def test_allowed_with_an_empty_allow_list_admits_this_machine_only(monkeypatch):
    _nobody(monkeypatch)
    assert _line(apply.station_dropin(_cfg("allowed", allow=[]), [])) == "IPAddressAllow=localhost"


def test_the_paired_servers_addresses_stay_in_every_value(monkeypatch):
    monkeypatch.setattr(apply, "paired_server_ips", lambda: ["198.51.100.7"])
    for value in apply.PAGE_FROM:
        assert _line(apply.station_dropin(_cfg(value), ["127.0.0.1"], subnets=lambda: ["192.0.2.0/24"])
                     ).endswith(" 127.0.0.1 198.51.100.7")


def test_a_value_that_is_not_one_of_the_three_is_refused():
    with pytest.raises(ModuleError, match="station.page_from"):
        apply.station_dropin(_cfg("everyone"), ["127.0.0.1"])


def test_plan_passes_the_subnets_reader_through(monkeypatch):
    _nobody(monkeypatch)
    acts = apply.plan(_cfg("subnets"), resolve=lambda h: [h], subnets=lambda: ["203.0.113.0/24"])
    (_, content, _, _), = [a.arg for a in acts if a.kind == "install_file" and a.arg[0] == apply.DROPIN]
    assert _line(content) == "IPAddressAllow=localhost 203.0.113.0/24 127.0.0.1"


def _ip_json(*ifaces):
    return subprocess.CompletedProcess([], 0, json.dumps(list(ifaces)), "")


def test_own_subnets_reads_ip_addr_json():
    lo = {"ifname": "lo", "flags": ["LOOPBACK", "UP"],
          "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8, "scope": "host"}]}
    eth = {"ifname": "eth0", "flags": ["BROADCAST", "UP"],
           "addr_info": [{"family": "inet", "local": "192.168.1.20", "prefixlen": 24, "scope": "global"},
                         {"family": "inet6", "local": "fe80::1", "prefixlen": 64, "scope": "link"},
                         {"family": "inet6", "local": "2001:db8::5", "prefixlen": 64, "scope": "global"}]}
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        return _ip_json(lo, eth, eth)

    assert apply.own_subnets(run=run) == ["192.168.1.0/24", "2001:db8::/64"]
    assert seen == [["ip", "-j", "addr"]]


@pytest.mark.parametrize("fail", [FileNotFoundError("no ip"), subprocess.CalledProcessError(1, "ip")])
def test_own_subnets_is_none_when_ip_fails(fail):
    def run(cmd, **kw):
        raise fail
    assert apply.own_subnets(run=run) is None


def test_own_subnets_is_none_for_output_that_is_not_json_or_has_no_address():
    assert apply.own_subnets(run=lambda cmd, **kw: subprocess.CompletedProcess([], 0, "not json", "")) is None
    assert apply.own_subnets(run=lambda cmd, **kw: _ip_json({"ifname": "lo", "flags": ["LOOPBACK"],
                                                             "addr_info": []})) is None


# ---- when the drop-in is rewritten

def _refresh(cfg, root, ran, **kw):
    return apply.refresh_station(cfg, run=ran.append, root=root, resolve=lambda h: [h], **kw)


def test_station_pair_refresh_rewrites_when_page_from_changes(tmp_path, monkeypatch):
    _nobody(monkeypatch)
    monkeypatch.delenv("FK_NO_SYSTEMD", raising=False)
    root, ran = tmp_path / "root", []
    assert _refresh(_cfg("home"), root, ran) == "restarted"
    dropin = root / apply.DROPIN.lstrip("/")
    assert HOME in _line(dropin.read_text(encoding="utf-8"))
    ran.clear()
    assert _refresh(_cfg("allowed"), root, ran) == "restarted"            # config set station.page_from allowed
    assert HOME not in _line(dropin.read_text(encoding="utf-8"))
    assert ran == [["systemctl", "daemon-reload"], ["systemctl", "restart", apply.STATION_UNIT]]


def test_config_set_rewrites_the_station_dropin(tmp_path, monkeypatch):
    _nobody(monkeypatch)
    monkeypatch.delenv("FK_NO_SYSTEMD", raising=False)
    monkeypatch.setattr(cli, "_is_root", lambda: True)
    monkeypatch.setattr(cli, "_hub_audit", lambda *args: None)
    saved = _cfg("home", ["127.0.0.1"])
    monkeypatch.setattr(config, "load", lambda: saved)
    monkeypatch.setattr(config, "save", lambda cfg: saved.update(cfg))
    monkeypatch.setattr(cli, "_plan_error", lambda cfg: None)
    root, ran = tmp_path / "root", []
    refresh = apply.refresh_station
    monkeypatch.setattr(apply, "refresh_station",
                        lambda cfg: refresh(cfg, run=ran.append, root=root, resolve=lambda h: [h]))

    assert cli.cmd_config_set("station.page_from", "allowed") == 0
    assert saved["station"]["page_from"] == "allowed"
    assert cli.cmd_config_set("station.allow", "127.0.0.1, 100.64.0.9") == 0
    assert saved["station"]["allow"] == ["127.0.0.1", "100.64.0.9"]
    dropin = (root / apply.DROPIN.lstrip("/")).read_text(encoding="utf-8")
    assert _line(dropin) == "IPAddressAllow=localhost 127.0.0.1 100.64.0.9"
    assert ran == [["systemctl", "daemon-reload"], ["systemctl", "restart", apply.STATION_UNIT]] * 2


def test_refresh_rewrites_when_station_allow_changes(tmp_path, monkeypatch):
    _nobody(monkeypatch)
    monkeypatch.delenv("FK_NO_SYSTEMD", raising=False)
    root, ran = tmp_path / "root", []
    _refresh(_cfg("home", allow=["127.0.0.1"]), root, ran)
    ran.clear()
    assert _refresh(_cfg("home", allow=["127.0.0.1", "100.64.0.9"]), root, ran) == "restarted"
    assert _line((root / apply.DROPIN.lstrip("/")).read_text(encoding="utf-8")).endswith("127.0.0.1 100.64.0.9")


def test_refresh_with_the_same_values_does_nothing(tmp_path, monkeypatch):
    _nobody(monkeypatch)
    monkeypatch.delenv("FK_NO_SYSTEMD", raising=False)
    root, ran = tmp_path / "root", []
    _refresh(_cfg("home"), root, ran)
    ran.clear()
    assert _refresh(_cfg("home"), root, ran) == "unchanged"
    assert ran == [["systemctl", "is-active", "--quiet", apply.STATION_UNIT]]


def test_subnets_goes_stale_until_the_drop_in_is_written_again(tmp_path, monkeypatch):
    _nobody(monkeypatch)
    monkeypatch.delenv("FK_NO_SYSTEMD", raising=False)
    root, ran = tmp_path / "root", []
    _refresh(_cfg("subnets"), root, ran, subnets=lambda: ["192.0.2.0/24"])
    ran.clear()
    assert _refresh(_cfg("subnets"), root, ran, subnets=lambda: ["198.51.100.0/24"]) == "restarted"
    assert "198.51.100.0/24" in (root / apply.DROPIN.lstrip("/")).read_text(encoding="utf-8")


def test_refresh_says_when_the_subnets_could_not_be_read(tmp_path, monkeypatch, capsys):
    _nobody(monkeypatch)
    monkeypatch.delenv("FK_NO_SYSTEMD", raising=False)
    _refresh(_cfg("subnets"), tmp_path / "root", [], subnets=lambda: None)
    assert apply.TEXT_PAGE_UNREAD in capsys.readouterr().out


# ---- the closing line, once

def _old_dropin(root, text="[Service]\nIPAddressAllow=localhost 127.0.0.1\n"):
    path = root / apply.DROPIN.lstrip("/")
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")


def test_the_closing_line_is_exact():
    assert apply.TEXT_PAGE_NOW_HOME == (
        "Weigh Station: its page now opens from computers on your home network; its data still answers only the Hub "
        "and a browser the Hub has let in. To keep it to the allow-list: "
        "sudo spoolwarden config set station.page_from allowed")


def test_the_apply_that_changes_an_old_dropin_prints_the_line_once(tmp_path, monkeypatch, capsys):
    _nobody(monkeypatch)
    monkeypatch.setenv("FK_NO_SYSTEMD", "")
    root = tmp_path / "root"
    _old_dropin(root)
    acts = [a for a in apply.plan(_cfg("home"), resolve=lambda h: [h]) if a.kind == "install_file"
            and a.arg[0] == apply.DROPIN]
    apply.execute(acts, run=lambda cmd: None, root=root)
    assert capsys.readouterr().out.count(apply.TEXT_PAGE_NOW_HOME) == 1
    apply.execute(acts, run=lambda cmd: None, root=root)                      # the next apply: nothing changes
    assert apply.TEXT_PAGE_NOW_HOME not in capsys.readouterr().out
    acts = [a for a in apply.plan(_cfg("allowed"), resolve=lambda h: [h]) if a.kind == "install_file"
            and a.arg[0] == apply.DROPIN]
    apply.execute(acts, run=lambda cmd: None, root=root)                      # a later change: not said again
    assert apply.TEXT_PAGE_NOW_HOME not in capsys.readouterr().out


def test_a_new_install_and_the_allowed_value_do_not_print_the_line(tmp_path, monkeypatch, capsys):
    _nobody(monkeypatch)
    monkeypatch.setenv("FK_NO_SYSTEMD", "")
    new_root, old_root = tmp_path / "new", tmp_path / "old"
    _old_dropin(old_root)
    for root, value in ((new_root, "home"), (old_root, "allowed")):
        acts = [a for a in apply.plan(_cfg(value), resolve=lambda h: [h]) if a.kind == "install_file"
                and a.arg[0] == apply.DROPIN]
        apply.execute(acts, run=lambda cmd: None, root=root)
    assert apply.TEXT_PAGE_NOW_HOME not in capsys.readouterr().out


def test_station_pair_on_an_old_dropin_prints_the_line_once(tmp_path, monkeypatch, capsys):
    _nobody(monkeypatch)
    monkeypatch.delenv("FK_NO_SYSTEMD", raising=False)
    root = tmp_path / "root"
    _old_dropin(root)
    _refresh(_cfg("home"), root, [])
    assert capsys.readouterr().out.count(apply.TEXT_PAGE_NOW_HOME) == 1
    _refresh(_cfg("home", allow=["127.0.0.1", "100.64.0.9"]), root, [])
    assert apply.TEXT_PAGE_NOW_HOME not in capsys.readouterr().out


# ---- the address the installer ends with

def _finish_line(monkeypatch, capsys, port, plain=None):
    monkeypatch.setenv("FK_NO_SYSTEMD", "1")
    monkeypatch.setattr(pairing, "station_plain_reason", lambda: plain)
    monkeypatch.setattr(apply, "_firewall_note", lambda cfg, on: None)
    monkeypatch.setattr(apply.socket, "gethostname", lambda: "pi")
    cfg = modules.set_mode(config._merge(config.DEFAULTS, {}), "station")
    cfg["station"].update(allow=["spoolman.example.lan"], port=port)
    apply.finish(cfg, run=lambda cmd: None, pairing=False)
    return next(ln for ln in capsys.readouterr().out.splitlines() if "Station:" in ln)


def test_the_closing_address_is_https_and_has_no_port_on_443(monkeypatch, capsys):
    assert "Station: https://pi.local/state" in _finish_line(monkeypatch, capsys, 443)


def test_the_closing_address_keeps_another_port(monkeypatch, capsys):
    assert "Station: https://pi.local:5003/state" in _finish_line(monkeypatch, capsys, 5003)


def test_the_closing_address_is_plain_only_for_a_plain_station(monkeypatch, capsys):
    assert "Station: http://pi.local:5003/state" in _finish_line(monkeypatch, capsys, 5003, plain="OSError: no")
