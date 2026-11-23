"""Which server test files one CI shard runs (tools/testenv.sh --shard I/N), the same answer wherever it is asked.

    fk_shard.py I N TESTDIR    the file names of shard I of N (1-based), one per line, sorted

Every test_*.py in TESTDIR lands in exactly one shard. The split is greedy by line count (largest file first, into the lightest
shard so far), with the name as the tie-break: line count, not bytes, so CRLF and LF checkouts agree. A file marked browser_audit
as a whole weighs nothing: its own job runs it. The weight is a rough stand-in for run time; it only has to keep the shards in
the same ballpark, and a file that grows moves to another shard by itself.
"""
import sys
from pathlib import Path


def all_files(test_dir):
    return sorted(p.name for p in Path(test_dir).glob("test_*.py"))


_BROWSER_FILES = {"test_alerts_ui.py", "test_menu_ui.py", "test_webapp_offline_marks.py"}


def _weight(data, name: str = ""):
    if b"\npytestmark = pytest.mark.browser_audit" in data:
        return 0
    lines = data.count(b"\n")
    if name == "test_demo_sealed.py":
        return lines + 50000
    if name.startswith("test_hub_") or name in _BROWSER_FILES:
        return lines + 2000
    return lines


def shards(test_dir, n):
    """The n shards, as lists of file names: shards(d, n)[i - 1] is shard i."""
    weight = {name: _weight((Path(test_dir) / name).read_bytes().replace(b"\r\n", b"\n"), name) for name in all_files(test_dir)}
    out, load = [[] for _ in range(n)], [0] * n
    for name in sorted(weight, key=lambda f: (-weight[f], f)):
        i = load.index(min(load))
        out[i].append(name)
        load[i] += weight[name]
    return [sorted(files) for files in out]


def shard(test_dir, i, n):
    if not 1 <= i <= n:
        raise ValueError(f"shard {i} of {n}: the first is 1 and the last is {n}")
    return shards(test_dir, n)[i - 1]


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    try:
        print("\n".join(shard(sys.argv[3], int(sys.argv[1]), int(sys.argv[2]))))
    except ValueError as e:
        sys.exit(f"error: {e}")
