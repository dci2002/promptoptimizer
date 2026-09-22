# ui/js

Static UI assets for the pywebview window. Added in Phase 1 (T1.3–T1.5)
and later phases:

- `index.html` — two tabs: Prompts / Settings
- `style.css` — base layout, tabs, sections, multiline fields, icon buttons, tooltips
- `app.js` — tab switching, table rendering, polling, execution area
- `dialog.html` / `dialog.js` — in-page modal for LLM add/edit
- `variables.html` / `variables.js` — in-page modal for variable add

No framework; vanilla JavaScript only. The page talks to Python solely
through `window.pywebview.api`.
