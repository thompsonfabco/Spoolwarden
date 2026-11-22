"""What the station says aloud: a material in its spoken form, and the words of the first two display lines.

The screen keeps "PLA"; the voices say it best letter by letter. A part of the material made of capital letters
(two to five) is spelled ("PLA" -> "P L A", "PA6-CF" -> "P A 6, C F"); HIPS and PEEK and every part with lower-case
letters ("Silk", "Nylon") are words; digits stay digits; "+" is "plus"; "-" and "/" are a pause. Only the material
is treated so: a maker's name in capitals ("SUNLU") is left alone.
"""
from __future__ import annotations

import re

AS_WORDS = {"HIPS", "PEEK"}   # capital-letter materials that are said as words
_PART = re.compile(r"[A-Za-z]+|\+|[-/]")


def spelled(letters: str) -> str:
    """THE written form of letter-by-letter speech: one place to change it ("P L A", "P. L. A." or "P, L, A")."""
    return " ".join(letters)


PAUSE = ", "   # the written form of a pause


def _part(m: re.Match) -> str:
    p = m.group()
    if p == "+":
        return " plus "
    if p in "-/":
        return PAUSE
    if p.isupper() and 2 <= len(p) <= 5 and p not in AS_WORDS:
        return " " + spelled(p) + " "
    return p


def material(text) -> str:
    """The spoken form of a material ("" for none)."""
    if not text:
        return ""
    s = _PART.sub(_part, str(text))
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"(\s*,)+\s*", ", ", s)          # runs of pauses make one
    return s.strip(" ,")


def _spellable(mat) -> set[str]:
    """The capital-letter parts of a material that are spelled (the words of a name that match them follow)."""
    return {p for p in re.findall(r"[A-Za-z]+", str(mat or "")) if p.isupper() and 2 <= len(p) <= 5 and p not in AS_WORDS}


def words(text, mat=None) -> str:
    """A name or maker as it is, except that a whole word equal to a spelled part of the material ("PLA" in
    "PLA Galaxy Blue", material "PLA+") is spelled too: the name says what the material says."""
    parts = _spellable(mat)
    return re.sub(r"[A-Za-z]+", lambda m: " " + spelled(m.group()) + " " if m.group() in parts else m.group(),
                  str(text or "")).strip()


def speak(first, *parts, mat=None) -> str:
    """The words of the first two display lines: `first` and the other parts (a material part comes through
    material()), in the order shown. `mat` is the item's material, for the words of a name that repeat it."""
    return re.sub(r"\s+", " ", " ".join(x for x in (words(t, mat).strip() for t in (first, *parts)) if x))
