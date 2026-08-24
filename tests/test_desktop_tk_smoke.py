from collections.abc import Sequence

import pytest

from ato.brain.agent import Agent
from ato.brain.messages import Message
from ato.ui.desktop import AtoDesktop, DesktopChatController
from ato.ui.knowledge_palette import KnowledgeActionPalette
from ato.ui.memory_palette import MemoryActionPalette
from ato.ui.palette import WorkspaceActionPalette
from ato.ui.permission_dialog import PermissionDialog
from ato.ui.permissions import GuiPermissionBridge, GuiPermissionPrompt
from ato.ui.settings_dialog import DesktopCapabilities, SettingsDialog
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

    bridge = GuiPermissionBridge()
    desktop = AtoDesktop(DesktopChatController(Agent(EchoLLM())), permission_bridge=bridge)
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

    selected: list[str] = []
    palette = WorkspaceActionPalette(desktop.root, desktop.theme, selected.append)
    desktop.root.update_idletasks()
    assert palette.window.winfo_exists() == 1
    palette.close()

    knowledge_palette = KnowledgeActionPalette(desktop.root, desktop.theme, selected.append)
    desktop.root.update_idletasks()
    assert knowledge_palette.window.winfo_exists() == 1
    knowledge_palette.close()

    memory_palette = MemoryActionPalette(desktop.root, desktop.theme, selected.append)
    desktop.root.update_idletasks()
    assert memory_palette.window.winfo_exists() == 1
    memory_palette.close()

    permission = PermissionDialog(
        desktop.root,
        desktop.theme,
        GuiPermissionPrompt("test_tool", "HIGH", '{"path": "example.txt"}'),
    )
    desktop.root.update_idletasks()
    assert permission.result is False
    assert permission.window.winfo_exists() == 1
    permission.deny()
    assert permission.result is False

    settings = SettingsDialog(
        desktop.root,
        desktop.theme,
        DesktopCapabilities(voice_input=False, voice_output=False),
        fullscreen=False,
        on_toggle_theme=lambda: None,
        on_toggle_fullscreen=lambda: None,
    )
    desktop.root.update_idletasks()
    assert settings.window.winfo_exists() == 1
    settings.close()
    assert bridge.attached is True
    desktop._close()
    assert bridge.attached is False
