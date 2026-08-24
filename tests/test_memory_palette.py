from ato.ui.memory_palette import MEMORY_ACTIONS


def test_memory_palette_has_only_fixed_risk_labelled_actions() -> None:
    assert tuple(action.name for action in MEMORY_ACTIONS) == (
        "remember",
        "edit",
        "refresh",
        "archive",
        "restore",
        "expire",
        "clear_expiration",
        "forget",
    )
    assert {action.risk for action in MEMORY_ACTIONS} <= {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert {action.name: action.risk for action in MEMORY_ACTIONS}["forget"] == "CRITICAL"
