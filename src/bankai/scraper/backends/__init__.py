"""Concrete scraper backends.

Each module here defines exactly one ``ScraperBackend`` subclass and decorates
it with ``@register``. The backends are auto-imported by the registry on first
lookup, so just dropping a new file in is enough.
"""
