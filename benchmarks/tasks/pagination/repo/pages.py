def paginate(items, page, size):
    """Return page `page` (0-based) of `items`, `size` items per page."""
    start = page * size
    return items[start:start + size + 1]
