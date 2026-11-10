"""Hysch-mejlets hjältebild: korall, stor rubrik och en röstvåg som rör sig. Loopar sömlöst, och första bilden
är en färdig affisch, så program som inte spelar GIF (Outlook) visar ändå något snyggt.
Kör: python make_hero.py  ->  hero.gif och hero_poster.png bredvid skriptet."""
import math, os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
CORAL, INK, WHITE = (255, 90, 54), (22, 24, 29), (255, 255, 255)
W, H, SS = 1120, 548, 2            # visas som 560x274
N, MS = 40, 50                     # 40 bilder à 50 ms = 2 s, sömlöst

def font(size, wght=650):
    f = ImageFont.truetype(r"C:\Windows\Fonts\SegUIVar.ttf", size)
    axes = f.get_variation_axes()
    f.set_variation_by_axes([wght if a.get("name") in (b"Weight", "Weight") else a["default"] for a in axes])
    return f

def tracked(d, xy, text, f, fill, track):
    x, y = xy
    for i, ch in enumerate(text):
        d.text((x, y), ch, font=f, fill=fill)
        adv = f.getlength(text[i:i + 2]) - f.getlength(text[i + 1]) if i + 1 < len(text) else f.getlength(ch)
        x += adv + track

mark = Image.open(os.path.join(HERE, "..", "..", "assets", "icon-1024.png")).convert("RGBA").resize((88 * SS, 88 * SS), Image.LANCZOS)

# stilla delar ritas en gång (i dubbel storlek för mjuka kanter)
base = Image.new("RGB", (W * SS, H * SS), CORAL)
d = ImageDraw.Draw(base)
base.paste(mark, (64 * SS, 52 * SS), mark)
tracked(d, (172 * SS, 62 * SS), "hysch.", font(46 * SS, 700), INK, -1.5 * SS)
big = font(150 * SS, 650)
tracked(d, (60 * SS, 178 * SS), "Här är", big, WHITE, -7 * SS)
tracked(d, (60 * SS, 322 * SS), "din kod.", big, INK, -7 * SS)

# röstvågen: tolv kraftiga staplar till höger, speglade runt mitten
BARS = 12
bw, gap = 20, 13
x0 = W - 64 - (BARS * bw + (BARS - 1) * gap)
cy = 300
env = [0.38, 0.55, 0.72, 0.88, 1.0, 0.9, 0.78, 0.94, 0.7, 0.55, 0.42, 0.3]
spin = [1, 2, 1, 1, 2, 1, 2, 1, 1, 2, 1, 2]                     # heltal = sömlös loop
phase = [0.0, 1.1, 2.3, 0.7, 3.0, 1.9, 0.4, 2.6, 1.3, 3.5, 0.9, 2.0]

def frame(t):
    im = base.copy()
    dr = ImageDraw.Draw(im)
    for i in range(BARS):
        a = abs(math.sin(math.pi * (spin[i] * t / N) + phase[i]))
        h = (46 + 300 * env[i] * (0.22 + 0.78 * a)) * SS
        x = (x0 + i * (bw + gap)) * SS
        dr.rounded_rectangle((x, cy * SS - h / 2, x + bw * SS, cy * SS + h / 2), radius=bw * SS // 2, fill=INK)
    return im.resize((W, H), Image.LANCZOS)

frames = [frame(t) for t in range(N)]
frames[0].save(os.path.join(HERE, "hero_poster.png"), optimize=True)
pal = [f.quantize(colors=16, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE) for f in frames]
pal[0].save(os.path.join(HERE, "hero.gif"), save_all=True, append_images=pal[1:], duration=MS, loop=0, optimize=True, disposal=1)
print("hero.gif", round(os.path.getsize(os.path.join(HERE, "hero.gif")) / 1024), "KB,", N, "bilder")
