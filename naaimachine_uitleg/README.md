# Uitlegfilmpje PFAFF varimatic 6085 (voor privegebruik)

Staand (1080x1920), Nederlands, zonder stem, ca. 2 min 53 s, 7 stappen: klaarzetten, spoel opwinden,
spoel inleggen, bovendraad, onderdraad omhoog, stof en steek, naaien. Gebouwd met hyperframes (HTML naar mp4).
Tempo en opzet zijn goedgekeurd door de gebruiker. Niet openbaar online zetten: de tekeningen komen uit de
Pfaff-handleiding. De repo is publiek, daarom staan `assets/`, `renders/` en `snapshots/` bewust NIET in git.

## Hervatten (ca. 5 min + 4 min renderen)
1. Zet deze branch klaar: `git checkout claude/naaimachine-uitleg`.
2. Vraag de gebruiker om opnieuw te uploaden:
   - de handleiding-pdf (`Pfaff_varimatic_6085_Sewing_Machine.pdf`, 73 blz., manuall.co.uk),
   - foto 1: voorkant van de machine (IMG_6488) -> `assets/machine.jpg` (voor overzicht en inzoomen),
   - foto 2: close-up van het paneel met stekentabel (IMG_6489) -> `assets/fig_ph_panel.png` (max 1500 px breed).
3. In `naaimachine_uitleg/`:
   - `python3 -I prep_figs.py <pad-naar-pdf>`  (knipt 28 tekeningen naar `assets/fig_*.png`, vereist `pdftoppm` en Pillow)
   - `python3 -I build.py`  (schrijft `compositions/stap1..7.html` en `index.html` uit de beat-lijst in `build.py`)
   - `npx --yes hyperframes@0.8.76 check` en daarna `npx --yes hyperframes@0.8.76 render` (Node 22; versie is vastgepind)
4. Teksten of volgorde wijzigen: pas `STEPS` in `build.py` aan en draai `build.py` opnieuw. Bewerk de gegenereerde HTML niet met de hand.

## Open punten
- Spoelhuis: plaats op de foto is een gok (klepje zit achter de accessoirebox). Foto's nodig: (1) accessoirebox eraf, (2) klepje open met spoelhuis.
- Stap 5: handleiding zegt NL "naar links", EN/DE/FR "naar rechts/achteren" voor de draden onder de voet; film zegt "naar achteren". Controleren op de machine.
- Bovendraad: de hoge tekening schuift in beeld; draadroute is lastig te volgen. Eventueel eigen schema of markering toevoegen.
- Optioneel: in twee filmpjes splitsen (stap 1-4 en 5-7).
- De getekende zoom-scene "Spoelwinder"/"Draadroute" gebruikt coordinaten op `machine.jpg` (zie `zoom`-variabelen in `build.py`).
