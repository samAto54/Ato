from ato.ui.research_palette import RESEARCH_ACTIONS


def test_research_palette_has_only_fixed_medium_risk_actions() -> None:
    assert tuple(action[0] for action in RESEARCH_ACTIONS) == ("search", "fetch")
    assert {action[3] for action in RESEARCH_ACTIONS} == {"MEDIUM"}
