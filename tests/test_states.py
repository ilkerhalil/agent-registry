import pytest

from agent_registry.states import TransitionError, check_transition


def test_happy_path():
    for current, target in [
        ("draft", "validated"),
        ("validated", "active"),
        ("active", "deprecated"),
        ("deprecated", "retired"),
    ]:
        check_transition(current, target)


def test_retired_is_terminal():
    for target in ("draft", "validated", "active", "deprecated"):
        with pytest.raises(TransitionError):
            check_transition("retired", target)


def test_active_cannot_go_back_to_validated():
    with pytest.raises(TransitionError):
        check_transition("active", "validated")
