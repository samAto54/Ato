from collections.abc import Sequence

import pytest

from ato.brain.agent import Agent
from ato.brain.messages import Message
from ato.ui.desktop import AtoDesktop, DesktopChatController
from ato.ui.state import AtoVisualState
from ato.ui.themes import ThemeId


class EchoLLM:
    def generate(self, messages: Sequence[Message], tools=None) -> str:
        del tools
        return messages[-1].content


def test_tk_desktop_builds_orb_and_switches_real_state_and_layout() -> None:
    tkinter = pytest.importorskip("tkinter")
    try:
        probe = tkinter.Tk()
        probe.withdraw()
        probe.update_idletasks()
        probe.destroy()
    except tkinter.TclError:
        pytest.skip("Tk display is unavailable")

    desktop = AtoDesktop(DesktopChatController(Agent(EchoLLM())))
    desktop.root.withdraw()
    desktop.root.update_idletasks()
    assert desktop.theme.id is ThemeId.ATO_HUD
    assert desktop.orb.canvas.winfo_manager() == "pack"
    assert desktop.composer.winfo_manager() == "pack"

    desktop._set_busy(True)
    assert desktop.state_model.snapshot().state is AtoVisualState.PROCESSING
    desktop._set_busy(False)
    assert desktop.state_model.snapshot().state is AtoVisualState.IDLE

    desktop._toggle_theme()
    assert desktop.theme.id is ThemeId.STANDARD
    assert desktop.orb.canvas.winfo_manager() == ""
    desktop._toggle_theme()
    assert desktop.orb.canvas.winfo_manager() == "pack"
    desktop.root.destroy()
