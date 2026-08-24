from ato.ui.palette import WORKSPACE_ACTION_GROUPS, workspace_action_names


def test_workspace_palette_actions_are_unique_and_fixed() -> None:
    names = workspace_action_names()

    assert names == (
        "list",
        "read",
        "search",
        "status",
        "diff",
        "staged",
        "log",
        "branches",
        "syntax",
        "lint",
        "tests",
        "preview",
        "checkpoints",
        "rollback",
    )
    assert len(names) == len(set(names))


def test_workspace_palette_shows_risk_for_every_action() -> None:
    actions = [action for _, group in WORKSPACE_ACTION_GROUPS for action in group]

    assert all(action.risk in {"LOW", "MEDIUM", "HIGH"} for action in actions)
    risks = {action.name: action.risk for action in actions}
    assert risks["tests"] == "HIGH"
    assert risks["rollback"] == "HIGH"
