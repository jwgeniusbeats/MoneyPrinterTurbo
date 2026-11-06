---
name: watch
description: Bekijk en beluister een video-link (Instagram reel/post, of andere site) en review hem. Gebruik als de gebruiker een link stuurt om te checken, of /watch <link>.
---

# /watch <link>

1. Eenmalig: `pip install -q faster-whisper yt-dlp` (ffmpeg moet er zijn).
2. Run: `python3 .claude/skills/watch/watch.py "<link>" <scratchpad>/watch-<id>`
3. Lees `caption.txt` en `transcript.txt`; bekijk `frames.jpg` met Read (tekst op scherm, namen, cijfers).
   Transcript kan namen verkeerd horen: controleer ze tegen de beelden.
4. Antwoord in vaste vorm, kort:
   - **Claim:** wat wordt beweerd of verkocht.
   - **Bewijs:** hoe sterk, wat ontbreekt (verificatie, steekproef, lead-magnet?).
   - **Past bij ons?** MoneyPrinterTurbo = faceless video-automatisering; link naar `automation/`.
   - **Al geprobeerd?** grep gericht in `automation/*.md` en `git log --grep`.
   - **Beslissing voor de gebruiker.**
5. Mislukt het (privé, 429, fotopost)? Zeg dat en vraag de gebruiker de video te uploaden.
