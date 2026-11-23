"""The command line of the address and first-run phase: `port` and the Getting started block of
`status`. Its texts are in address_texts.MESSAGES (the catalogue); cli.py hooks the parser in and runs it.

    port                 the Hub's port and its address; no sudo
    port <number>        move the Hub to another port (hubport.change_port); asks first, `--yes` answers it
"""
from __future__ import annotations

import sys

from . import config, hubport, output, pairing
from .address_texts import COMMAND, say


def hub_address(cfg: dict | None = None, path: str = "/setup") -> str:
    """The Hub's address as a person types it: no port with 443 (`https://192.0.2.10/setup`), else `:8710`."""
    cfg = cfg if cfg is not None else config.load()
    ips = pairing.local_ips()
    host = ips[0] if ips else f"{pairing.short_hostname()}.local"
    return f"https://{host}{hubport.url_port(cfg.get('port', hubport.FALLBACK_PORT))}{path}"


def add_parser(sub) -> None:
    p = sub.add_parser("port", help="the port the Hub listens on, or move the Hub to another")
    p.add_argument("number", nargs="?", help="the new port: 443, or 1024 to 65535")
    p.add_argument("-y", "--yes", action="store_true", help="do not ask")


def cmd_port(number: str | None, yes: bool = False) -> int:
    """The design, command line. Reading needs no sudo. A change is refused before anything is asked or changed when the port is not
    allowed or is taken (exit 2, with the Fix for a taken port), asks the dialog's words, then runs
    `hubport.change_port` (config, drop-in, advert, station drop-in, restarts, the firewall line)."""
    from . import cli
    cfg = config.load()
    port = int(cfg.get("port", hubport.FALLBACK_PORT))
    if number is None:
        address = hub_address(cfg)
        output.emit({"port": port, "address": address})
        output.out(say("port_show", port=port))
        output.out(say("port_show_address", address=address))
        return 0
    if not cli._is_root():
        return output.need_sudo(cli._sudo_command(f"sudo {COMMAND} port <number>"))
    n, why = hubport.check_port(number, current=port, allow_443=True)
    if n is None:
        taken = why.endswith("in use on this machine.") or "could not be checked" in why
        output.error(why, say("port_fix_other") if taken else None)
        return output.USAGE
    stop = cli._confirm(f"{say('port_dialog_title', port=n)} {say('port_dialog_body')}", f"moving the Hub to port {n}",
                        yes=yes)
    if stop is not None:
        return stop
    try:
        with output.wait(say("port_moving", port=n)):
            hubport.change_port(str(n))
    except hubport.PortError as e:
        output.error(str(e), e.fix or None)
        return output.USAGE
    except (OSError, RuntimeError) as e:
        output.error(f"the Hub could not be moved: {e}")
        return 1
    address = hub_address(config.load())
    output.emit({"ok": True, "port": n, "address": address})
    output.out_lines(output.status_row("done", say("port_done", port=n, address=address)))
    return 0


# ---------- the Getting started block of `status` ----------

_WORD = {"done": ("cli_start_done", "good"), "next": ("cli_start_next", "note"), "open": ("cli_start_not_yet", "off")}


def _row(kind: str, text: str) -> list[str]:
    """One step: two spaces, the glyph, the word padded to 12, two spaces, the name (as status_row does; the words
    `next` and `not yet` are not in output.py's vocabulary, so the glyph is chosen here)."""
    key, meaning = _WORD[kind]
    word = say(key)
    glyph = output.glyph(meaning)
    if output.plain():
        start = len(word) + 2
        body = output.wrap(text, indent=start, first=start)
        return [word + "  " + body[0][start:]] + body[1:]
    shown = f"{glyph} {word}"
    colored = output.paint(shown, output._GLYPHS[meaning][2], sys.stdout)
    start = max(2 + 2 + output.STATE_WORD_WIDTH + 2, 2 + len(shown) + 2)
    body = output.wrap(text, indent=start, first=start)
    return ["  " + colored + " " * (start - 2 - len(shown)) + body[0][start:]] + body[1:]


def started_lines(view: dict | None = None) -> list[str]:
    """The block: `Getting started: 4 of 8 done`, one line per step with its word, under the `next` one the command that
    does it, and (while no step is done) the quiet Restore line. [] when the list is hidden or finished, or has no step
    left (all done: the page says "All set" once; the command line says nothing)."""
    from . import getting_started
    view = view if view is not None else getting_started.view()
    if not view["show"] or view["done"] >= view["total"]:
        return []
    lines = [say("start_line", done=view["done"], total=view["total"])]
    if view["restore_offer"]:
        lines.append("  " + say("cli_start_restore"))
    printer = _first_printer() if any(s["id"] == "slots" and s["state"] == "next" for s in view["steps"]) else ""
    for s in view["steps"]:
        lines += _row(s["state"], say(f"start_step_{s['id']}"))
        if s["state"] == "next":
            lines += output.status_row("", output.fill(getting_started.COMMANDS[s["id"]], command=COMMAND, printer=printer))
    return lines


def _first_printer() -> str:
    from .bridge import registry
    return next((registry.display_name(p) for p in registry.load().values() if p.configured), "")


# ---------- after switching features ----------

def reader_next(before, after) -> str | None:
    """The install command `Next:` names when Label Reader: On this Hub was just switched on and the reader is not
    installed (here: the machine's own address, nothing installed), else None."""
    from . import getting_started
    if "local_intake" not in after or "local_intake" in before:
        return None
    if not getting_started.reader_install_pending(config.load()):
        return None
    return output.fill(getting_started.COMMANDS["reader"], command=COMMAND)


def after_features(before, after, changed: bool) -> None:
    """A feature was switched: the Getting started mark `features` (the page reads it), and the `Next:` line of the
    Label Reader. Never raises: a first-run file that cannot be written changes nothing else."""
    from . import firstrun
    if changed:
        firstrun.mark("features")
    step = reader_next(before, after)
    if step:
        output.out(say("cli_next", step=step))
