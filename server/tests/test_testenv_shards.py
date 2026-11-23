"""CI runs the server suite in shards (tools/testenv.sh --shard): no test file may fall between them, and the workflow may not drop one."""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TESTS = REPO / "server/tests"
sys.path.insert(0, str(REPO / "tools/testenv_plugin"))
import fk_shard  # noqa: E402

FLOW = (REPO / ".github/workflows/server.yml").read_text(encoding="utf-8")


def _jobs():
    """The workflow's jobs as {id: text}, from the lines that start a job (two spaces, an id, a colon)."""
    body = FLOW.split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([\w-]+):[ \t]*$", body, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


def _flow_shards():
    """(N, matrix entries) from the shard job: the N its command passes, and the numbers its matrix lists."""
    jobs = _jobs()
    ns, matrices = set(), []
    for text in jobs.values():
        for m in re.finditer(r"testenv\.sh --shard \$\{\{ matrix\.shard \}\}/(\d+)\s*$", text, re.M):
            ns.add(int(m.group(1)))
            matrix = re.search(r"matrix:\s*\n\s*shard:\s*\[([^\]]*)\]", text)
            assert matrix, "a job runs --shard ${{ matrix.shard }}/N without a matrix of shard numbers"
            matrices.append([int(x) for x in matrix.group(1).split(",")])
    assert len(ns) == 1 and len(matrices) == 1, (ns, matrices)
    return ns.pop(), matrices[0]


def _recipe_runs():
    """Every `bash tools/testenv.sh ...` the workflow runs, with the job that runs it."""
    return [(job, m.group(1).strip()) for job, text in _jobs().items()
            for m in re.finditer(r"^\s*run: bash tools/testenv\.sh(.*)$", text, re.M)]


def test_the_shards_are_the_whole_set_with_no_file_twice():
    n, _ = _flow_shards()
    whole = fk_shard.all_files(TESTS)
    assert len(whole) > 200, "the glob found almost nothing: is this the repo's server/tests?"
    taken = [f for i in range(1, n + 1) for f in fk_shard.shard(TESTS, i, n)]
    assert sorted(taken) == whole, (set(whole) - set(taken), sorted({f for f in taken if taken.count(f) > 1}))
    assert all(fk_shard.shard(TESTS, i, n) for i in range(1, n + 1)), "an empty shard is a job that proves nothing"


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 7])
def test_any_shard_count_covers_every_file_once(n):
    shards = fk_shard.shards(TESTS, n)
    assert len(shards) == n
    assert sorted(f for s in shards for f in s) == fk_shard.all_files(TESTS)


def test_a_new_test_file_is_taken_by_exactly_one_shard(tmp_path):
    for name in ("test_a.py", "test_b.py", "test_c.py"):
        (tmp_path / name).write_text("def test_x():\n    pass\n" * 3)
    before = fk_shard.shards(tmp_path, 2)
    (tmp_path / "test_new.py").write_text("def test_y():\n    pass\n")
    after = fk_shard.shards(tmp_path, 2)
    assert sorted(f for s in before for f in s) == ["test_a.py", "test_b.py", "test_c.py"]
    assert sorted(f for s in after for f in s) == ["test_a.py", "test_b.py", "test_c.py", "test_new.py"]


def test_the_shards_do_not_depend_on_the_line_endings(tmp_path):
    lf, crlf = tmp_path / "lf", tmp_path / "crlf"
    lf.mkdir()
    crlf.mkdir()
    for i in range(9):
        text = "def test_x():\n    pass\n" * (i + 1)
        (lf / f"test_{i}.py").write_bytes(text.encode())
        (crlf / f"test_{i}.py").write_bytes(text.replace("\n", "\r\n").encode())
    assert fk_shard.shards(lf, 3) == fk_shard.shards(crlf, 3)


def test_the_audit_files_weigh_nothing_in_the_shards():
    """Their tests are the audit job's, so a shard holding them has the same work as one that doesn't."""
    for name in ("test_web_app_audit.py", "test_hub_page_audit.py"):
        assert fk_shard._weight((TESTS / name).read_bytes()) == 0, name


@pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="the recipe is a Linux script")
def test_the_recipe_lists_the_files_the_shard_function_gives():
    n, _ = _flow_shards()
    for i in range(1, n + 1):
        r = subprocess.run(["bash", str(REPO / "tools/testenv.sh"), "--shard", f"{i}/{n}", "--list"], capture_output=True, text=True,
                           timeout=60)
        assert r.returncode == 0, r.stdout + r.stderr
        assert r.stdout.split() == [f"tests/{f}" for f in fk_shard.shard(TESTS, i, n)]


@pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="the recipe is a Linux script")
@pytest.mark.parametrize("args", [["--shard", "0/4"], ["--shard", "5/4"], ["--shard", "x"], ["--shard", "1/4", "--only", "get"], ["--list"]])
def test_the_recipe_refuses_a_shard_it_cannot_make_sense_of(args):
    r = subprocess.run(["bash", str(REPO / "tools/testenv.sh"), *args], capture_output=True, text=True, timeout=60)
    assert r.returncode == 2, r.stdout + r.stderr


def test_the_workflow_runs_every_shard_number_once_for_the_n_it_tells_the_recipe():
    n, matrix = _flow_shards()
    assert n >= 2
    assert matrix == list(range(1, n + 1)), f"the matrix is {matrix}, the shards are 1..{n}"


def test_the_workflow_runs_the_audit_group_and_every_other_suite_too():
    runs = _recipe_runs()
    commands = [cmd for _, cmd in runs]
    assert commands.count("--shard audit") == 1, commands
    rest = [cmd for cmd in commands if "--shard" not in cmd]
    assert len(rest) == 1, commands
    only = re.search(r"--only (\S+)", rest[0])
    assert only and set(only.group(1).split(",")) == {"pc-sync", "other", "get"}, rest[0]
    assert "--root" in rest[0] and "--lint" in rest[0]
    assert len(commands) == 3, commands     # the shards, the audits, the rest: nothing runs the server suite whole as well


def test_a_job_that_fails_is_not_allowed_to_pass():
    jobs = _jobs()
    assert "fail-fast: false" in jobs["server-shard"]       # one red shard must not hide what the others found
    assert "continue-on-error" not in FLOW
    gate = jobs["test"]       # the job a required check names
    needs = re.search(r"needs:\s*\[([^\]]*)\]", gate)
    assert needs, gate
    assert {x.strip() for x in needs.group(1).split(",")} == set(jobs) - {"test"}
    assert re.search(r"if:\s*\$\{\{\s*always\(\)\s*\}\}", gate), "without always() a failed job would skip the gate, which reads as passed"
    assert "!= success" in gate or '[ "$r" = success ] || exit 1' in gate
    for job, text in jobs.items():
        m = re.search(r"timeout-minutes:\s*(\d+)", text)
        assert m and int(m.group(1)) <= 30, job


def test_the_four_shards_are_balanced_and_exact():
    shards = fk_shard.shards(TESTS, 4)
    whole = fk_shard.all_files(TESTS)
    flat = [f for s in shards for f in s]
    assert sorted(flat) == whole and len(flat) == len(set(flat)) == len(whole)
    loads = [
        sum(fk_shard._weight((TESTS / f).read_bytes().replace(b"\r\n", b"\n"), f) for f in s)
        for s in shards
    ]
    assert max(loads) < 2 * min(loads), f"shard imbalance: {loads}"
    demo_shard = next(i for i, s in enumerate(shards) if "test_demo_sealed.py" in s)
    assert not any(f.startswith("test_hub_") or f in fk_shard._BROWSER_FILES for f in shards[demo_shard])
