from ato.ui.settings_dialog import DesktopCapabilities


def test_desktop_capabilities_report_ready_and_locked_states() -> None:
    rows = dict(DesktopCapabilities(voice_input=True, voice_output=False).rows())

    assert rows == {
        "TEXT CHAT": "READY",
        "VOICE INPUT": "READY",
        "VOICE OUTPUT": "LOCKED",
        "BACKGROUND LISTENING": "LOCKED",
    }
