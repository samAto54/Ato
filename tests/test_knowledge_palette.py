from ato.ui.knowledge_palette import KNOWLEDGE_ACTIONS


def test_knowledge_palette_has_only_fixed_risk_labelled_actions() -> None:
    assert tuple(action[0] for action in KNOWLEDGE_ACTIONS) == ("import", "refresh", "remove")
    assert {action[3] for action in KNOWLEDGE_ACTIONS} <= {"LOW", "MEDIUM", "HIGH"}
    assert {action[0]: action[3] for action in KNOWLEDGE_ACTIONS}["remove"] == "HIGH"
