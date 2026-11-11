"""
<summary>Tests for the hardware-free parts of pixelate.devices.</summary>
<remarks>Nothing here opens Bluetooth. The font lookup is covered because its
absence was the first thing to break on the real panel.</remarks>
"""

from pathlib import Path

import pytest

from pixelate import devices


def test_classify_by_name():
    """<summary>The scanner tells the two protocols apart from the advertised name.</summary>"""
    assert devices.classify("IDM-ABC123") == "idotmatrix"
    assert devices.classify("LED_BLE_1234") == "ipixel"
    assert devices.classify("Something else") == "unknown"


def test_find_text_font_returns_an_existing_file():
    """<summary>The chosen font exists and Pillow can open it at the size the library uses.</summary>"""
    try:
        path = devices.find_text_font()
    except FileNotFoundError:
        pytest.skip("no candidate font installed on this machine")
    assert Path(path).is_file()
    from PIL import ImageFont

    ImageFont.truetype(path, 16)


def test_find_text_font_fails_clearly(monkeypatch):
    """<summary>With no candidates present the error names the fix, not Pillow's bare message.</summary>"""
    monkeypatch.setattr(devices, "TEXT_FONT_CANDIDATES", ["/nonexistent/font.ttf"])
    with pytest.raises(FileNotFoundError, match="fonts-dejavu-core"):
        devices.find_text_font()


def test_check_carries_the_library_error_text():
    """<summary>A False from the library becomes an error naming the library's logged reason.</summary>"""
    import logging

    devices._install_error_capture()
    logging.getLogger("idotmatrix.modules.gif").error("could not upload gif processed: GATT Protocol Error: Unlikely Error")
    with pytest.raises(RuntimeError, match="Unlikely Error"):
        devices._check(False)
    # the detail is consumed, so a later unrelated failure does not repeat it
    with pytest.raises(RuntimeError, match="no detail logged"):
        devices._check(False)
    assert devices._check(b"ok") == b"ok"
