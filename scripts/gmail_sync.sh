#!/bin/bash
# 22:00 PT daily — recruiter thread tracking (no-op until .[gmail] + OAuth configured)
cd "$(dirname "$0")/.."
exec .venv/bin/python -m engine.main gmail_sync
