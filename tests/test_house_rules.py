"""
<summary>House rules gate: no dash characters as punctuation, and a well-formed VERSION.</summary>
<remarks>These are the checks a build script would run before packaging. They live in the
test suite so they run on every test pass and fail before anything is shipped. The scan
covers every text file in the tree that would be published; the virtual environment and
caches are skipped.</remarks>
"""

import re
from pathlib import Path

import pytest

import pixelate

ROOT = Path(__file__).resolve().parent.parent
TEXT_SUFFIXES = {".py", ".md", ".txt", ".sh", ".cfg", ".toml", ".ini", ".json"}
# The three output folders are build output, already checked by build-zip.sh before they are written.
SKIP_DIRS = {".git", ".pytest_cache", "__pycache__", "presets-out", "Pixelate App", "Pixelate Dist", "Pixelate Git"}


def _text_files():
    """<summary>Every text file that would go into the repository.</summary>"""
    for path in ROOT.rglob("*"):
        if any(part in SKIP_DIRS or part.startswith(".venv") for part in path.relative_to(ROOT).parts):
            continue
        if path.is_file() and (path.suffix in TEXT_SUFFIXES or path.name in ("VERSION", "LICENSE")):
            yield path


def test_there_are_files_to_check():
    """<summary>Guards against the scan silently covering nothing.</summary>"""
    assert len(list(_text_files())) >= 10


@pytest.mark.parametrize("path", sorted(_text_files()), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_dash_characters(path):
    """<summary>No em dash or en dash anywhere, in any file.</summary>"""
    em_dash, en_dash = chr(0x2014), chr(0x2013)  # built from code points so this file passes its own check
    text = path.read_text(encoding="utf-8")
    bad = [i + 1 for i, line in enumerate(text.splitlines()) if em_dash in line or en_dash in line]
    assert not bad, f"dash character on line(s) {bad}"


@pytest.mark.parametrize("path", sorted(p for p in _text_files() if p.suffix == ".md"), ids=lambda p: p.name)
def test_no_hyphen_as_punctuation_in_docs(path):
    """
    <summary>In prose, a spaced hyphen is being used as a dash. Lists and negative numbers are fine.</summary>
    <remarks>Code files are not checked this way because " - " is subtraction there.</remarks>
    """
    bad = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if re.match(r"^\s*-\s", line):  # list item
            continue
        if re.search(r"\S\s+-\s+\S", line) and not re.search(r"\s-\d", line):
            bad.append(i)
    assert not bad, f"spaced hyphen used as punctuation on line(s) {bad}"


def test_version_file_is_well_formed():
    """<summary>VERSION holds major.minor.build and the package reads exactly that.</summary>"""
    text = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", text), f"VERSION is {text!r}"
    assert pixelate.__version__ == text
