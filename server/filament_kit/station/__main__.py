"""`python -m filament_kit.station`: start the panel, reader and scale threads and serve the read-only view over HTTPS
on STATION_PORT (default 5003) for the addresses in STATION_ALLOW (space-separated, default 127.0.0.1). The certificate
is the station's own (state.ensure_cert); after each start, and every 5 minutes after, it tells the Hub its fingerprint, port and
version. It also serves its own page and settings (page.py, settings.py)."""
from __future__ import annotations

import logging
import os
import signal
import socket
import sys
import threading
from http.server import ThreadingHTTPServer

from . import beep, camera, flow, http, nfc, page, panel, scale, server_api, settings, speech
from . import state as st

log = logging.getLogger("intake-station")


def stop_service(signum, frame):
    """SIGTERM/SIGINT: let the reader loop close nfcpy first (at most 3 s), so the PN532 is left at 115200 and idle;
    otherwise a restart finds it at 921600 and times out until it is unplugged."""
    nfc.stop()
    sys.exit(0)


class _DualStackServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


class _DualStackTlsServer(http.TlsServer, _DualStackServer):
    pass


_ORIG_TLS_SERVER = http.TlsServer


def make_server(port: int, handler):
    """(listener, certificate fingerprint or None, why there is none or None): HTTPS with the station's own certificate.
    Functionality first: a station that cannot make its certificate still serves, over plain HTTP as before. It says
    so in the journal and in its hello (`plain=<why>`: the Hub's Checks row); a Hub that pinned it never follows it
    down to plain HTTP."""
    dual_stack = socket.has_dualstack_ipv6()
    address = ("::" if dual_stack else "0.0.0.0", port)
    try:
        cert, key, fp = st.ensure_cert()
        context = http.tls_context(cert, key)
    except (OSError, ValueError, ImportError) as e:
        plain = f"{type(e).__name__}: {e}"
        log.error("no HTTPS certificate for the station (%s): serving plain HTTP", plain)
        server = _DualStackServer if dual_stack else ThreadingHTTPServer
        return server(address, handler), None, plain
    log.info("https: port %s, certificate %s", port, fp)
    server = _DualStackTlsServer if (dual_stack and http.TlsServer is _ORIG_TLS_SERVER) else http.TlsServer
    return server(address, handler, context), fp, None


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    s = st.settings
    log.info("scan beep: %s", (s["beep_device"] or beep.beep_device() or "no speaker output found")
             if s["beep"] else "off")
    log.info("camera: %s", camera.camera_dev() or "none found (looked for again at each new spool)")
    log.info("kit server: %s", "paired" if flow.api().paired else "not paired (sudo spoolwarden station pair)")
    store = settings.Store(after=settings.after_change)     # the station's own values over the installed ones (settings.xml)
    if store.problem:
        log.warning("%s", store.problem)
    access = page.Access(server_api.proof_key)
    handler = http.make_handler(st.view, s["allow"], access, store, page_ranges=os.environ.get("STATION_PAGE_RANGES"))
    server, fp, plain = make_server(s["port"], handler)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop_service)
    for fn in (panel.panel_loop, nfc.nfc_loop, scale.scale_loop, speech.speech_loop, flow.tick_loop):
        threading.Thread(target=fn, daemon=True).start()
    if not flow.api().paired:       # ask the kit server once; a long press of yellow asks again
        flow.ask_to_pair()
    threading.Thread(target=server_api.announce, args=(flow.api, fp, s["port"]), kwargs={"plain": plain},
                     daemon=True).start()
    threading.Thread(target=server_api.checkin, args=(flow.api, fp, s["port"]), kwargs={"plain": plain},
                     daemon=True).start()        # the Hub's "last seen" for this station: every 5 minutes
    server.serve_forever()


if __name__ == "__main__":
    main()
