"""ui — pywebview layer.

Modules:
    api — Api class: all JS ↔ Python bridge methods

The UI layer never calls langchain directly; it only talks to ``core.runner``
and ``core.config`` through :class:`ui.api.Api`.
"""
