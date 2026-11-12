#!/bin/bash
# One paper-desk cycle. The API key lives outside the repo, in ~/.paper_desk_env (one line: export ANTHROPIC_API_KEY=...).
cd "$(dirname "$0")/../.." || exit 1
[ -f "$HOME/.paper_desk_env" ] && . "$HOME/.paper_desk_env"
exec /usr/bin/python3 -m automation.paper_desk.desk run
