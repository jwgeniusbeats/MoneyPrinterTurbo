#!/bin/bash
# Haalt de nieuwste levering van branch main-b0gds1 veilig binnen op de Mac, zonder dat staged bestanden of
# lokale commits kwijtraken. Bij een conflict stopt het en laat alles ongemoeid (merge --abort).
#   bash automation/sync_deliveries.sh
set -u
cd "$(dirname "$0")/.." || exit 1
BR=main-b0gds1

git fetch origin "$BR" || { echo "FOUT: fetch mislukt"; exit 1; }

if ! git diff --cached --quiet; then
  echo "Staged bestanden (eerdere levering) worden eerst vastgelegd..."
  git commit -m "Leveringen vastgelegd (sync_deliveries)" || exit 1
fi
if ! git diff --quiet; then
  echo "Er zijn niet-vastgelegde wijzigingen in bestaande bestanden; leg die eerst vast of stash ze. Gestopt."
  git status --short
  exit 1
fi

if ! git merge --no-edit "origin/$BR"; then
  echo "CONFLICT: merge afgebroken, niets veranderd. Stuur de bestandsnamen hieronder naar de chat."
  git diff --name-only --diff-filter=U
  git merge --abort
  exit 1
fi

echo; echo "Controle (elke regel moet een aantal > 0 tonen):"
chk() { printf '  %-45s %s\n' "$1 in $2" "$(grep -c "$1" "$2" 2>/dev/null)"; }
chk FEED_COMMIT_ENV automation/export_status.py
chk script_is_complete app/services/llm.py
chk verify_task_video automation/daily_pipeline.py
chk INSTAGRAM_MODE automation/daily_pipeline.py
chk experiments automation/daily_pipeline.py
chk DISABLED_PLATFORMS automation/daily_pipeline.py
echo "Klaar. Draai daarna: uv run python -m unittest test.services.test_experiments"
