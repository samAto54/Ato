from ato.ui.memory_palette import MEMORY_ACTIONS


def test_memory_palette_has_only_fixed_risk_labelled_actions() -> None:
    assert tuple(action[0] for action in MEMORY_ACTIONS) == (
        "remember",
        "edit",
        "refresh",
        "archive",
        "restore",
        "forget",
    )
    assert {action[3] for action in MEMORY_ACTIONS} <= {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert {action[0]: action[3] for action in MEMORY_ACTIONS}["forget"] == "CRITICAL"
