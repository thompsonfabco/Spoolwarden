"""AUT-5 runner and page driver end only their own browser (tests/aut5_browser_end.mjs endOwnBrowser): the tree kill runs
while the browser is still running and not after it has exited. The driver runs the real function on real child processes."""
import shutil
import subprocess
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent


def _node():
    node = shutil.which("node")
    if not node:
        return None
    major = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip().lstrip("v").split(".")[0]
    return node if major.isdigit() and int(major) >= 22 else None


@pytest.mark.skipif(not _node(), reason="needs Node 22 or later")
def test_cleanup_ends_only_a_running_browser():
    r = subprocess.run([_node(), str(TESTS / "aut5_browser_end_check.mjs")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok ") == 3, r.stdout


@pytest.mark.skipif(not _node(), reason="needs Node 22 or later")
@pytest.mark.parametrize("mode,want", [("hang_list", "waiting for /json/list"), ("hang_ws", "waiting for WebSocket open")])
def test_page_drive_bounds_dead_browser_endpoint(tmp_path, mode, want):
    import json
    import socket
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    held = []
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    if mode == "hang_list":
        port = srv.getsockname()[1]
        threading.Thread(target=lambda: held.append(srv.accept()[0]), daemon=True).start()
    else:
        ws_port = srv.getsockname()[1]
        threading.Thread(target=lambda: held.append(srv.accept()[0]), daemon=True).start()

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps([{"type": "page", "webSocketDebuggerUrl": f"ws://127.0.0.1:{ws_port}/p"}]).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        hs = ThreadingHTTPServer(("127.0.0.1", 0), H)
        port = hs.server_port
        threading.Thread(target=hs.serve_forever, daemon=True).start()

    script = tmp_path / "noop.js"
    script.write_text("return 1;\n", encoding="utf-8")
    t0 = time.monotonic()
    try:
        r = subprocess.run([_node(), str(TESTS / "page_drive.mjs"), "--url", "http://127.0.0.1:1/",
                            "--script", str(script), "--devtools-port", str(port), "--wait-timeout", "350"],
                           capture_output=True, text=True, timeout=15)
        assert time.monotonic() - t0 < 5.0
        assert r.returncode == 2 and want in r.stderr, r.stdout + r.stderr
    finally:
        for s in held:
            try:
                s.close()
            except OSError:
                pass
        srv.close()
        if mode != "hang_list":
            hs.shutdown()
            hs.server_close()
