# Werkafspraken (houd dit bestand kort, het wordt elke beurt meegestuurd)

## Antwoorden
- Nederlands, kort en direct. Geen inleiding, geen samenvatting achteraf.
- Alleen de uitkomst en wat de gebruiker moet beslissen of doen.
- Geen lange uitleg tenzij erom gevraagd wordt.

## Tokens besparen
- Lees alleen bestanden die nodig zijn. Gebruik Grep/Glob met een gerichte `offset`/`limit` in plaats van hele bestanden.
- Niet lezen of doorzoeken: `resource/`, `.git/`, `uv.lock`, `node_modules/`, grote video- en audiobestanden.
- Geen subagents voor kleine taken. Verbose werk (tests, logs) in een subagent met een klein model.
- Filter command-output (`| head`, `| tail`, `grep`) en print geen volledige logs.
- Eén taak per sessie. Bij wisselen van onderwerp: nieuwe sessie of `/clear`.

## Compact instructions
Bij `/compact`: bewaar alleen openstaande taken, genomen beslissingen en gewijzigde bestanden.
