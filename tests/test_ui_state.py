import pytest

from ato.ui.state import AtoStateModel, AtoVisualState


def test_visual_state_follows_real_chat_lifecycle() -> None:
    model = AtoStateModel()
    assert model.snapshot().state is AtoVisualState.IDLE
    processing = model.transition(AtoVisualState.PROCESSING, active_task="Answering user")
    assert processing.status == "PROCESSING"
    assert processing.active_task == "Answering user"
    idle = model.transition(AtoVisualState.IDLE)
    assert idle.state is AtoVisualState.IDLE
    assert idle.revision == 2


def test_tool_state_requires_label_and_invalid_transition_fails_closed() -> None:
    model = AtoStateModel()
    with pytest.raises(ValueError, match="tool label"):
        model.transition(AtoVisualState.TOOL_EXECUTION)
    with pytest.raises(ValueError, match="Invalid"):
        model.transition(AtoVisualState.SPEAKING)


def test_state_labels_are_single_line_and_bounded() -> None:
    model = AtoStateModel()
    snapshot = model.transition(
        AtoVisualState.PROCESSING,
        active_task="line one\n" + "x" * 200,
    )
    assert "\n" not in snapshot.active_task
    assert len(snapshot.active_task) <= 120


def test_permission_state_can_enter_and_leave_listening() -> None:
    model = AtoStateModel()
    model.transition(AtoVisualState.TOOL_EXECUTION, tool="MICROPHONE")
    model.transition(AtoVisualState.LISTENING, tool="MICROPHONE")
    model.transition(AtoVisualState.TOOL_EXECUTION, tool="TRANSCRIPTION")
    model.transition(AtoVisualState.PROCESSING, tool="TRANSCRIPTION")
    assert model.snapshot().state is AtoVisualState.PROCESSING
