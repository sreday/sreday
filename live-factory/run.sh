#!/usr/bin/env bash
# LIVE talk factory app on http://localhost:8043 (macOS / Linux; Windows uses run.cmd)
cd "$(dirname "$0")"
export PYTHONUTF8=1
exec env/bin/python -m live_factory serve
