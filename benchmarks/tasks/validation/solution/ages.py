import re


def parse_age(text):
    """Parse a person's age from form input: a whole number from 0 to 150."""
    value = text.strip() if isinstance(text, str) else ""
    if not re.fullmatch(r"[0-9]{1,3}", value) or int(value) > 150:
        raise ValueError(f"Not a valid age: {text!r}")
    return int(value)
