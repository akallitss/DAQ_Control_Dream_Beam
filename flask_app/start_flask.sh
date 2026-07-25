#!/bin/bash

# Absolute venv interpreter, NOT bare `flask`: ~/.local/bin/flask has shebang
# #!/usr/bin/python3 (3.8) and ~/.local/bin precedes .venv/bin on PATH, so the
# bare name silently ran the GUI on system python (3.8) while every other DAQ
# process ran on the venv's 3.12. app.py spawns processor_watcher / qa_watcher /
# backup_watcher with sys.executable, so this one line decides the interpreter
# for all of them. Absolute path because tmux panes re-source .bashrc and
# reorder PATH, which defeats `source .venv/bin/activate` (2026-07-25).
export FLASK_APP=flask_app/app.py
exec /local/home/banco/DAQ_Control_Dream_Beam/.venv/bin/python -m flask run --host=0.0.0.0 --port=5001
