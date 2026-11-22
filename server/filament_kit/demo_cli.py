"""`spoolwarden demo [status]`, `sudo spoolwarden demo on|off`, and the line "Demo data." every result has.

    add_parsers(sub)     the command for cli.py's parser (RC9 mounts it)
    run_demo(args)       the command; the exit codes of the design, command line
    announce()           "Demo data." above a result while the demo is on: standard output, also with `--quiet`;
                         with `--json` standard error, so that standard output stays the one document

The texts are plain constants until RC9 puts them in the catalogue (keys in the comments).
"""
from __future__ import annotations

from . import config, demo, output

LINE = "Demo data."                                            # demo.cli_line
ON = "The demo is on."                                         # demo.on
OFF = "The demo is off."                                       # demo.off
ON_HINT = "Look around on the Hub page: {url}"                 # demo.on_hint
LEAVE_HINT = "Leave it with: sudo spoolwarden demo off"        # demo.leave_hint
LEAVE_QUESTION = "Leave the demo? Everything in it is deleted."   # demo.leave_question
NOT_ON = "The demo is not on."                                 # demo.not_on


def add_parsers(sub) -> None:
    d = sub.add_parser("demo", help="the demo: made-up data to look around in; on and off need root")
    ds = d.add_subparsers(dest="demo_cmd")
    ds.add_parser("status", help="say whether the demo is on (also what demo alone does)")
    ds.add_parser("on", help="start the demo on an empty Hub (root)")
    off = ds.add_parser("off", help="leave the demo and delete what is in it; asks first (root)")
    off.add_argument("-y", "--yes", action="store_true", help="do not ask")


def announce() -> None:
    """The line "Demo data." for a command that prints a result; nothing while the demo is off."""
    if not demo.is_on():
        return
    if output.json_mode():
        output.err(LINE)
        return
    with output.shown():
        output.out(LINE)


def _need_root(word: str) -> int | None:
    from . import cli
    if not cli._no_systemd() and not cli._is_root():
        return output.need_sudo(cli._sudo_command(f"sudo spoolwarden demo {word}"))
    return None


def run_demo(args) -> int:
    which = getattr(args, "demo_cmd", None) or "status"
    if which == "on":
        return cmd_on()
    if which == "off":
        return cmd_off(bool(getattr(args, "yes", False)))
    return cmd_status()


def cmd_status() -> int:
    on = demo.is_on()
    output.emit({"demo": on})
    output.out(ON if on else OFF)
    return output.SUCCESS


def cmd_on() -> int:
    if (rc := _need_root("on")) is not None:
        return rc
    try:
        demo.start()
    except demo.DemoError as e:
        output.error(e.message)
        return output.FAILED
    from . import setup_code
    output.emit({"demo": True, "ok": True})
    output.result(ON + " " + ON_HINT.format(url=setup_code.page_url(config.load())))
    output.result(LEAVE_HINT)
    return output.SUCCESS


def cmd_off(yes: bool) -> int:
    if (rc := _need_root("off")) is not None:
        return rc
    if not demo.is_on():
        output.error(NOT_ON)
        return output.FAILED
    if (rc := output.confirm(LEAVE_QUESTION, yes=yes, doing="leave the demo", rerun="sudo spoolwarden demo off -y")) \
            is not None:
        return rc
    demo.leave()
    output.emit({"demo": False, "ok": True})
    output.result(OFF)
    return output.SUCCESS
