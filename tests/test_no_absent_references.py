"""Guard: comments, docstrings, strings and texts in this repository cite nothing that is not in the repository.

It scans the repository's text files, enumerated by walking the tree (the test host holds an extracted archive, no git),
and fails naming `path:line` and the kind on:

  folder   the folder docs/design exists
  A        a path or file name of a document that is not in this repository, or of a page that was removed
  B        a decision id (the letter R and two or three digits, in any wording around it; "ruling 11:55")
  C        a citation of such a document by its bare number ("(05 2.7)", "of 06 3.2")
  D        a hand-off file name of the form YYYY-MM-DD-HHMM-<words>.md

Lines that must keep such a text (a program string the test compares, data) are listed one per line in
tests/absent_references_exceptions.txt as `<path> | <the line, stripped> | 1 | <reason>`; a listed line that no longer
matches anything fails, so the list cannot rot.
"""
import os
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXCEPTIONS = ROOT / "tests" / "absent_references_exceptions.txt"
ITSELF = {"tests/test_no_absent_references.py", "tests/absent_references_exceptions.txt"}

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", "pytest-tmp", "_vendor", ".gradle", ".venv", "venv"}
SKIP_FILES = {"LICENSE", "THIRD-PARTY.md"}
SKIP_SUFFIXES = {".lock", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".icns", ".svg", ".woff", ".woff2",
                 ".ttf", ".otf", ".eot", ".tiff"}

# ---- the shapes of a reference ----
SLUGS = ("0[1-9]-(?:foundations|components|hub-page|web-app|android-app|command-line|accessibility|platform-mapping"
         "|implementation-order)|10-feature-ideas")
CLOCK = r"\d{1,2}:\d{2}(?:\.\d+)?"
LOC1 = r"(?:\d+(?:\.\d+)*[a-z]?|[A-Z]{1,4}-?\d+[a-z]?)"
LOC1B = rf"(?:{CLOCK}|{LOC1})"
GUARD = r"(?!(?:0[1-9]|10) (?:section|§|\d|[A-Z]{1,4}-?\d))(?!(?:0[1-9]|10)-[a-z])"
SUFFIX = r"(?: (?:rule|test|point|item) \d+)?"


def _locitem(loc1, prefix):
    return rf"(?:{prefix})?{loc1}(?:(?: to |[-–]){loc1})?{SUFFIX}"


LOCITEM = _locitem(LOC1, r"(?:sections?|§) ?|(?:sections?) ")
LOCITEM_B = _locitem(LOC1B, r"(?:sections?|points?|assumptions?|items?)\s|§\s?|s(?=\d)")
LOCITEM_C = _locitem(r"(?:\d+(?:\.\d+)*[a-z]?|[A-Z]{1,4}-\d+)", r"(?:sections?|§) ?|(?:sections?) ")
LOCS = rf" {LOCITEM}(?:(?:, |,? and |; ?){GUARD}{LOCITEM_C})*"
LOCS_B = rf" {LOCITEM_B}(?:(?:, |,? and |; ?){GUARD}(?:{LOCITEM_B}|(?:ruling of |ruled ){CLOCK}))*(?: ruling)?"
B_REF = rf"(?:(?:the )?(?:ruling|DECISIONS|design) )?R\d{{1,3}}\b(?:{LOCS_B})?"
B_CLOCK = rf"(?<!\w)(?:ruling|design|ruled)(?: of)? {CLOCK}"
B_ANY = r"(?<![A-Za-z0-9_-])R\d{2,3}(?![A-Za-z0-9_-])"
D_NAME = (r"(?<!\w)(?:(?:see|per|in|the brief|the handoff|the hand-off|brief|handoff|hand-off) )?"
          r"`?20\d\d-\d\d-\d\d-\d{4}-[a-z0-9-]+\.md`?")
PAGE = r"(?<!\w)(?:(?:see|per|in) )?`?(?:docs/)?(?:maintainer|testing-e2e|android-e2e)\.md(?::\d+)?`?"
LINE = r"(?::\d+(?:-\d+)?)?"
NAME = rf"(?:docs/design/)?(?:mockups/)?(?:(?P<nn{{i}}>{SLUGS})|CONTENT-SPEC)\.md{LINE}"


def _one(i):
    name = NAME.replace("{i}", str(i))
    return (rf"(?:{name}(?:{LOCS})?"
            rf"|docs/design (?P<dn{i}>0[1-9]|10)(?:{LOCS})?"
            rf"|(?<![\w.\-:/$#=@%])(?<!\d )(?P<bn{i}>0[1-9]|10)(?:{LOCS})(?![\w/%])(?!\.\d)(?!:\d))")


UNIT_SEP = r"(?:, |; | and )"
GROUP = rf"(?:{_one(1)}(?:{UNIT_SEP}{_one(2)})*)"
CATCH = r"docs/design(?:/(?:README\.md|mockups/?[\w./-]*)?)?"
BARE_ID = re.compile(B_ANY)
UNIT_RE = re.compile(rf"(?P<B>{B_REF})|(?P<BC>{B_CLOCK})|(?P<D>{D_NAME})|(?P<P>{PAGE})|(?P<G>{GROUP})|(?P<X>{CATCH})")
GROUP_ONE = re.compile(_one(9))


def _kind(m):
    for g, k in (("B", "B"), ("BC", "B"), ("D", "D"), ("P", "A"), ("X", "A")):
        if m.group(g) is not None:
            return k
    return "A" if re.search(r"\.md|docs/design", m.group()) else "C"


def _valid(s, m):
    t = m.group()
    if m.group("B") is not None:
        if re.fullmatch(r"R\d{1,3}", t):
            return s[m.start() - 1:m.start()] == "(" and s[m.end():m.end() + 1] == ")"
        return True
    if m.group("G") is not None:
        if not GROUP_ONE.search(t):
            return False
        mm = re.match(r"(?:docs/design )?(10) ([^ ]+)", t)
        if mm and re.fullmatch(r"\d+", mm.group(2)) and s[m.start() - 1:m.start()] != "(":
            return False
    return True


def _hits(line):
    """The kinds of reference in one line."""
    if not any(c in line for c in ("R", "0", "1", "docs", ".md", "design", "ruling", "ruled", "DECISIONS")):
        return []
    kinds = [_kind(m) for m in UNIT_RE.finditer(line) if _valid(line, m)]
    if BARE_ID.search(line):
        kinds.append("B")
    return kinds


# ---- the tree ----
def _files():
    out = []
    for d, ds, fs in os.walk(ROOT):
        rel_dir = Path(d).relative_to(ROOT).as_posix()
        ds[:] = sorted(x for x in ds if x not in SKIP_DIRS and not (rel_dir == "." and x == "release-notes"))
        for f in sorted(fs):
            rel = f if rel_dir == "." else f"{rel_dir}/{f}"
            if rel in ITSELF or f in SKIP_FILES or Path(f).suffix.lower() in SKIP_SUFFIXES:
                continue
            out.append(rel)
    return out


def _exceptions():
    out = {}
    for n, raw in enumerate(EXCEPTIONS.read_text(encoding="utf-8").split("\n"), 1):
        if not raw:
            continue
        path, rest = raw.split("|", 1)
        body, count, reason = rest.rsplit("|", 2)
        assert count.strip() == "1" and reason.strip(), f"exceptions line {n} needs a count of 1 and a reason"
        out[(path.strip(), body.strip())] = reason.strip()
    return out


@lru_cache(maxsize=1)
def _scan():
    """({kind: ["path:line: text"]}, the set of exception keys that matched a line)."""
    skip = _exceptions()
    found = {"A": [], "B": [], "C": [], "D": []}
    used = set()
    for rel in _files():
        data = (ROOT / rel).read_bytes()
        if b"\0" in data[:8192]:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for n, line in enumerate(text.split("\n"), 1):
            kinds = _hits(line.rstrip("\r"))
            if not kinds:
                continue
            if (rel, line.strip()) in skip:
                used.add((rel, line.strip()))
                continue
            for k in sorted(set(kinds)):
                found[k].append(f"{rel}:{n}: {line.strip()[:120]}")
    return found, used


def test_the_design_folder_is_not_in_the_repository():
    assert not (ROOT / "docs" / "design").exists(), "the folder docs/design exists"


def test_no_path_or_name_of_an_absent_document():
    assert not _scan()[0]["A"], "a document that is not in this repository, or a removed page, is named:\n" + \
        "\n".join(_scan()[0]["A"])


def test_no_decision_id():
    assert not _scan()[0]["B"], "a decision id:\n" + "\n".join(_scan()[0]["B"])


def test_no_citation_by_document_number():
    assert not _scan()[0]["C"], "a document that is not in this repository is cited by its number:\n" + \
        "\n".join(_scan()[0]["C"])


def test_no_hand_off_file_name():
    assert not _scan()[0]["D"], "a hand-off file name:\n" + "\n".join(_scan()[0]["D"])


def test_every_exception_still_matches_a_line():
    stale = sorted(set(_exceptions()) - _scan()[1])
    assert not stale, "in the exception list but no longer found, drop the line:\n" + \
        "\n".join(f"{p} | {b}" for p, b in stale)
