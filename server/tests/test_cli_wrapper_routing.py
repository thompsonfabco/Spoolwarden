"""install/spoolwarden-cli.sh: which commands run as root and which as the service user (FA-105).

A copy of the wrapper runs under bash with `id` saying root, and stub `runuser` and program that print what they were
given, so each command's route is read off the output.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "install" / "spoolwarden-cli.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None or os.name == "nt", reason="the wrapper is a bash script for the Hub")

ROOT_COMMANDS = [
    ["config", "show"], ["cert", "status"], ["modules", "apply"], ["modules", "set", "full"], ["update"],
    ["port", "8711", "--yes"], ["port", "-y", "8711"], ["port", "invalid"], ["port", "-1"], ["port", " 8711 ", "--yes"], ["uninstall"], ["station", "pair"], ["setup-code"], ["backup", "create"], ["restore", "x.zip"],
    ["web", "passphrase"], ["reader", "token", "a"], ["reader", "revoke", "a"], ["reader", "setup"],
    ["local-intake", "install", "--yes", "--planned"], ["local-intake", "remove", "--yes"],
    ["local-intake", "token"], ["local-intake", "plan"], ["local-intake", "share-corrections", "on"],
    ["label-reader", "corrections"], ["label-reader", "corrections", "send"],
    ["certificate"], ["logs"], ["logs", "filament-kit-web", "--lines", "50"], ["features", "on", "web"],
    ["weigh-station", "pair"], ["spoolman", "address", "http://spoolman.example:7912"], ["catalog", "token"],
    ["catalog", "token", "--remove"], ["backup", "keep", "21"], ["remote", "get-certificate"], ["config", "set", "model", "x"],
    ["locations", "add", "x"], ["locations", "edit", "x", "--limit", "3"], ["locations", "move", "x", "--first"],
    ["locations", "remove", "x"], ["locations", "default-overflow", "x"], ["locations", "sealed", "x"],
]
USER_COMMANDS = [
    ["local-intake", "status"], ["local-intake", "test", "a.jpg"], ["web", "status"], ["reader", "tokens"],
    ["port"], ["port", "--yes"], ["port", "--help"], ["printer", "list"], ["recent"], ["label-reader", "status"], ["label-reader", "test", "a.jpg"], ["catalog", "status"],
    ["catalog", "update"], ["spoolman", "fields"], ["spoolman", "address"], ["add"], ["spool"], ["help", "aliases"],
    ["locations"], ["locations", "list"], ["locations", "--json"],
]


def _route(tmp_path, args, station=False, result=False, stdin=False):
    stubs = tmp_path / "bin"
    stubs.mkdir(exist_ok=True)
    runuser = 'shift 3; echo "AS-USER $*"' + ('; echo "STDIN:$(cat)"' if stdin else "")
    for name, body in (("id", 'echo 0'), ("runuser", runuser),
                       ("systemctl", "exit 1"), ("pgrep", "exit 1"), ("fk", 'echo "AS-ROOT $*"')):
        p = stubs / name
        p.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8", newline="\n")
        p.chmod(0o755)
    text = WRAPPER.read_text(encoding="utf-8")
    text = text.replace("BIN=/opt/filament-kit/venv/bin/filament-kit", f"BIN={stubs / 'fk'}")
    if station:
        (tmp_path / "station").mkdir(exist_ok=True)
    text = text.replace("STATION_DIR=/etc/filament-kit-station",
                        f"STATION_DIR={tmp_path / ('station' if station else 'no-station')}")
    text = text.replace("KIT_OPT=/opt/filament-kit", f"KIT_OPT={tmp_path / 'no-opt'}")
    script = tmp_path / "wrapper.sh"
    script.write_text(text, encoding="utf-8", newline="\n")
    env = {**os.environ, "PATH": f"{stubs}:{os.environ.get('PATH', '')}"}
    r = subprocess.run([BASH, str(script), *args], capture_output=True, text=True, env=env, timeout=30,
                       **({"stdin": subprocess.DEVNULL} if stdin else {}))
    return r if result else r.stdout.strip()


def test_the_wrapper_is_patched_where_the_test_expects():
    text = WRAPPER.read_text(encoding="utf-8")
    for line in ("BIN=/opt/filament-kit/venv/bin/filament-kit", "STATION_DIR=/etc/filament-kit-station",
                 "KIT_OPT=/opt/filament-kit"):
        assert line in text, line


@pytest.mark.parametrize("args", ROOT_COMMANDS, ids=" ".join)
def test_root_commands_run_as_root(tmp_path, args):
    assert _route(tmp_path, args) == "AS-ROOT " + " ".join(args)


@pytest.mark.parametrize("args", USER_COMMANDS, ids=" ".join)
def test_other_commands_run_as_the_service_user(tmp_path, args):
    assert _route(tmp_path, args).startswith("AS-USER ")


SWITCHED = [["--plain"], ["--no-progress"], ["--color", "never"], ["--color=always"],
            ["--plain", "--color", "never", "--no-progress"], ["--json"], ["--quiet"], ["-q"], ["--json", "-q"]]


@pytest.mark.parametrize("switches", SWITCHED, ids=" ".join)
@pytest.mark.parametrize("args", [["backup", "create"], ["restore", "x.zip"], ["config", "show"], ["web", "passphrase"],
                                  ["reader", "token", "a"]], ids=" ".join)
def test_switches_before_a_root_command_do_not_change_who_runs_it(tmp_path, switches, args):
    assert _route(tmp_path, [*switches, *args]) == "AS-ROOT " + " ".join([*switches, *args])


@pytest.mark.parametrize("switches", SWITCHED, ids=" ".join)
def test_switches_before_a_user_command_stay_in_front_of_it(tmp_path, switches):
    got = _route(tmp_path, [*switches, "web", "status"])
    assert got.startswith("AS-USER ") and got.endswith(" ".join([*switches, "web", "status"]))


def test_status_keeps_its_station_pairing_answer_after_switches(tmp_path):
    got = _route(tmp_path, ["--plain", "status"], station=True)
    assert got.startswith("AS-USER ") and " --plain status --station-pairing=" in got


@pytest.mark.parametrize("old", ["catalog", "library"])
def test_catalog_add_of_a_file_that_cannot_be_read_is_exit_2_with_the_fix(tmp_path, old):
    r = _route(tmp_path, [old, "add", str(tmp_path / "none.json")], result=True)
    assert r.returncode == 2 and r.stdout == ""
    assert r.stderr.startswith("Error: there is no readable file at ") and r.stderr.endswith("none.json.\n" + (
        "Fix: give the path of one catalog entry as a JSON file.\n"))


@pytest.mark.parametrize("old", ["catalog", "library"])
def test_catalog_add_of_a_readable_file_is_run_by_the_user_on_standard_input(tmp_path, old):
    entry = tmp_path / "entry.json"
    entry.write_text("{}", encoding="utf-8")
    got = _route(tmp_path, [old, "add", str(entry)])
    assert got.startswith("AS-USER ") and got.endswith(f" {old} add -")


def _file_case(tmp_path, old, args, name="entry.json"):
    """Run `<old> add <args>` with `{f}` replaced by a readable entry file; (the program's argument line, what it read on stdin)."""
    entry = tmp_path / name
    entry.write_text('{"x": 1}', encoding="utf-8")
    r = _route(tmp_path, [old, "add", *[str(entry) if a == "{f}" else a for a in args]], result=True, stdin=True)
    return r, r.stdout.splitlines()


@pytest.mark.parametrize("old", ["catalog", "library"])
@pytest.mark.parametrize("args, shown", [
    (["{f}"], "add -"), (["-y", "{f}"], "add -y -"), (["{f}", "-y"], "add -y -"), (["--yes", "{f}"], "add --yes -")],
    ids=lambda v: " ".join(v) if isinstance(v, list) else v)
def test_catalog_add_with_a_file_takes_the_file_branch_with_options_before_or_after(tmp_path, old, args, shown):
    """The file is the first word after `add` that does not start with `-`: root opens it, the program reads it on stdin."""
    r, lines = _file_case(tmp_path, old, args)
    assert r.returncode == 0 and r.stderr == ""
    assert lines[0].startswith("AS-USER ") and lines[0].endswith(f" {old} {shown}")
    assert lines[1] == 'STDIN:{"x": 1}'


@pytest.mark.parametrize("old", ["catalog", "library"])
def test_catalog_add_with_a_file_name_with_spaces_keeps_it_whole(tmp_path, old):
    r, lines = _file_case(tmp_path, old, ["-y", "{f}"], name="my entry (1).json")
    assert r.returncode == 0 and lines[0].endswith(f" {old} add -y -") and lines[1] == 'STDIN:{"x": 1}'


@pytest.mark.parametrize("old", ["catalog", "library"])
@pytest.mark.parametrize("rest", [["--ask"], ["--ask", "-y"], ["-y", "--ask"], ["-y"], ["-"]], ids=" ".join)
def test_catalog_add_with_no_file_word_takes_the_service_path_as_it_is(tmp_path, old, rest):
    """`--ask` (and `-y`, `-`) are not files: the program gets them untouched and nothing is read from a file."""
    r = _route(tmp_path, [old, "add", *rest], result=True, stdin=True)
    assert r.returncode == 0 and r.stderr == ""
    lines = r.stdout.splitlines()
    assert lines[0].startswith("AS-USER ") and lines[0].endswith(" ".join([old, "add", *rest])) and lines[1] == "STDIN:"


@pytest.mark.parametrize("old", ["catalog", "library"])
@pytest.mark.parametrize("args", [["-y", "none.json"], ["none.json", "-y"]], ids=" ".join)
def test_catalog_add_of_a_missing_file_is_exit_2_with_options_too(tmp_path, old, args):
    r = _route(tmp_path, [old, "add", *[str(tmp_path / a) if a.endswith(".json") else a for a in args]], result=True)
    assert r.returncode == 2 and r.stdout == ""
    assert r.stderr.startswith("Error: there is no readable file at ") and r.stderr.endswith("none.json.\nFix: give the path "
                                                                                           "of one catalog entry as a JSON file.\n")
