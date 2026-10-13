#!/bin/bash
# 06:30 PT daily — free API sources only, then score anything new
cd "$(dirname "$0")/.."
.venv/bin/python -m engine.main aggregate --source=greenhouse,lever \
  && .venv/bin/python -m engine.main score --lane=all
