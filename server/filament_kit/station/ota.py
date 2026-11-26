"""Remote OTA update and restart control for the Weigh Station."""
from __future__ import annotations

import inspect
import logging
import subprocess
import threading
import time

from . import state as st

log = logging.getLogger("intake-station")

_lock = threading.Lock()
_status: dict = {
    "state": "idle",
    "target_version": None,
    "started_at": None,
    "finished_at": None,
    "error": None,
    "last_result": None,
}


def status() -> dict:
    """Thread-safe snapshot of the station's OTA state."""
    with _lock:
        return dict(_status)


def _invoke_runner(runner, target_version: str | None) -> None:
    try:
        sig = inspect.signature(runner)
        params = [p for p in sig.parameters.values()
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.VAR_POSITIONAL)]
    except (ValueError, TypeError):
        params = []
    if params:
        runner(target_version)
    else:
        runner()


def _default_update_runner(target_version: str | None) -> None:
    from .. import config, updates
    fn = getattr(updates, "apply_update", None)
    if callable(fn):
        fn(target_version)
        subprocess.run(["systemctl", "restart", "filament-kit-station"], check=True)
        return
    rc = updates.update(config.load())
    if rc != 0:
        raise RuntimeError(f"update exited with code {rc}")


def _default_restart_runner() -> None:
    subprocess.run(["systemctl", "restart", "filament-kit-station"], check=True)


def trigger_update(target_version: str | None = None, runner=None) -> tuple[int, dict]:
    """Start a background OTA update unless disabled or already running."""
    if not st.settings.get("auto_ota", True):
        return 409, {
            "error": "auto_ota_disabled",
            "message": "Remote OTA updates are disabled on this Weigh Station.",
        }
    ver = str(target_version).strip()[:64] if isinstance(target_version, str) and target_version.strip() else None
    with _lock:
        if _status["state"] == "updating":
            return 409, {
                "error": "ota_in_progress",
                "message": "An update is already running.",
            }
        _status["state"] = "updating"
        _status["target_version"] = ver
        _status["started_at"] = time.time()
        _status["finished_at"] = None
        _status["error"] = None
        snapshot = dict(_status)

    def _run() -> None:
        try:
            if runner is not None:
                _invoke_runner(runner, ver)
            else:
                _default_update_runner(ver)
        except Exception as e:  # noqa: BLE001
            log.exception("station ota update failed")
            with _lock:
                _status["state"] = "error"
                _status["finished_at"] = time.time()
                _status["error"] = str(e)
                _status["last_result"] = "error"
        else:
            with _lock:
                _status["state"] = "idle"
                _status["finished_at"] = time.time()
                _status["error"] = None
                _status["last_result"] = "ok"

    threading.Thread(target=_run, name="station-ota-update", daemon=True).start()
    return 200, {"ok": True, **snapshot}


def trigger_restart(runner=None) -> tuple[int, dict]:
    """Restart the Weigh Station service in a background daemon thread."""
    with _lock:
        if _status["state"] == "updating":
            return 409, {
                "error": "ota_in_progress",
                "message": "An update is already running.",
            }

    def _run() -> None:
        try:
            if runner is not None:
                runner()
            else:
                _default_restart_runner()
        except Exception:  # noqa: BLE001
            log.exception("station restart failed")

    threading.Thread(target=_run, name="station-ota-restart", daemon=True).start()
    return 200, {"ok": True, "restarting": True}
