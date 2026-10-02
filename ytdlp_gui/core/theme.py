"""Theme choices (no Qt)."""
THEMES = [("system", "System"), ("light", "Light"), ("dark", "Dark")]
_KEYS = {key for key, _label in THEMES}


def normalize(choice) -> str:
    return choice if choice in _KEYS else "system"


def effective(choice, system_is_dark: bool) -> str:
    """Resolve a saved choice to "light" or "dark"."""
    choice = normalize(choice)
    if choice == "system":
        return "dark" if system_is_dark else "light"
    return choice
