"""Knipt de tekeningen uit de handleiding (pdf) en bewaart ze als assets/fig_*.png.
Gebruik: python3 prep_figs.py <handleiding.pdf>   (privegebruik: de handleiding is van Pfaff)"""
import subprocess, sys, tempfile, pathlib
from PIL import Image

PDF = sys.argv[1]
F = 3  # kijk-coordinaten (850x1100) naar 300 dpi pixels
COL = (14, 274)
FIGS = {  # naam: (pdf-pagina, y1, y2) of (pdf-pagina, y1, y2, x1, x2)
    "p3_stekker": (7, 20, 405), "p3_pedaal": (7, 575, 742),
    "p4_1": (8, 20, 208), "p4_2": (8, 216, 407), "p4_3": (8, 414, 605), "p4_4": (8, 612, 797),
    "p5_1": (9, 20, 208), "p5_2": (9, 216, 407), "p5_3": (9, 414, 605), "p5_4": (9, 612, 797),
    "p6_top": (10, 20, 208, 10, 548),
    "p7_top": (11, 20, 408), "p7_a": (11, 414, 590), "p7_b": (11, 590, 797),
    "p8_bot": (12, 414, 797),
    "p9_top": (13, 20, 604), "p9_bot": (13, 612, 797),
    "p10_1": (14, 20, 408), "p10_2": (14, 414, 605), "p10_3": (14, 612, 797),
    "p11_2": (15, 216, 407), "p11_4": (15, 612, 797),
    "p13_2": (17, 414, 605), "p13_3": (17, 612, 797),
    "p14_1": (18, 20, 512), "p15_1": (19, 20, 797),
    "p17_top": (21, 90, 190, 10, 548),
    "p23_1": (27, 20, 405), "p23_2": (27, 414, 607),
}
out = pathlib.Path("assets"); out.mkdir(exist_ok=True)
tmp = pathlib.Path(tempfile.mkdtemp())
for pg in sorted({v[0] for v in FIGS.values()}):
    subprocess.run(["pdftoppm", "-r", "300", "-f", str(pg), "-l", str(pg), "-png", PDF, str(tmp / f"pg{pg}")], check=True)
for name, v in FIGS.items():
    pg, y1, y2 = v[:3]; x1, x2 = (v[3], v[4]) if len(v) == 5 else COL
    img = Image.open(next(tmp.glob(f"pg{pg}-*.png")))
    img.crop((x1 * F, y1 * F, x2 * F, y2 * F)).save(out / f"fig_{name}.png")
print(len(FIGS), "figuren")
