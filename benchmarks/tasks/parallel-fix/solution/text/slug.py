import re


def slugify(text):
    """A URL-friendly version of text."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
