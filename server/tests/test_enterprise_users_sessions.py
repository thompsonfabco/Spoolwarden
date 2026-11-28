"""Tests for enterprise users, sessions, PKCE, API auth/RBAC, and setup auth routes."""
from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from filament_kit import config, pairing, setup_page
from filament_kit.api import create_app
from filament_kit.enterprise import policy, sessions, users
from filament_kit.enterprise.sessions import PKCE_TTL_S, SessionError
from filament_kit.enterprise.users import AuthError, UserError
from filament_kit.spoolman import Spoolman


@pytest.fixture(autouse=True)
def enterprise_env(tmp_path, monkeypatch):
    """Isolate config/state/opt dirs, speed up scrypt, and enable the enterprise module."""
    for name in ("etc", "state", "opt"):
        d = tmp_path / name
        d.mkdir(exist_ok=True)
        monkeypatch.setenv(f"FK_{name.upper()}", str(d))
    pairing.make_cert("fkhost", [], tmp_path / "etc")
    config.save({"placement": {"box_count": 2}, "modules": {"enterprise": True}})
    monkeypatch.setattr(users, "SCRYPT", {"n": 2 ** 10, "r": 8, "p": 1, "maxmem": 2 ** 26})


@pytest.fixture
def sm(spoolman_url):
    s = Spoolman(spoolman_url)
    s.ensure_fields()
    return s


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _auth(tok: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


# ---------------------------------------------------------------------------
# 1. Users, salted scrypt hashing, lockout, last-active-admin guard
# ---------------------------------------------------------------------------


def test_create_user_and_salted_scrypt_hashing():
    u1 = users.create_user("alice", "operator", password="correct-horse-battery", display_name="Alice")
    u2 = users.create_user("bob", "viewer", password="correct-horse-battery")

    assert u1["id"].startswith("usr_")
    assert u1["username"] == "alice"
    assert u1["role"] == "operator"
    assert u1["active"] is True
    assert "password_hash" not in u1
    assert "fails" not in u1

    raw_xml = users._users_path().read_text(encoding="utf-8")
    assert "correct-horse-battery" not in raw_xml

    parsed = users._parse_users_xml(users._users_path().read_bytes())
    hashes = {u["username"]: u["password_hash"] for u in parsed}
    assert " " in hashes["alice"]
    assert hashes["alice"] != hashes["bob"]  # distinct random 16-byte salts
    assert users._verify_password("correct-horse-battery", hashes["alice"])
    assert not users._verify_password("wrong-password-123", hashes["alice"])


def test_authenticate_local_success_and_lockout_after_5_failures():
    users.create_user("carol", "operator", password="strong-password-123")

    # 4 failures then success resets failure counter
    for _ in range(4):
        with pytest.raises(AuthError, match="Invalid username or password"):
            users.authenticate_local("carol", "wrong-password")

    authed = users.authenticate_local("CAROL", "strong-password-123")
    assert authed["username"] == "carol"
    assert authed["last_login_at"]

    # 5 consecutive failures trigger lockout on the 6th attempt even with right password
    for _ in range(5):
        with pytest.raises(AuthError, match="Invalid username or password"):
            users.authenticate_local("carol", "wrong-password")

    with pytest.raises(AuthError, match="locked"):
        users.authenticate_local("carol", "strong-password-123")


def test_last_active_admin_guard_on_update_and_delete():
    admin1 = users.create_user("admin1", "administrator", password="admin-password-1")
    viewer = users.create_user("viewer1", "viewer", password="viewer-password-1")

    with pytest.raises(UserError, match="last active administrator"):
        users.delete_user(admin1["id"])

    with pytest.raises(UserError, match="last active administrator"):
        users.update_user(admin1["id"], role="operator")

    with pytest.raises(UserError, match="last active administrator"):
        users.update_user(admin1["id"], active=False)

    # Non-admin can be deleted freely
    users.delete_user(viewer["id"])
    assert users.get_user(viewer["id"]) is None

    # Adding a second active admin allows demoting/deleting the first
    admin2 = users.create_user("admin2", "administrator", password="admin-password-2")
    demoted = users.update_user(admin1["id"], role="operator")
    assert demoted["role"] == "operator"

    # Now admin2 is the last active admin
    with pytest.raises(UserError, match="last active administrator"):
        users.delete_user(admin2["id"])


# ---------------------------------------------------------------------------
# 2. Sessions (browser 8h idle / 24h abs, client 30d sliding) & revocation
# ---------------------------------------------------------------------------


def test_browser_session_idle_and_absolute_expiry(monkeypatch):
    clock = [1_000_000.0]
    monkeypatch.setattr(sessions.time, "time", lambda: clock[0])

    u = users.create_user("dana", "operator", password="dana-password-123")
    tok, sess = sessions.create_session(u, kind="browser", auth_method="local")
    assert sess["kind"] == "browser"
    assert sess["idle_s"] == 28800
    assert sess["expires_at"] == pytest.approx(clock[0] + 86400)
    assert sess["idle_expires_at"] == pytest.approx(clock[0] + 28800)

    # Activity at +4h renews idle_expires_at by 8h, keeping absolute 24h cap
    clock[0] += 4 * 3600
    touched = sessions.validate_session(tok, touch=True)
    assert touched is not None
    assert touched["idle_expires_at"] == pytest.approx(clock[0] + 28800)
    assert touched["expires_at"] == pytest.approx(1_000_000.0 + 86400)

    # Idle for >= 8h expires the browser session even before 24h abs
    clock[0] += 8 * 3600
    assert sessions.validate_session(tok) is None


def test_browser_session_absolute_cap(monkeypatch):
    clock = [2_000_000.0]
    monkeypatch.setattr(sessions.time, "time", lambda: clock[0])

    u = users.create_user("eli", "operator", password="eli-password-123")
    tok, _ = sessions.create_session(u, kind="browser")

    # Touch every 6h; at 24h absolute cap it must expire
    for _ in range(3):
        clock[0] += 6 * 3600
        assert sessions.validate_session(tok, touch=True) is not None
    clock[0] += 6 * 3600
    assert sessions.validate_session(tok) is None


def test_client_session_30d_sliding_and_revocation(monkeypatch):
    clock = [3_000_000.0]
    monkeypatch.setattr(sessions.time, "time", lambda: clock[0])

    u1 = users.create_user("fay", "operator", password="fay-password-123")
    u2 = users.create_user("gus", "viewer", password="gus-password-123")

    tok1, s1 = sessions.create_session(u1, kind="client")
    tok1b, _ = sessions.create_session(u1, kind="client")
    tok2, s2 = sessions.create_session(u2, kind="client")

    assert s1["kind"] == "client"
    assert s1["sliding_s"] == 2592000
    assert s1["idle_expires_at"] == 0.0
    assert s1["expires_at"] == pytest.approx(clock[0] + 2592000)

    # Touching at +20d slides expiry out by another 30d
    clock[0] += 20 * 86400
    renewed = sessions.validate_session(tok1, touch=True)
    assert renewed is not None
    assert renewed["expires_at"] == pytest.approx(clock[0] + 2592000)

    # Revoke single session
    assert sessions.revoke_session(s2["id"]) is True
    assert sessions.revoke_session(s2["id"]) is False
    assert sessions.validate_session(tok2) is None

    # Revoke all sessions for u1
    removed = sessions.revoke_user_sessions(u1["id"])
    assert removed == 2
    assert sessions.validate_session(tok1) is None
    assert sessions.validate_session(tok1b) is None


# ---------------------------------------------------------------------------
# 3. PKCE S256 verification, wrong verifier, single-use replay, 120s expiry
# ---------------------------------------------------------------------------


def test_pkce_s256_exchange_replay_wrong_verifier_and_expiry(monkeypatch):
    clock = [4_000_000.0]
    monkeypatch.setattr(sessions.time, "time", lambda: clock[0])

    u = users.create_user("hana", "operator", password="hana-password-123")
    verifier = "verifier-secret-string-for-pkce-s256"
    challenge = _s256(verifier)

    # Valid S256 exchange issues a client session
    code = sessions.create_pkce_code(u, "android", challenge, "spoolwarden://callback")
    tok, sess = sessions.exchange_pkce_code(code, verifier, client_type="android")
    assert sess["kind"] == "client"
    assert sess["auth_method"] == "pkce"
    assert sessions.validate_session(tok) is not None

    # Single-use replay rejection
    with pytest.raises(SessionError, match="Invalid or expired authorization code"):
        sessions.exchange_pkce_code(code, verifier, client_type="android")

    # Wrong verifier rejection (and code is consumed on attempt)
    code2 = sessions.create_pkce_code(u, "android", challenge, "spoolwarden://callback")
    with pytest.raises(SessionError, match="Invalid code_verifier"):
        sessions.exchange_pkce_code(code2, "wrong-verifier")
    with pytest.raises(SessionError, match="Invalid or expired authorization code"):
        sessions.exchange_pkce_code(code2, verifier)

    # 120s expiry
    code3 = sessions.create_pkce_code(u, "android", challenge, "spoolwarden://callback")
    clock[0] += PKCE_TTL_S
    with pytest.raises(SessionError, match="Invalid or expired authorization code"):
        sessions.exchange_pkce_code(code3, verifier)


# ---------------------------------------------------------------------------
# 4. FastAPI/Starlette TestClient tests on create_app + setup_page/enterprise_tab
# ---------------------------------------------------------------------------


def test_api_info_enterprise_block_on_and_off(sm):
    c = TestClient(create_app(sm=sm))
    r = c.get("/api/info")
    assert r.status_code == 200
    assert r.json()["enterprise"] == {
        "enabled": True,
        "login_required": False,
        "methods": ["local"],
    }

    pol = policy.load_policy()
    pol["toggles"]["multiuser_login"] = True
    policy.save_policy(pol)
    assert c.get("/api/info").json()["enterprise"]["login_required"] is True

    config.save({"placement": {"box_count": 2}, "modules": {"enterprise": False}})
    c_off = TestClient(create_app(sm=sm))
    assert c_off.get("/api/info").json()["enterprise"] == {
        "enabled": False,
        "login_required": False,
        "methods": [],
    }


def test_api_auth_login_exchange_me_logout_rbac_and_pairing(sm):
    viewer = users.create_user("view_u", "viewer", password="viewer-password-123")
    op = users.create_user("op_u", "operator", password="operator-password-123")

    pol = policy.load_policy()
    pol["toggles"]["rbac"] = True
    policy.save_policy(pol)

    c = TestClient(create_app(sm=sm))

    # /api/auth/login success & failure
    bad = c.post("/api/auth/login", json={"username": "view_u", "password": "wrong"})
    assert bad.status_code == 401

    login = c.post("/api/auth/login", json={"username": "view_u", "password": "viewer-password-123", "client": "pc-sync"})
    assert login.status_code == 200
    body = login.json()
    tok = body["token"]
    assert body["user"]["id"] == viewer["id"]
    assert body["role"] == "viewer"
    assert body["client"] == "pc-sync"

    # /api/auth/me with Bearer token
    me = c.get("/api/auth/me", headers=_auth(tok))
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "view_u"
    assert me.json()["role"] == "viewer"

    # RBAC 403 (never 401) when viewer lacks RW permission on PATCH /api/spools/1
    denied = c.patch("/api/spools/1", headers=_auth(tok), json={"remaining_weight": 500})
    assert denied.status_code == 403
    assert denied.json()["error"] == "forbidden"

    # /api/auth/logout revokes the session
    out = c.post("/api/auth/logout", headers=_auth(tok))
    assert out.status_code == 200
    assert out.json() == {"logged_out": True}
    assert c.get("/api/auth/me", headers=_auth(tok)).status_code == 401

    # Expired/revoked fks_ user token returns 401 session_expired
    expired = c.get("/api/auth/me", headers=_auth("fks_expired_or_revoked_token"))
    assert expired.status_code == 401
    assert expired.json()["error"] == "session_expired"
    assert expired.json()["code"] == "session_expired"

    # /api/auth/exchange with PKCE S256
    verifier = "client-pkce-verifier-1234567890"
    code = sessions.create_pkce_code(op, "android", _s256(verifier), "spoolwarden://cb")
    ex = c.post("/api/auth/exchange", json={"code": code, "code_verifier": verifier})
    assert ex.status_code == 200
    ex_tok = ex.json()["token"]
    assert ex.json()["role"] == "operator"
    assert c.get("/api/auth/me", headers=_auth(ex_tok)).status_code == 200

    # Replay /api/auth/exchange fails with 401
    assert c.post("/api/auth/exchange", json={"code": code, "code_verifier": verifier}).status_code == 401

    # Existing pairing.issue() device tokens still work alongside enterprise
    dev_tok = pairing.redeem_code(pairing.issue().code)
    dev_me = c.get("/api/auth/me", headers=_auth(dev_tok))
    assert dev_me.status_code == 200
    assert dev_me.json()["device"] is not None
    assert c.get("/api/spools", headers=_auth(dev_tok)).status_code == 200


def test_setup_page_and_enterprise_tab_auth_routes():
    users.create_user("web_admin", "administrator", password="web-admin-password-123")

    class _FakeHelper:
        def __call__(self, req: dict) -> dict:
            op = req.get("op")
            if op == "enterprise_auth_local":
                try:
                    u = users.authenticate_local(req["username"], req["password"])
                except AuthError as exc:
                    raise setup_page.Refused(401, str(exc)) from None
                return {"ok": True, "user": u}
            if op == "enterprise_auth_login":
                try:
                    u = users.authenticate_local(req["username"], req["password"])
                except AuthError as exc:
                    raise setup_page.Refused(401, str(exc)) from None
                tok, sess = sessions.create_session(u, kind="browser", auth_method="local")
                return {"token": tok, "user": u, "max_age": sess["idle_s"]}
            if op == "enterprise_auth_me":
                sess = sessions.validate_session(req["token"])
                if sess is None:
                    return {"authenticated": False, "user": None}
                return {"authenticated": True, "user": {"id": sess["user_id"], "username": sess["username"], "role": sess["role"]}}
            if op == "enterprise_auth_logout":
                sess = sessions.validate_session(req["token"], touch=False)
                if sess is not None:
                    sessions.revoke_session(sess["id"])
                return {"ok": True}
            if op == "enterprise_auth_code":
                sess = sessions.validate_session(req["token"])
                if sess is None:
                    raise setup_page.Refused(401, "not authenticated")
                code = sessions.create_pkce_code(
                    {"id": sess["user_id"], "username": sess["username"], "role": sess["role"]},
                    "client",
                    req["code_challenge"],
                    req["redirect_uri"],
                )
                return {"code": code}
            return {"ok": True}

    page = setup_page.SetupPage(helper=_FakeHelper(), hosts=lambda: {"workshop-hub"})
    c = TestClient(
        Starlette(routes=page.routes()),
        client=("127.0.0.1", 1234),
        base_url="https://workshop-hub:8710",
    )
    origin_hdr = {"Origin": "https://workshop-hub:8710"}

    # Login sets fk_session cookie
    r = c.post("/auth/login", headers=origin_hdr, json={"username": "web_admin", "password": "web-admin-password-123"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert "fk_session" in c.cookies

    # /auth/me reflects authenticated user
    me = c.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["authenticated"] is True
    assert me.json()["user"]["username"] == "web_admin"

    # /auth/client-login redirects with PKCE code
    verifier = "setup-pkce-verifier-1234567890"
    redir = c.get(
        "/auth/client-login",
        params={"redirect_uri": "http://127.0.0.1:9999/cb", "code_challenge": _s256(verifier), "state": "xyz"},
        follow_redirects=False,
    )
    assert redir.status_code == 302
    loc = urlparse(redir.headers["location"])
    qs = parse_qs(loc.query)
    assert qs["state"] == ["xyz"]
    tok, _ = sessions.exchange_pkce_code(qs["code"][0], verifier)
    assert sessions.validate_session(tok) is not None

    # /auth/logout clears cookie and revokes session
    out = c.post("/auth/logout", headers=origin_hdr)
    assert out.status_code == 200
    assert c.get("/auth/me").json()["authenticated"] is False
