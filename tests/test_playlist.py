"""
<summary>Tests for the preset playlist's rotation, editing and settings round trip.</summary>
<remarks>No Qt here: the GUI's timer and reconnect loop sit on top of this class and are
exercised by hand against the simulator, as the rest of the window is.</remarks>
"""

from pixelate.playlist import DEFAULT_MINUTES, MAX_MINUTES, MIN_MINUTES, Playlist
from pixelate.presets import BY_KEY


def test_rotation_wraps_round():
    """
    <summary>After the last item the rotation starts again from the first.</summary>
    """
    pl = Playlist(["stars", "fireflies", "garden"], 5)
    assert [pl.next_key() for _ in range(5)] == ["stars", "fireflies", "garden", "stars", "fireflies"]


def test_empty_playlist_has_nothing_to_send():
    """
    <summary>An empty playlist returns None and stays at position -1.</summary>
    """
    pl = Playlist()
    assert pl.next_key() is None
    assert pl.position == -1


def test_minutes_are_clamped():
    """
    <summary>Minutes outside the allowed range are clamped, and the timer period follows them.</summary>
    """
    assert Playlist([], 0).minutes == MIN_MINUTES
    assert Playlist([], 10_000).minutes == MAX_MINUTES
    assert Playlist(["stars"], 3).interval_ms == 180_000


def test_remove_keeps_the_rotation_in_step():
    """
    <summary>Removing items before or at the current position does not skip or repeat the next one.</summary>
    """
    pl = Playlist(["a", "b", "c", "d"])
    pl.next_key(), pl.next_key()  # "b" was sent last
    pl.remove(0)  # take "a" out from before it
    assert pl.next_key() == "c"
    pl.remove(99)  # out of range is ignored
    pl.remove(pl.position)  # remove the item just sent
    assert pl.next_key() == "d"


def test_remove_last_item_wraps_to_the_start():
    """
    <summary>Removing the item just sent at the end of the list goes back to the first.</summary>
    """
    pl = Playlist(["a", "b"])
    pl.next_key(), pl.next_key()
    pl.remove(1)
    assert pl.next_key() == "a"


def test_settings_round_trip():
    """
    <summary>What encode stores, decode reads back, repeats included.</summary>
    """
    pl = Playlist(["stars", "garden", "stars"], 7)
    back = Playlist.decode(pl.encode(), "7", set(BY_KEY))
    assert back.keys == ["stars", "garden", "stars"]
    assert back.minutes == 7


def test_decode_tolerates_missing_or_bad_settings():
    """
    <summary>Missing or unreadable QSettings values give an empty playlist with the default interval.</summary>
    """
    assert Playlist.decode(None, None, set(BY_KEY)).keys == []
    assert Playlist.decode("", "ten", set(BY_KEY)).minutes == DEFAULT_MINUTES


def test_decode_drops_unknown_presets():
    """
    <summary>A saved key whose preset no longer exists is dropped and the rest kept in order.</summary>
    """
    pl = Playlist.decode("stars,gone,garden", 5, set(BY_KEY))
    assert pl.keys == ["stars", "garden"]
