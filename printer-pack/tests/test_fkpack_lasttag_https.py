"""the lasttag service's unit. A new install may take port 443 (a unit below 1024 gets
exactly CAP_NET_BIND_SERVICE), an existing install keeps its port, and an install made before the service served
HTTPS gets its unit rewritten (once) so it can keep a certificate. systemd is simulated as in test_fkpack_reinstall."""
import fkpack  # noqa: F401  (test_fkpack puts the pack on sys.path)
import pytest

from test_fkpack import UPSTREAM, data, moon  # noqa: F401  (fixtures)
from test_fkpack_reinstall import Systemd, env  # noqa: F401  (fixtures and the systemd stand-in)

OLD_UNIT = ("[Unit]\nDescription=lasttag: read-only last NFC tag for Spoolwarden (port 5002)\n"
            "[Service]\nUser=pi\nExecStart=/x/python -I /x/lasttag.py\nRestart=always\nNoNewPrivileges=yes\n"
            "ProtectSystem=strict\nPrivateTmp=yes\n[Install]\nWantedBy=multi-user.target\n")


def mk(data, moon, home, units, sd, answers=None, free=True):
    said, asked = [], []

    def ask(q):
        asked.append(q)
        return answers(q) if answers else True

    p = fkpack.Pack(str(data), moon.url, home=str(home), ask=ask, out=said.append, run=sd, units_dir=str(units),
                    klipper_dir=str(data.parent / "klipper"), port_free=lambda port: free)
    p.said, p.asked = said, asked
    return p


def _unit(units):
    return (units / "lasttag.service").read_text(encoding="utf-8")


def test_a_new_install_takes_443_when_it_is_free_and_the_owner_says_yes(env, moon):
    data, home, units = env
    p = mk(data, moon, home, units, Systemd(units))
    p.install(["lasttag"], allow=["192.0.2.10"])
    unit = _unit(units)
    assert any("port 443" in q for q in p.asked)
    assert "Environment=LASTTAG_PORT=443\n" in unit and "(port 443)" in unit
    assert "CapabilityBoundingSet=CAP_NET_BIND_SERVICE\nAmbientCapabilities=CAP_NET_BIND_SERVICE\n" in unit
    assert "StateDirectory=lasttag\n" in unit


def test_a_new_install_stays_on_5002_when_the_owner_says_no(env, moon):
    data, home, units = env
    p = mk(data, moon, home, units, Systemd(units), answers=lambda q: "443" not in q)
    p.install(["lasttag"], allow=["192.0.2.10"])
    unit = _unit(units)
    assert "LASTTAG_PORT" not in unit and "CAP_NET_BIND_SERVICE" not in unit and "(port 5002)" in unit
    assert any("port 5002, read only" in q for q in p.asked)


def test_a_new_install_is_not_asked_about_443_when_it_is_taken(env, moon):
    data, home, units = env
    p = mk(data, moon, home, units, Systemd(units), free=False)
    p.install(["lasttag"], allow=["192.0.2.10"])
    assert not any("443" in q for q in p.asked)
    assert "LASTTAG_PORT" not in _unit(units) and "(port 5002)" in _unit(units)


def _old_install(env, moon, text=OLD_UNIT):
    data, home, units = env
    sd = Systemd(units)
    mk(data, moon, home, units, sd, free=False).install(["lasttag"], allow=["192.0.2.10"])
    (units / "lasttag.service").write_text(text, encoding="utf-8")
    return data, home, units, sd


def test_an_existing_install_gets_its_unit_rewritten_and_restarted_keeping_5002(env, moon):
    data, home, units, sd = _old_install(env, moon)
    sd.runs.clear()
    p = mk(data, moon, home, units, sd, free=True)          # 443 free now: an existing install never moves to it
    p.install(["lasttag"])
    unit = _unit(units)
    assert "StateDirectory=lasttag\n" in unit and "LASTTAG_PORT" not in unit and "(port 5002)" in unit
    assert not any("443" in q for q in p.asked)
    assert sd.runs[-2:] == [["sudo", "systemctl", "daemon-reload"], ["sudo", "systemctl", "restart", "lasttag"]]


def test_the_rewrite_happens_once(env, moon):
    data, home, units, sd = _old_install(env, moon)
    mk(data, moon, home, units, sd).install(["lasttag"])
    sd.runs.clear()
    mk(data, moon, home, units, sd).install(["lasttag"])
    assert not [r for r in sd.runs if r[:3] in (["sudo", "systemctl", "restart"], ["sudo", "install", "-m"])]


def test_an_existing_port_in_the_old_unit_is_kept(env, moon):
    old = OLD_UNIT.replace("Restart=always\n", 'Restart=always\nEnvironment=A=1 "LASTTAG_PORT=5010"\n')
    data, home, units, sd = _old_install(env, moon, old)
    mk(data, moon, home, units, sd).install(["lasttag"])
    assert "Environment=LASTTAG_PORT=5010\n" in _unit(units)


def test_the_owner_can_keep_the_old_unit(env, moon):
    data, home, units, sd = _old_install(env, moon)
    sd.runs.clear()
    mk(data, moon, home, units, sd, answers=lambda q: "HTTPS" not in q).install(["lasttag"])
    assert _unit(units) == OLD_UNIT
    assert ["sudo", "systemctl", "restart", "lasttag"] not in sd.runs
