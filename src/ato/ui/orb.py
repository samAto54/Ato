"""Canvas-rendered animated visual core for the original Ato HUD."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from ato.ui.state import AtoStateModel, AtoVisualState
from ato.ui.themes import UiTheme


@dataclass(frozen=True, slots=True)
class OrbProfile:
    speed: float
    pulse: float
    energy: float
    waveform: bool
    scan: bool


ORB_PROFILES = {
    AtoVisualState.IDLE: OrbProfile(0.22, 0.035, 0.35, False, False),
    AtoVisualState.LISTENING: OrbProfile(0.75, 0.12, 0.78, True, False),
    AtoVisualState.PROCESSING: OrbProfile(1.35, 0.07, 0.9, False, True),
    AtoVisualState.TOOL_EXECUTION: OrbProfile(1.0, 0.09, 0.84, False, True),
    AtoVisualState.SPEAKING: OrbProfile(0.9, 0.15, 1.0, True, False),
}


def visual_profile(state: AtoVisualState | str) -> OrbProfile:
    selected = state if isinstance(state, AtoVisualState) else AtoVisualState(state)
    return ORB_PROFILES[selected]


class AtoOrbCanvas:
    """Draw a living layered orb using only local vector primitives."""

    def __init__(self, parent, state_model: AtoStateModel, theme: UiTheme, *, height: int = 340):
        import tkinter as tk

        self.state_model = state_model
        self.theme = theme
        self.canvas = tk.Canvas(parent, height=height, highlightthickness=0, bd=0)
        self._started = time.monotonic()
        self._running = True
        self.canvas.bind("<Destroy>", self._stop)
        self._animate()

    def pack(self, **kwargs) -> None:
        self.canvas.pack(**kwargs)

    def pack_forget(self) -> None:
        self.canvas.pack_forget()

    def set_theme(self, theme: UiTheme) -> None:
        self.theme = theme

    def _stop(self, event) -> None:
        del event
        self._running = False

    def _animate(self) -> None:
        if not self._running:
            return
        self._draw(time.monotonic() - self._started)
        self.canvas.after(33, self._animate)

    def _draw(self, elapsed: float) -> None:
        canvas = self.canvas
        theme = self.theme
        snapshot = self.state_model.snapshot()
        profile = visual_profile(snapshot.state)
        width = max(canvas.winfo_width(), 500)
        height = max(canvas.winfo_height(), 300)
        cx, cy = width / 2, height / 2 - 4
        base = min(width, height) * 0.205
        pulse = 1 + math.sin(elapsed * (2.2 + profile.speed)) * profile.pulse
        radius = base * pulse
        canvas.delete("all")
        canvas.configure(bg=theme.background)

        self._draw_particles(cx, cy, radius, elapsed, profile)
        self._draw_rings(cx, cy, radius, elapsed, profile)
        self._draw_core(cx, cy, radius, elapsed, profile)
        if profile.waveform:
            self._draw_waveform(cx, cy, radius, elapsed, profile)
        if profile.scan:
            self._draw_scan(cx, cy, radius, elapsed)
        canvas.create_text(
            cx,
            cy + radius + 42,
            text=snapshot.status,
            fill=theme.accent,
            font=(theme.heading_family, 11),
        )

    def _draw_particles(
        self, cx: float, cy: float, radius: float, elapsed: float, profile: OrbProfile
    ) -> None:
        for index in range(28):
            angle = index * 2.399 + elapsed * profile.speed * (0.08 + index % 3 * 0.025)
            distance = radius * (1.35 + (index % 7) * 0.16)
            x = cx + math.cos(angle) * distance
            y = cy + math.sin(angle) * distance * 0.58
            size = 1 + (index % 3) * 0.45 * profile.energy
            color = self.theme.accent if index % 4 else self.theme.accent_secondary
            self.canvas.create_oval(x - size, y - size, x + size, y + size, fill=color, outline="")

    def _draw_rings(
        self, cx: float, cy: float, radius: float, elapsed: float, profile: OrbProfile
    ) -> None:
        for index, scale in enumerate((1.28, 1.48, 1.72, 2.02)):
            ring = radius * scale
            direction = -1 if index % 2 else 1
            phase = elapsed * profile.speed * direction * (34 + index * 11)
            extent = 150 - index * 17
            color = self.theme.accent if index < 3 else self.theme.accent_secondary
            self.canvas.create_oval(
                cx - ring,
                cy - ring * 0.64,
                cx + ring,
                cy + ring * 0.64,
                outline=_mix(self.theme.background, color, 0.34),
                width=1,
            )
            for offset in (0, 180):
                self.canvas.create_arc(
                    cx - ring,
                    cy - ring * 0.64,
                    cx + ring,
                    cy + ring * 0.64,
                    start=phase + offset + index * 23,
                    extent=extent,
                    style="arc",
                    outline=color,
                    width=1 + (index == 0),
                )

    def _draw_core(
        self, cx: float, cy: float, radius: float, elapsed: float, profile: OrbProfile
    ) -> None:
        for layer in range(9, 0, -1):
            scale = layer / 9
            ring = radius * scale
            strength = (1 - scale) * 0.7 + profile.energy * 0.18
            color = _mix(self.theme.background, self.theme.accent, min(strength, 0.88))
            self.canvas.create_oval(
                cx - ring,
                cy - ring,
                cx + ring,
                cy + ring,
                fill=color,
                outline="",
            )
        inner = radius * (0.26 + math.sin(elapsed * 3.1) * 0.025)
        self.canvas.create_oval(
            cx - inner,
            cy - inner,
            cx + inner,
            cy + inner,
            fill=_mix(self.theme.accent, "#FFFFFF", 0.58),
            outline=self.theme.text,
            width=1,
        )

    def _draw_waveform(
        self, cx: float, cy: float, radius: float, elapsed: float, profile: OrbProfile
    ) -> None:
        points = []
        for index in range(73):
            angle = index / 72 * math.tau
            wave = math.sin(angle * 8 + elapsed * 6.5) * 0.11
            wave += math.sin(angle * 13 - elapsed * 4.2) * 0.045
            ring = radius * (1.13 + wave * profile.energy)
            points.extend((cx + math.cos(angle) * ring, cy + math.sin(angle) * ring))
        self.canvas.create_line(
            *points,
            fill=self.theme.accent_secondary,
            width=2,
            smooth=True,
        )

    def _draw_scan(self, cx: float, cy: float, radius: float, elapsed: float) -> None:
        offset = ((elapsed * 0.7) % 1.0 - 0.5) * radius * 1.7
        half = math.sqrt(max(radius * radius - offset * offset, 0))
        self.canvas.create_line(
            cx - half,
            cy + offset,
            cx + half,
            cy + offset,
            fill=self.theme.accent_secondary,
            width=1,
        )


def _mix(first: str, second: str, amount: float) -> str:
    amount = max(0.0, min(1.0, amount))
    one = tuple(int(first[index : index + 2], 16) for index in (1, 3, 5))
    two = tuple(int(second[index : index + 2], 16) for index in (1, 3, 5))
    values = tuple(round(a + (b - a) * amount) for a, b in zip(one, two, strict=True))
    return "#" + "".join(f"{value:02X}" for value in values)
