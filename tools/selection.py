# tools/selection.py
"""Deprecated compatibility module.

Tool selection is now catalog-based (``tools.catalog``): basic tools are always
available, extended tools are activated through ``tools_search``, and no
per-request keyword matching remains. This module is intentionally empty so old
imports fail loudly instead of silently reintroducing the old behavior.
"""
