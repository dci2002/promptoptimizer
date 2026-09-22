# dev

Developer helpers that are not part of the shipped application.

- `smoke_core.py` — headless smoke test for the core package
  (config + prompt_io). Added in Phase 3 (T3.4); a stub is present at Phase 0.

These helpers are meant to be run from the project root so that `scripts/`
is on `sys.path` and `import core` works without an installed package.
