"""Sürüm yaşam döngüsü kuralları."""

STATES = ("draft", "validated", "candidate", "active", "deprecated", "retired", "blocked")

# Yalnızca bu geçişlere izin var.
ALLOWED = {
    "draft": {"validated", "blocked"},
    "validated": {"active", "candidate", "draft", "blocked"},
    "candidate": {"active", "validated", "blocked"},
    "active": {"deprecated", "blocked"},
    "deprecated": {"retired"},
    "retired": set(),
    "blocked": {"draft", "validated"},
}

TERMINAL = {"retired"}


class TransitionError(Exception):
    pass


def check_transition(current: str, target: str) -> None:
    if current not in ALLOWED:
        raise TransitionError(f"bilinmeyen durum: {current}")
    if target not in ALLOWED[current]:
        raise TransitionError(f"transition {current} -> {target} is not allowed")
