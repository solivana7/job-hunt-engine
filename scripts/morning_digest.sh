#!/bin/bash
# 06:00 PT Mon/Wed/Fri — full cycle: aggregate (incl. SerpAPI) -> score -> digest
cd "$(dirname "$0")/.."
.venv/bin/python -m engine.main aggregate \
  && .venv/bin/python -m engine.main score --lane=all \
  && .venv/bin/python -m engine.main digest
