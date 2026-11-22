"""The spoken form of a material and of the first two display lines (spoken.py)."""
import pytest

from filament_kit import spoken


@pytest.mark.parametrize("mat, said", [
    ("PLA", "P L A"), ("PETG", "P E T G"), ("PA6-CF", "P A 6, C F"), ("PLA+", "P L A plus"),
    ("HIPS", "HIPS"), ("PEEK", "PEEK"), ("Silk PLA", "Silk P L A"), ("PLA Matte", "P L A Matte"),
    ("Nylon", "Nylon"), ("PLA/PETG", "P L A, P E T G"), ("PLA - CF", "P L A, C F"), ("PLA-", "P L A"),
    ("123", "123"), ("", ""), (None, ""),
])
def test_material(mat, said):
    assert spoken.material(mat) == said


def test_the_written_form_is_one_function(monkeypatch):
    monkeypatch.setattr(spoken, "spelled", lambda letters: ", ".join(letters))
    assert spoken.material("PLA") == "P, L, A"


def test_a_maker_in_capitals_is_left_alone():
    assert spoken.speak("Black", "SUNLU", spoken.material("PLA")) == "Black SUNLU P L A"
    assert spoken.speak("Black", "ESUN", spoken.material("PLA+")) == "Black ESUN P L A plus"


def test_a_name_repeating_the_material_is_spelled_too():
    assert spoken.speak("PLA Galaxy Blue", "SUNLU", spoken.material("PLA+"), mat="PLA+") == \
        "P L A Galaxy Blue SUNLU P L A plus"
    assert spoken.speak("Black", "ESUN PLA", mat=None) == "Black ESUN PLA"  # no material known: left alone


def test_missing_parts_are_skipped():
    assert spoken.speak("Saved", "Red") == "Saved Red"
    assert spoken.speak("New Tag Scanned") == "New Tag Scanned"
    assert spoken.speak("Spool", None, None) == "Spool"
