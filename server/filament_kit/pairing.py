"""Phone pairing: self-signed cert, pairing tokens/codes, device registry."""
from __future__ import annotations

import datetime as dt
import errno
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

# cryptography is imported where a certificate is read or made, so the file helpers below (_write_atomic,
# open_lock ...) import without it: the release job's library build uses them with a plain Python.

from . import config, event_sink

log = logging.getLogger(__name__)

try:
    import fcntl
except ImportError:  # Windows dev PCs: no cross-process lock (the service only runs on Linux)
    fcntl = None

TOKEN_TTL = 600
MAX_ATTEMPTS = 5
LAST_SEEN_STALE = 60
SPENT_KEEP = max(3600, TOKEN_TTL)   # how long a spent code is remembered, to tell "used up" from "never issued"
_lock = threading.Lock()


@contextmanager
def _locked():
    """Serialize pairing state between threads and with the CLI (`pair`/`unpair`) in another process."""
    with _lock:
        if fcntl is None:
            yield
            return
        state = config.paths().state
        state.mkdir(parents=True, exist_ok=True)
        with open_lock(state / "pairing.lock") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class PairingError(Exception):
    pass


class CertUnreadable(PairingError):
    """The certificate exists but this user may not read it (the CLI run without sudo)."""


class AmbiguousStation(PairingError):
    """Two Weigh Station pairings have told the Hub a certificate: the Hub has one `station.url`, so it cannot tell
    which pin belongs to it (it would call one station with the other's certificate)."""


class CodeSpent(PairingError):
    """The code was valid a short while ago: expired, redeemed, or dropped at the attempt limit."""


@dataclass
class Pairing:
    token: str
    code: str
    expires: float
    qr: str


def fingerprint(cert_pem: bytes) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    cert = x509.load_pem_x509_certificate(cert_pem)
    return cert.fingerprint(hashes.SHA256()).hex()


def give_to_state_owner(path) -> None:
    """Run as root (`sudo spoolwarden config`), a new state file would belong to root and the service user
    could not read it: hand it to the owner of the state directory instead. A no-op for everyone else."""
    if getattr(os, "geteuid", lambda: -1)() != 0:
        return
    try:
        st = os.stat(config.paths().state)
    except FileNotFoundError:
        return
    os.chown(path, st.st_uid, st.st_gid, follow_symlinks=False)      # never through a planted link


def make_state_dir(path) -> None:
    """`mkdir -p` for a directory in the state directory; each directory it creates is handed to the state
    directory's owner (as `give_to_state_owner` does for files), so a root-run CLI that creates
    `printers/<serial>/` first does not lock the service user out of it."""
    path = Path(path)
    missing = []
    p = path
    while not p.exists() and p.parent != p:
        missing.append(p)
        p = p.parent
    path.mkdir(parents=True, exist_ok=True)
    state = Path(config.paths().state)
    for d in reversed(missing):
        if state in d.parents:
            give_to_state_owner(d)


_REFUSED = (errno.EACCES, errno.EPERM, errno.EROFS)


class LockUnavailable(OSError):
    """A lock this process may not create, and that does not exist yet (made by the kit's install as root)."""


def open_lock(path: Path):
    """Open (creating if needed) a lock file in the state directory, owned like the state directory."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fh = os.fdopen(os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | nofollow, 0o644), "a")
    except OSError as e:
        if e.errno not in _REFUSED:
            raise
        # a lock this process may only read (the web unit's user on the kit's locks, or a read-only mount under
        # ProtectSystem=strict): flock works on a read-only descriptor all the same
        try:
            fh = os.fdopen(os.open(path, os.O_RDONLY | nofollow), "r")
        except FileNotFoundError:
            raise LockUnavailable(e.errno, f"the lock {path} is missing and this process may not create it; "
                                  "run `sudo spoolwarden modules apply` to make the shared locks", str(path)) from e
    try:
        give_to_state_owner(path)
    except BaseException:
        fh.close()
        raise
    return fh


def _write_atomic(path: Path, data: bytes, mode: int, state_owned: bool = False) -> None:
    """Write via a temp file and a rename. `state_owned`: a state file, handed to the state directory's owner
    before the rename when running as root."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
            # mode and owner on the open file, not by name: a link swapped in at `tmp` is never followed
            if hasattr(os, "fchmod"):
                os.fchmod(fh.fileno(), mode)
            else:
                os.chmod(tmp, mode)
            if state_owned and hasattr(os, "fchown") and getattr(os, "geteuid", lambda: -1)() == 0:
                st = os.stat(config.paths().state)
                os.fchown(fh.fileno(), st.st_uid, st.st_gid)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


_BRIDGE_IFACES = ("docker", "br-", "veth", "virbr", "cni", "flannel", "kube", "podman", "tun", "tap", "wsl", "vethernet")
_DOCKER_BRIDGE_NET = ipaddress.ip_network("172.17.0.0/16")


def _is_bridge_iface(name: str) -> bool:
    low = name.lower()
    return any(low.startswith(p) for p in _BRIDGE_IFACES)


def _is_docker_bridge_ip(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip) in _DOCKER_BRIDGE_NET
    except ValueError:
        return False


def _read_proc_net_route() -> list[tuple[str, int, int]]:
    """Read Linux `/proc/net/route` as (iface, dest_host_order, mask_host_order)."""
    routes: list[tuple[str, int, int]] = []
    try:
        text = Path("/proc/net/route").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return routes
    for line in text.splitlines()[1:]:
        cols = line.split()
        if len(cols) < 8:
            continue
        try:
            routes.append((cols[0], int(cols[1], 16), int(cols[7], 16)))
        except ValueError:
            continue
    return routes


def _iface_for_ip(ip: str, routes: list[tuple[str, int, int]]) -> str | None:
    try:
        addr = int.from_bytes(ipaddress.IPv4Address(ip).packed, sys.byteorder)
    except ValueError:
        return None
    best_iface: str | None = None
    best_bits = -1
    for iface, dest, mask in routes:
        if mask != 0 and (addr & mask) == (dest & mask):
            bits = mask.bit_count()
            if bits > best_bits:
                best_bits = bits
                best_iface = iface
    return best_iface


def _candidate_ips() -> list[str]:
    raw: list[str] = []
    if os.name != "nt":
        try:
            raw = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5).stdout.split()
        except (OSError, subprocess.SubprocessError):
            raw = []
    if not raw:
        try:
            raw = [i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
        except OSError:
            raw = []
    routes = _read_proc_net_route() if os.name != "nt" else []
    default_ifaces = {iface for iface, dest, mask in routes if dest == 0 and mask == 0}
    default_ips: list[str] = []
    other_ips: list[str] = []
    fallback: list[str] = []
    for ip in raw:
        if not _usable(ip):
            continue
        iface = _iface_for_ip(ip, routes) if routes else None
        if (iface and _is_bridge_iface(iface)) or (_is_docker_bridge_ip(ip) and iface not in default_ifaces):
            fallback.append(ip)
        elif iface and iface in default_ifaces:
            default_ips.append(ip)
        else:
            other_ips.append(ip)
    preferred = default_ips + other_ips
    return preferred if preferred else fallback


def _usable(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.version == 4 and not a.is_loopback and not a.is_link_local


def local_ips() -> list[str]:
    """This machine's IPv4 addresses a phone can reach: no loopback, no link-local (169.254.x.x)."""
    return list(dict.fromkeys(a for a in _candidate_ips() if _usable(a)))


def short_hostname() -> str:
    """The host's own name without any domain: what avahi publishes as <name>.local."""
    return socket.gethostname().split(".")[0]


def hub_id() -> str:
    """Stable random identifier for this Hub, persisted in state as XML (never an owner name or hostname)."""
    from .profiles import statexml
    with _locked():
        f = config.paths().state / "pairing_hub_id.xml"
        names = (f,)
        _refuse_links(names)
        data = statexml.read(f, tag="hub", mode=0o600, state_owned=True)
        _refuse_links(names)
        if isinstance(data, dict) and isinstance(data.get("id"), str) and data["id"]:
            return data["id"]
        hid = uuid.uuid4().hex
        config.paths().state.mkdir(parents=True, exist_ok=True)
        statexml.write(f, {"id": hid}, 0o600, tag="hub", state_owned=True)
        return hid


def make_cert(hostname: str, ips: list[str], dest: Path, extra_dns: list[str] = ()) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    dns = list(dict.fromkeys([hostname, f"{hostname}.local", "localhost", *extra_dns]))
    sans: list[x509.GeneralName] = [x509.DNSName(n) for n in dns]
    sans += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in dict.fromkeys(ips) if _usable(ip)]
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    _write_atomic(dest / "cert.pem", cert_pem, 0o644)
    _write_atomic(dest / "key.pem", key_pem, 0o600)
    return fingerprint(cert_pem)


def _old_json(obj: list) -> bytes:
    """The JSON file as v0.6.2 wrote it: indent 2, no newline after."""
    return json.dumps(obj, indent=2).encode("utf-8")


def _read(name: str) -> list:
    """A state list file (`devices.json`, `pending.json`, `spent.json`, `station-requests.json`), given by its JSON name:
    its XML form when there is one, else the JSON form (moved to XML on the way); a JSON that an older release
    changed after this code wrote it wins once."""
    from .profiles import statexml
    f = config.paths().state / name
    # never through a link: root (modules apply) reads these too, in a folder the service user owns
    names = (f, statexml.xml_path(f), f.with_name(f.name + ".sha256"))
    _refuse_links(names)
    out = statexml.read_list(f, mode=0o600, state_owned=True)
    _refuse_links(names)        # the helper reads through a no-follow descriptor, so a link swapped in while it read is
                                # not followed; a link seen now is an error here, not a quiet fall back to the JSON form
    return [] if out is None else out


def _refuse_links(paths) -> None:
    """OSError(ELOOP) when any of `paths` is a link. Opened with O_NOFOLLOW where the system has it (the open itself
    refuses a link: no check, then a read); the lstat is the fallback."""
    for p in paths:
        try:
            if getattr(os, "O_NOFOLLOW", 0):
                os.close(os.open(p, os.O_RDONLY | os.O_NOFOLLOW))
            elif stat.S_ISLNK(os.lstat(p).st_mode):
                raise OSError(errno.ELOOP, "a state file is a link", str(p))
        except FileNotFoundError:
            pass
        except OSError as e:
            if e.errno == errno.ELOOP:
                raise OSError(errno.ELOOP, "a state file is a link", str(p)) from None
            raise


def _write(name: str, obj: list) -> None:
    """Save the list as its XML form (0600, the state's owner), then as the JSON file an older release reads (the same
    shape as v0.6.2, a hash beside it): `update --rollback` keeps the pairings and the open codes. An older
    release's change to the JSON wins once on the next read."""
    from .profiles import statexml
    state = config.paths().state
    state.mkdir(parents=True, exist_ok=True)
    statexml.write_list(state / name, obj, 0o600, state_owned=True, old_format=_old_json)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _spent_read(now: float) -> list:
    """spent.json without entries past SPENT_KEEP. It holds the hash of a code, never the code."""
    return [e for e in _read("spent.json") if e["at"] > now - SPENT_KEEP]


def _spent_add(spent: list, codes: list, at: float) -> list:
    return spent + [{"hash": _hash(c), "at": at} for c in codes]


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _load_devices() -> list:
    """Read devices, purging QR tokens never claimed within their window. Caller holds _locked()."""
    now = time.time()
    devs = _read("devices.json")
    kept = [d for d in devs if d.get("claimed") is not None or d.get("expires", 0) > now]
    if len(kept) != len(devs):
        log.debug("pairing token expired: count=%d", len(devs) - len(kept))
        _write("devices.json", kept)
    return kept


def issue(device_hint: str = "", role: str | None = None, scope: str | None = None) -> Pairing:
    """A new pairing for a device named `device_hint`. `scope` ("station") limits its token to that scope's routes; it is
    given here, never read from the name. `role` marks what the device is for routes that serve only
    one kind of device ("web": the web unit's pairing, made by `modules apply` as root; see role_of)."""
    cert = config.paths().etc / "cert.pem"
    try:
        cert_pem = cert.read_bytes()
    except FileNotFoundError:
        raise PairingError(f"no server certificate at {cert}; create it first") from None
    except PermissionError:
        raise CertUnreadable(f"cannot read the server certificate {cert}: permission denied") from None
    fp = fingerprint(cert_pem)
    port = config.load()["port"]
    with _locked():
        now = time.time()
        token = _new_token()
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires = now + TOKEN_TTL
        device_id = uuid.uuid4().hex[:12]

        devs = _load_devices()
        devs.append({
            "id": device_id,
            "name": device_hint or "phone",
            "hash": _hash(token),
            "paired": None,
            "last_seen": None,
            "expires": expires,
            "claimed": None,
            # the intake station's token reaches only /api/station/* (api._authed); phones and the PC sync: all
            **({"scope": scope} if scope else {}),
            **({"role": role} if role else {}),
        })
        _write("devices.json", devs)

        pending = _read("pending.json")
        spent = _spent_add(_spent_read(now), [p["code"] for p in pending if p["expires"] <= now], now)
        pending = [p for p in pending if p["expires"] > now]
        _write("spent.json", spent)
        pending.append({"code": code, "device": device_id, "expires": expires, "attempts": 0})
        _write("pending.json", pending)

    log.debug("pairing issued: device_id=%s kind=%s", device_id, scope or role or "phone")
    # server_name is only a display name; the phone finds the server by mDNS hostname or one of its IPs
    query = urllib.parse.urlencode(
        {"h": f"{short_hostname()}.local", "p": port, "t": token, "fp": fp, "ip": local_ips()},
        doseq=True,
    )
    return Pairing(token=token, code=code, expires=expires, qr=f"fkit://pair?{query}")


def redeem_code(code: str) -> str:
    with _locked():
        now = time.time()
        allp = _read("pending.json")
        expired_codes = [p["code"] for p in allp if p["expires"] <= now]
        if expired_codes:
            log.debug("pairing code expired: count=%d", len(expired_codes))
        pending = [p for p in allp if p["expires"] > now]
        spent = _spent_add(_spent_read(now), expired_codes, now)
        well_formed = isinstance(code, str) and re.fullmatch(r"[0-9]{6}", code)
        match = None
        if well_formed:
            match = next((p for p in pending if hmac.compare_digest(p["code"], code)), None)
        if match is None:
            # A wrong guess counts against every live code; at the limit a code is dropped.
            for p in pending:
                p["attempts"] += 1
            dropped = [p["code"] for p in pending if p["attempts"] >= MAX_ATTEMPTS]
            if dropped:
                log.debug("pairing code lockout: count=%d", len(dropped))
            spent = _spent_add(spent, dropped, now)
            _write("pending.json", [p for p in pending if p["attempts"] < MAX_ATTEMPTS])
            _write("spent.json", spent)
            if well_formed and any(hmac.compare_digest(e["hash"], _hash(code)) for e in spent):
                log.debug("pairing code rejected: reason=spent")
                raise CodeSpent("pairing code expired or already used")
            log.debug("pairing code rejected: reason=invalid")
            raise PairingError("invalid, expired or locked pairing code")
        pending.remove(match)
        _write("pending.json", pending)
        _write("spent.json", _spent_add(spent, [match["code"]], now))
        devs = _load_devices()
        token = _new_token()
        for d in devs:
            if d["id"] == match["device"]:
                d["hash"] = _hash(token)
                d["paired"] = d["claimed"] = d["last_seen"] = now
                _write("devices.json", devs)
                kind = device_type(d) or d.get("role") or "phone"
                log.debug("pairing code verified: device_id=%s kind=%s", d["id"], kind)
                emit_args = ("device.paired", {"type": "device", "id": d["id"], "name": d.get("name", "")}, {"kind": kind})
                break
        else:
            log.debug("pairing code rejected: device_id=%s reason=device_missing", match["device"])
            raise PairingError("device for this code no longer exists")
    event_sink.emit(*emit_args)
    return token


CLIENT_KINDS = {"android": "phone", "slicer-sync": "pc", "weigh-station": "station", "web": "web",
                "home-assistant": "home-assistant", "phone": "phone", "pc": "pc", "station": "station"}


def parse_client_header(raw: str | None) -> tuple[str, str] | None:
    """Parse `X-Spoolwarden-Client: <kind>/<version>` into `(kind, version)` or None."""
    if not isinstance(raw, str) or "/" not in raw:
        return None
    raw_kind, _, raw_ver = raw.strip().partition("/")
    kind = CLIENT_KINDS.get(raw_kind.strip().lower())
    ver = raw_ver.strip()
    if kind is None or not (0 < len(ver) <= 40) or not ver.isprintable() or any(c.isspace() for c in ver):
        return None
    return kind, ver


def authorize(token: str, client: str | None = None) -> tuple[str, str | None] | None:
    """(device id, scope) for a live token, else None. Claims an unclaimed QR token (its first use). scope is
    "station" for a token from `pair --station`, "web" for the web unit's (from its role, so a pairing made before this
    needs nothing done), else None (every route but the web unit's own)."""
    h = _hash(token)
    parsed_client = parse_client_header(client)
    emit_args = None
    result = None
    with _locked():
        devs = _load_devices()
        for d in devs:
            if hmac.compare_digest(d["hash"], h):
                now = time.time()
                dirty = False
                if parsed_client is not None:
                    ckind, cver = parsed_client
                    if device_type(d) == "station":
                        ckind = "station"
                    if (d.get("kind"), d.get("version")) != (ckind, cver):
                        d["kind"], d["version"] = ckind, cver
                        dirty = True
                if d.get("claimed") is None:
                    d["claimed"] = d["paired"] = d["last_seen"] = now
                    allp = _read("pending.json")
                    pend = [p for p in allp if p["device"] != d["id"]]
                    _write("pending.json", pend)   # the QR claim uses the code up
                    _write("spent.json", _spent_add(_spent_read(now), [p["code"] for p in allp if p["device"] == d["id"]], now))
                    _write("devices.json", devs)
                    kind = device_type(d) or d.get("role") or "phone"
                    emit_args = ("device.paired", {"type": "device", "id": d["id"], "name": d.get("name", "")},
                                 {"kind": kind})
                elif d["last_seen"] is None or now - d["last_seen"] > LAST_SEEN_STALE:
                    d["last_seen"] = now
                    _write("devices.json", devs)
                elif dirty:
                    _write("devices.json", devs)
                # a device paired as "station" before scopes existed is scoped too
                if d.get("scope") or device_type(d) == "station":
                    result = (d["id"], d.get("scope") or "station")
                elif d.get("role") in (WEB_ROLE, VIEWER_ROLE, HA_CONTROL_ROLE):
                    result = (d["id"], d["role"])
                else:
                    result = (d["id"], None)
                log.debug("pairing token verified: device_id=%s kind=%s", result[0], result[1] or "phone")
                break
    if result is None:
        log.debug("pairing token rejected: reason=unknown_or_expired")
    if emit_args is not None:
        event_sink.emit(*emit_args)
    return result


def check(token: str) -> str | None:
    got = authorize(token)
    return got[0] if got else None


def revoke(device_id: str) -> bool:
    to_emit = []
    with _locked():
        devs = _load_devices()
        gone = [d for d in devs if d["id"] == device_id]
        kept = [d for d in devs if d["id"] != device_id]
        if len(kept) == len(devs):
            return False
        _write("devices.json", kept)
        for d in gone:
            if d.get("claimed") is not None:
                kind = device_type(d) or d.get("role") or "phone"
                to_emit.append(("device.removed", {"type": "device", "id": d["id"], "name": d.get("name", "")},
                                {"kind": kind}))
    for args in to_emit:
        event_sink.emit(*args)
    return True


WEB_ROLE = "web"     # the web unit's pairing: the kit API's web-only routes (GET /api/intake/orders) serve it
VIEWER_ROLE = "viewer"  # read-only integration pairing (Home Assistant)
HA_CONTROL_ROLE = "ha-control"  # opt-in Home Assistant integration pairing with printer/slot control


def role_of(device_id: str) -> str | None:
    """The `role` of a paired device's record (None: none set, or no such device)."""
    with _locked():
        for d in _load_devices():
            if d.get("id") == device_id:
                r = d.get("role")
                return r if isinstance(r, str) else None
    return None


def set_role_by_name(name: str, role: str) -> int:
    """Give `role` to every device named `name` that has none (the upgrade of pairings made before roles
    existed). Returns how many changed."""
    with _locked():
        devs = _load_devices()
        n = 0
        for d in devs:
            if d.get("name") == name and not d.get("role"):
                d["role"] = role
                n += 1
        if n:
            _write("devices.json", devs)
        return n


def device_type(d: dict) -> str | None:
    """What a pairing is for: "station" (a Weigh Station), "home-assistant" (viewer), else None. A pairing made
    before scopes existed has no scope, only the name `pair --station` gave it, so the name counts as well."""
    if d.get("scope") == "station" or d.get("name") == "station":
        return "station"
    if d.get("role") in (VIEWER_ROLE, HA_CONTROL_ROLE):
        return "home-assistant"
    return None


def devices() -> list[dict]:
    with _locked():
        return [
            {**{k: d[k] for k in ("id", "name", "paired", "last_seen")}, "type": device_type(d),
             **({"kind": d["kind"]} if d.get("kind") else {}),
             **({"version": d["version"]} if d.get("version") else {})}
            for d in _load_devices()
            if d.get("claimed") is not None
        ]


def record_station_hello(device_id: str, fp, port, version, plain=None) -> bool:
    """What a Weigh Station says on `GET /api/station/hello`: the SHA-256 of its certificate, its HTTPS
    port and its software version, kept with that station's pairing in devices.json (`fp`, `port`, `version`,
    `hello_at` on its record; the file is JSON today, so these keys join it as they are). False, nothing stored, for a
    device that is no station or a value that is not what it should be (the older station sends none: it stays on plain
    HTTP). Called by the hello route; the Hub then calls the station over HTTPS, accepting only this certificate.
    `plain`: a station that could not make its certificate sends no `fp` but this reason (`plain=<why>`) with its port
    and version: it is kept as `plain_reason` (the Checks row says so) and an fp the Hub already holds is NEVER
    cleared by it: a pinned station is not downgraded to plain HTTP."""
    fp = str(fp or "").strip().lower()
    version = str(version or "").strip()
    reason = " ".join(str(plain or "").split())[:120]
    if not fp and reason:
        return _record_plain(device_id, port, version, reason)
    try:
        port = int(str(port).strip())
    except ValueError:
        return False
    if not re.fullmatch(r"[0-9a-f]{64}", fp) or not 1 <= port <= 65535 or len(version) > 40 or not version.isprintable():
        return False
    with _locked():
        devs = _load_devices()
        for d in devs:
            if d.get("id") == device_id and device_type(d) == "station":
                if (d.get("fp"), d.get("port"), d.get("version")) != (fp, port, version):
                    d.update(fp=fp, port=port, version=version)
                d.pop("plain_reason", None)
                d["hello_at"] = time.time()
                _write("devices.json", devs)
                return True
    return False


def _record_plain(device_id: str, port, version: str, reason: str) -> bool:
    try:
        port = int(str(port).strip())
    except ValueError:
        return False
    if not 1 <= port <= 65535 or len(version) > 40 or not version.isprintable():
        return False
    with _locked():
        devs = _load_devices()
        for d in devs:
            if d.get("id") == device_id and device_type(d) == "station":
                d.update(version=version, plain_reason=reason, hello_at=time.time())
                if not d.get("fp"):
                    d["port"] = port        # the plain port; a pinned station keeps its pinned HTTPS port
                _write("devices.json", devs)
                return True
    return False


def station_plain_reason() -> str | None:
    """Why the Weigh Station that said hello last has no certificate (`plain` of its hello), else None."""
    with _locked():
        found = [d for d in _load_devices() if d.get("claimed") is not None and device_type(d) == "station" and d.get("plain_reason")]
    return max(found, key=lambda x: x.get("hello_at") or 0)["plain_reason"] if found else None


def station_info() -> dict | None:
    """`{"fp", "port", "version", "hello_at"}` for the Weigh Station pairing that said hello with a certificate, else
    None (no station paired, or only an older one that sends no fingerprint: plain HTTP). Raises AmbiguousStation when
    two pairings have one: the Hub has a single station address (unpair the one that is gone)."""
    with _locked():
        found = [d for d in _load_devices() if d.get("claimed") is not None and device_type(d) == "station" and d.get("fp")]
    if not found:
        return None
    if len(found) > 1:
        raise AmbiguousStation("two Weigh Stations are paired and the Hub has one station address: unpair the one "
                               "that is gone (Settings > Devices)")
    d = found[0]
    return {"fp": d["fp"], "port": int(d["port"]), "version": d.get("version") or "", "hello_at": d.get("hello_at")}

