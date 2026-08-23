# Agent Notes

- Read `README.md` before making project changes.
- The application code is in `src/fenetre`.
- Use the local virtualenv in `venv` for Python commands.
- Keep changes scoped to the current task.
- Prefer Docker/Portainer and AREDN/IP-camera workflows in docs and examples.
- Treat GoPro and Raspberry Pi capture as optional legacy backends unless the task is specifically about them.
- Do not expose `/srv/fenetre/data` directly through a static web server in examples; use the Fenetre server on `:8888` so auth and per-camera visibility work.
- If you edit Python files, run `venv/bin/black` on those files.
- Use `TODO.md` only for current follow-up items that apply to this fork.
