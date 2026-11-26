"""Bouwt de scene-bestanden (compositions/stap*.html) en index.html uit de beat-lijst hieronder.
Draai: python3 build.py   (na prep_figs.py)"""
import json, pathlib
from PIL import Image

W, CARD_H = 756, 608  # binnenmaat van de figuurkaart
LEAD, TAIL = 0.5, 0.3

# (figuur, tekst, seconden, pan) ; pan = None (past in beeld) of (van, tot) als fractie van de scrollafstand
STEPS = [
    ("Klaarzetten", [
        ("p3_stekker", "Stekker in de machine. Dan in het stopcontact.", 4.5, None),
        ("p3_pedaal", "Pedaal op de grond. Trap erop: hoe harder, hoe sneller.", 4.5, None)]),
    ("Spoel opwinden", [
        ("p4_1", "Til de accessoirebox op en haal hem weg.", 4, None),
        ("p4_2", "Open het klepje.", 3.5, None),
        ("p4_3", "Pak het spoelhuis. Trek het eruit.", 4, None),
        ("p4_4", "Laat het lipje los. De spoel valt eruit.", 4, None),
        ("p5_1", "Zet de lege spoel op de spoelas.", 4, None),
        ("p5_2", "Duw de spoel naar rechts tot hij stopt.", 4, None),
        ("p5_3", "Pak het handwiel vast. Draai de knop naar je toe.", 4.5, None),
        ("p5_4", "Zet de klos op de garenpen.", 3.5, None),
        ("p6_top", "Draad langs het schijfje naar de spoel. Een paar rondjes.", 5, None),
        ("p3_pedaal", "Trap op het pedaal. De spoel wordt vol.", 4, None),
        ("p5_2", "Vol? Duw naar links, spoel eraf, knip de draad.", 5, None),
        ("p7_top", "Handwiel vast. Knop van je af. Wiel naar je toe tot het klikt.", 5, None)]),
    ("Spoel inleggen", [
        ("p7_a", "Spoel in het spoelhuis. De draad wijst van je af.", 5, None),
        ("p7_b", "Trek de draad door de gleuf, onder het veertje.", 5, None),
        ("p8_bot", "Houd het lipje open. Schuif het spoelhuis erop. Gat naar boven.", 5.5, None)]),
    ("Bovendraad", [
        ("p9_top", "Stroom uit. Naald en naaivoet omhoog.", 4.5, None),
        ("p9_top", "Eerst omlaag, dan omhoog door de hefboom.", 5, (0.0, 0.25)),
        ("p9_top", "Weer omlaag, achter de geleiders.", 5, (0.6, 0.95)),
        ("p9_bot", "Draad door het oog van de naald. Van voor naar achter.", 5, None)]),
    ("Onderdraad", [
        ("p10_1", "Houd de bovendraad vast. Draai het handwiel naar je toe.", 5, None),
        ("p10_1", "Maak een steek. De onderdraad komt mee omhoog.", 4.5, None),
        ("p10_2", "Leg beide draden naar achteren, onder de naaivoet.", 5, None),
        ("p10_3", "Klepje dicht. Klaar!", 3.5, None)]),
    ("Stof en steek", [
        ("p11_2", "Leg de stof onder de naaivoet.", 4, None),
        ("p11_4", "Zet de hendel omlaag.", 3.5, None),
        ("ph_panel", "Kies je steek met het wiel. E of D: rechte lijn. F: zigzag.", 5.5, None),
        ("p23_1", "Draai aan het wiel: hoe lang is de steek?", 5, None),
        ("p23_2", "Zigzag? Zet het wiel op F.", 3.5, None)]),
    ("Naaien", [
        ("p3_pedaal", "Trap op het pedaal en naai.", 3.5, None),
        ("ph_panel", "Duw het knopje met de pijl omlaag: achteruit naaien. Doe dat aan het begin en het eind.", 6, None),
        ("p13_3", "Draden in het gleufje. Trek naar beneden: knip!", 4.5, None),
        ("p13_2", "Hendel omhoog. Trek de stof naar achteren.", 4.5, None)]),
]

CSS = """
    #root { position: absolute; inset: 0; background: #f3eadb; font-family: Poppins, sans-serif; color: #2a2622; }
    #root .hd { position: absolute; left: 156px; top: 300px; width: 768px; }
    #root .chip { font-size: 30px; letter-spacing: 0.14em; font-weight: 700; color: #e4572e; }
    #root .ttl { font-size: 84px; line-height: 1.02; font-weight: 700; margin-top: 6px; }
    #root .card { position: absolute; left: 156px; top: 470px; width: 768px; height: 620px; border: 6px solid #2a2622; border-radius: 24px; background: #fff; overflow: hidden; }
    #root .fig { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; overflow: hidden; }
    #root .fig img { display: block; }
    #root .fig.fit img { max-width: 100%; max-height: 100%; }
    #root .fig.pan { align-items: flex-start; }
    #root .fig.pan img { width: 100%; height: auto; }
    #root .cap { position: absolute; left: 156px; top: 1110px; width: 768px; font-size: 48px; line-height: 1.2; font-weight: 700; }
"""

def scene(n, title, beats):
    parts, caps, js, t = [], [], [], LEAD
    for i, (fig, text, dur, pan) in enumerate(beats, 1):
        w, h = Image.open(f"assets/fig_{fig}.png").size
        cls = "pan" if pan else "fit"
        parts.append(f'    <div class="fig {cls} clip" id="s{n}-f{i}" data-start="{t}" data-duration="{dur}"><img id="s{n}-i{i}" src="assets/fig_{fig}.png" /></div>')
        caps.append(f'    <div class="cap clip" id="s{n}-c{i}" data-start="{t}" data-duration="{dur}">{text}</div>')
        js.append(f'tl.fromTo("#s{n}-i{i}", {{ opacity: 0, y: 24 }}, {{ opacity: 1, y: 0, duration: 0.4, ease: "power2.out" }}, {t});')
        if pan:
            rng = max(0, W * h / w - CARD_H)
            js.append(f'tl.fromTo("#s{n}-i{i}", {{ y: {-pan[0]*rng:.0f} }}, {{ y: {-pan[1]*rng:.0f}, duration: {dur - 0.8}, ease: "none" }}, {t + 0.4});')
        t += dur
    total = round(t + TAIL, 2)
    html = f"""<template>
  <style>{CSS}  </style>
  <div id="root" data-composition-id="stap{n}" data-width="1080" data-height="1920">
    <div class="hd"><div class="chip">STAP {n} VAN {len(STEPS)}</div><div class="ttl">{title}</div></div>
    <div class="card">
{chr(10).join(parts)}
    </div>
{chr(10).join(caps)}
  </div>
  <script>
    const tl = gsap.timeline({{ paused: true }});
    {chr(10).join("    " + l if k else l for k, l in enumerate(js))}
    window.__timelines["stap{n}"] = tl;
  </script>
</template>
"""
    pathlib.Path(f"compositions/stap{n}.html").write_text(html, encoding="utf8")
    return total

# volgorde: overzicht, stap 1, zoom spoelwinder, stap 2, stap 3, zoom draadroute, stap 4, 5, 6, 7
durs = [scene(n, t, b) for n, (t, b) in enumerate(STEPS, 1)]
order = [("overzicht", "overzicht", 7, None)]
zoom = {2: {"chip": "STAP 2 VAN 7", "title": "Spoelwinder", "cap": "Bovenop de machine, rechts", "cx": 775, "cy": 135, "w": 300},
        4: {"chip": "STAP 4 VAN 7", "title": "Draadroute", "cap": "Van de klos bovenop naar de naald links", "cx": 230, "cy": 320, "w": 480}}
for n, d in enumerate(durs, 1):
    if n in zoom:
        order.append((f"zoom{n}", "zoom", 4, zoom[n]))
    order.append((f"stap{n}", f"stap{n}", d, None))
t, hosts = 0.0, []
for hid, comp, dur, var in order:
    v = f" data-variable-values='{json.dumps(var, ensure_ascii=False)}'" if var else ""
    hosts.append(f'      <div id="{hid}" data-composition-id="{comp}" data-composition-src="compositions/{comp}.html"{v}\n        data-start="{round(t, 2)}" data-duration="{dur}" data-track-index="1" data-track-kind="graphics" data-width="1080" data-height="1920"></div>')
    t += dur
total = round(t, 2)
pathlib.Path("index.html").write_text(f"""<!doctype html>
<html lang="nl">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=1080, height=1920" />
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      html, body {{ margin: 0; width: 1080px; height: 1920px; overflow: hidden; background: #f3eadb; }}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="{total}" data-width="1080" data-height="1920">
{chr(10).join(hosts)}
    </div>
    <script>
      const tl = gsap.timeline({{ paused: true }});
      window.__timelines["main"] = tl;
      tl.seek(0);
    </script>
  </body>
</html>
""", encoding="utf8")
print("totaal", total, "s;", [(h[0], round(h[2], 1)) for h in order])
