import base64
import hashlib
import io
import json
import sys
import threading
import urllib.parse
import urllib.request

import pytest

from fksync import __main__ as cli
from fksync import client, store
from tls_helper import TlsServer, make_cert

DEVICE_TOKEN = "fk_dev_" + "d" * 32
USER_TOKEN = "fk_usr_" + "u" * 32


# --- store: user_session & active_token ---------------------------------------------------------


def test_user_session_roundtrip_and_active_token(tmp_path):
    d = store.AppDirs(tmp_path / "fks")
    assert store.load_user_session(d) is None
    assert store.active_token(d) is None

    store.save_token(d, DEVICE_TOKEN)
    assert store.active_token(d) == DEVICE_TOKEN

    session = {
        "token": USER_TOKEN,
        "username": "alice",
        "role": "admin",
        "permissions": ["presets:write", "devices:read"],
    }
    store.save_user_session(d, session)
    raw = d.user_session.read_bytes()
    if sys.platform == "win32":
        assert USER_TOKEN.encode() not in raw
    else:
        assert (d.user_session.stat().st_mode & 0o777) == 0o600

    loaded = store.load_user_session(d)
    assert loaded == session
    assert store.active_token(d) == USER_TOKEN

    store.clear_user_session(d)
    store.clear_user_session(d)  # idempotent
    assert store.load_user_session(d) is None
    assert store.active_token(d) == DEVICE_TOKEN


def test_user_session_corrupt_returns_none(tmp_path):
    d = store.AppDirs(tmp_path / "fks")
    d.ensure()
    d.user_session.write_bytes(b"not-valid-session")
    assert store.load_user_session(d) is None


# --- client: enterprise auth routes -------------------------------------------------------------


def test_client_enterprise_routes(tmp_path):
    cert, key, fp = make_cert(tmp_path / "cert")
    ent_state = {"enabled": True, "login_required": True, "methods": ["local", "saml"]}

    def respond(method, path, body):
        if path == "/api/info":
            if ent_state is None:
                return 200, "application/json", {"name": "fk-hub", "api": 1}
            return 200, "application/json", {"name": "fk-hub", "api": 1, "enterprise": ent_state}
        if method == "POST" and path == "/api/auth/login":
            payload = json.loads(body)
            if payload.get("username") == "alice" and payload.get("password") == "s3cr3t":
                return 200, "application/json", {
                    "token": USER_TOKEN,
                    "user": {"username": "alice"},
                    "role": "operator",
                    "permissions": ["presets:read"],
                }
            return 401, "application/json", {"error": "invalid_credentials", "message": "bad login"}
        if method == "POST" and path == "/api/auth/exchange":
            payload = json.loads(body)
            if payload.get("code") == "good-code" and payload.get("code_verifier"):
                return 200, "application/json", {
                    "token": USER_TOKEN,
                    "user": {"username": "sso-user"},
                    "role": "admin",
                    "permissions": ["*"],
                }
            return 200, "application/json", {}  # missing token -> ApiError bad_response
        if method == "GET" and path == "/api/auth/me":
            return 200, "application/json", {"username": "alice", "role": "operator"}
        if method == "POST" and path == "/api/auth/logout":
            return 200, "application/json", {"ok": True}
        return 404, "application/json", {"error": "not_found", "message": "unknown"}

    with TlsServer(cert, key, respond=respond) as srv:
        cfg = {"host": srv.host, "port": srv.port, "fp": fp, "ips": []}
        anon = client.Client(cfg, None, timeout=5)

        assert anon.enterprise_info() == {"enabled": True, "login_required": True, "methods": ["local", "saml"]}
        assert anon.login_required() is True

        res = anon.login_password("alice", "s3cr3t")
        assert res["token"] == USER_TOKEN
        assert srv.requests[-1]["auth"] is None
        assert json.loads(srv.requests[-1]["body"]) == {
            "username": "alice", "password": "s3cr3t", "client": "fksync",
        }

        with pytest.raises(client.ApiError) as ei:
            anon.login_password("alice", "wrong")
        assert ei.value.status == 401

        ex = anon.exchange_pkce("good-code", "verifier-123")
        assert ex["token"] == USER_TOKEN
        assert srv.requests[-1]["auth"] is None
        assert json.loads(srv.requests[-1]["body"]) == {
            "code": "good-code", "code_verifier": "verifier-123",
        }

        with pytest.raises(client.ApiError) as ei:
            anon.exchange_pkce("no-token", "v")
        assert ei.value.code == "bad_response"

        ent_state = None
        assert anon.enterprise_info() == {"enabled": False, "login_required": False, "methods": []}
        assert anon.login_required() is False

        authed = client.Client(cfg, USER_TOKEN, timeout=5)
        assert authed.me() == {"username": "alice", "role": "operator"}
        assert srv.requests[-1]["auth"] == f"Bearer {USER_TOKEN}"
        assert authed.logout() == {"ok": True}
        assert srv.requests[-1]["auth"] == f"Bearer {USER_TOKEN}"


# --- __main__: cmd_login & cmd_unpair -----------------------------------------------------------


def _paired_dirs(tmp_path, srv, fp) -> store.AppDirs:
    dirs = store.AppDirs(tmp_path / "local" / "filament-kit-sync")
    dirs.ensure()
    store.save_config(dirs, {"host": srv.host, "port": srv.port, "fp": fp, "ips": [], "device": "dev-1"})
    store.save_token(dirs, DEVICE_TOKEN)
    return dirs


def test_cmd_login_requires_pairing_and_enterprise(tmp_path, capsys):
    dirs = store.AppDirs(tmp_path / "local" / "filament-kit-sync")
    dirs.ensure()
    assert cli.cmd_login(dirs) == 1
    assert "not paired" in capsys.readouterr().out

    cert, key, fp = make_cert(tmp_path / "cert")

    def respond(m, p, b):
        return 200, "application/json", {"name": "fk-hub", "api": 1, "enterprise": {"enabled": False}}

    with TlsServer(cert, key, respond=respond) as srv:
        dirs = _paired_dirs(tmp_path, srv, fp)
        assert cli.cmd_login(dirs) == 1
        assert "not enabled" in capsys.readouterr().out


def test_cmd_login_password_interactive_and_failure(tmp_path, monkeypatch, capsys):
    cert, key, fp = make_cert(tmp_path / "cert")

    def respond(method, path, body):
        if path == "/api/info":
            return 200, "application/json", {
                "name": "fk-hub", "api": 1,
                "enterprise": {"enabled": True, "login_required": True, "methods": ["local", "ldap"]},
            }
        if path == "/api/auth/login":
            payload = json.loads(body)
            if payload["username"] == "bob" and payload["password"] == "pw123":
                return 200, "application/json", {
                    "token": USER_TOKEN,
                    "user": {"username": "bob"},
                    "role": "user",
                    "permissions": ["presets:read"],
                }
            return 401, "application/json", {"error": "unauthorized", "message": "bad password"}
        return 404, "application/json", {}

    with TlsServer(cert, key, respond=respond) as srv:
        dirs = _paired_dirs(tmp_path, srv, fp)
        monkeypatch.setattr(sys, "stdin", io.StringIO("bob\n"))
        monkeypatch.setattr("getpass.getpass", lambda _prompt="": "pw123")
        rc = cli.cmd_login(dirs)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Signed in as bob" in out
        sess = store.load_user_session(dirs)
        assert sess == {
            "token": USER_TOKEN,
            "username": "bob",
            "role": "user",
            "permissions": ["presets:read"],
        }

        # Failed password login leaves no session when cleared first
        store.clear_user_session(dirs)
        monkeypatch.setattr("getpass.getpass", lambda _prompt="": "wrong")
        rc_fail = cli.cmd_login(dirs, username="bob")
        assert rc_fail == 1
        assert store.load_user_session(dirs) is None


def test_cmd_login_sso_loopback_pkce(tmp_path, monkeypatch, capsys):
    cert, key, fp = make_cert(tmp_path / "cert")
    seen_exchange = {}

    def respond(method, path, body):
        if path == "/api/info":
            return 200, "application/json", {
                "name": "fk-hub", "api": 1,
                "enterprise": {"enabled": True, "login_required": True, "methods": ["saml"]},
            }
        if path == "/api/auth/exchange":
            payload = json.loads(body)
            seen_exchange.update(payload)
            return 200, "application/json", {
                "token": USER_TOKEN,
                "user": {"username": "sso-alice"},
                "role": "admin",
                "permissions": ["presets:write"],
            }
        return 404, "application/json", {}

    opened = {}

    def fake_open(url):
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        opened["qs"] = qs
        redirect_uri = qs["redirect_uri"][0]

        def send_callback():
            state = qs["state"][0]
            assert state
            with urllib.request.urlopen(
                f"{redirect_uri}?code=saml-auth-code&state={state}"
            ) as resp:
                resp.read()

        threading.Thread(target=send_callback, daemon=True).start()
        return True

    monkeypatch.setattr("webbrowser.open", fake_open)

    with TlsServer(cert, key, respond=respond) as srv:
        dirs = _paired_dirs(tmp_path, srv, fp)
        rc = cli.cmd_login(dirs)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Signed in as sso-alice" in out

    assert seen_exchange["code"] == "saml-auth-code"
    verifier = seen_exchange["code_verifier"]
    expected_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    assert opened["qs"]["code_challenge_method"] == ["S256"]
    assert opened["qs"]["code_challenge"] == [expected_challenge]
    assert store.load_user_session(dirs) == {
        "token": USER_TOKEN,
        "username": "sso-alice",
        "role": "admin",
        "permissions": ["presets:write"],
    }


def test_cmd_unpair_clears_user_session(tmp_path, capsys):
    cert, key, fp = make_cert(tmp_path / "cert")
    with TlsServer(cert, key) as srv:
        dirs = _paired_dirs(tmp_path, srv, fp)
        store.save_user_session(dirs, {
            "token": USER_TOKEN,
            "username": "alice",
            "role": "admin",
            "permissions": ["*"],
        })
        assert store.active_token(dirs) == USER_TOKEN

        rc = cli.cmd_unpair(dirs, yes=True)
        assert rc == 0
        assert "unpaired" in capsys.readouterr().out.lower()
        assert store.load_token(dirs) is None
        assert store.load_user_session(dirs) is None
        assert store.active_token(dirs) is None
