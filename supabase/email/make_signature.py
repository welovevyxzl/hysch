"""Animerad namnteckning: Adian Hipsz skrivs ut, sedan ett svep och Hysch-pricken. Spelas en gång och stannar."""
import math
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

OUT = r"C:\Users\Adian Karol Hipsz\AppData\Local\Temp\claude\e481176e22946da9\70b285e0-9f63-42f9-b389-831bd5b25917\scratchpad\out"
import os; os.makedirs(OUT, exist_ok=True)

W, H = 660, 210                      # visas som 330x105 i mejlet (2x för skarp text)
INK = (22, 24, 29)
CORAL = (255, 90, 54)
BG = (255, 255, 255)
SS = 3                               # supersampling för mjuka kanter

font = ImageFont.truetype(r"C:\Windows\Fonts\segoesc.ttf", 80 * SS)
big = Image.new("L", (W * SS, H * SS), 0)
d = ImageDraw.Draw(big)
d.text((30 * SS, 36 * SS), "Adian Hipsz", font=font, fill=255)
big = big.rotate(2.5, resample=Image.BICUBIC, center=(W * SS // 2, H * SS // 2))   # lätt uppåtlutning, som en riktig namnteckning
alpha = np.asarray(big.resize((W, H), Image.LANCZOS), dtype=np.float32) / 255.0

ys, xs = np.nonzero(alpha > 0.1)
x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
print("ink bbox", x0, x1, y0, y1)

# tid -> kant: blyertsen rör sig i takt med hur mycket bläck som finns i kolumnerna (täta bokstäver tar längre tid)
col = alpha.sum(axis=0)
cum = np.cumsum(col); cum = (cum - cum[x0 - 1 if x0 > 0 else 0]) / (cum[x1] - cum[x0 - 1 if x0 > 0 else 0])
cum = np.clip(cum, 0, 1)
def edge_at(p):
    return float(np.searchsorted(cum, p)) if p < 1 else float(x1 + 40)

SHEAR = 0.35                          # kanten lutar lite, så skrivandet följer snedstrecken
yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
ycen = (y0 + y1) / 2

def ease(t):                          # mjuk start och slut
    return t * t * (3 - 2 * t)

def ink_frame(p, pen=True):
    edge = edge_at(p)
    dist = edge - (xx + SHEAR * (yy - ycen))
    reveal = np.clip(dist / 5.0 + 0.5, 0, 1)
    a = alpha * reveal
    img = np.empty((H, W, 3), np.float32)
    for c in range(3):
        img[..., c] = BG[c] * (1 - a) + INK[c] * a
    im = Image.fromarray(img.astype(np.uint8))
    if pen and 0 < p < 1:
        win = (np.abs(xx + SHEAR * (yy - ycen) - edge) < 7) & (alpha > 0.3)
        if win.any():
            py = float(yy[win].mean()); px = float(xx[win].mean())
            dr = ImageDraw.Draw(im, "RGBA")
            dr.ellipse((px - 6, py - 6, px + 6, py + 6), fill=CORAL + (60,))
            dr.ellipse((px - 3, py - 3, px + 3, py + 3), fill=CORAL + (255,))
    return im

# svepet under namnet: en kurva som ritas upp, spetsig i båda ändar
bx0, bx1, by = x0 + 20, x1 + 6, y1 + 14
def curve(u):
    sx, sy = bx0, by + 6
    ex, ey = bx1, by - 14
    cx, cy = (bx0 + bx1) / 2, by + 22
    x = (1 - u) ** 2 * sx + 2 * (1 - u) * u * cx + u ** 2 * ex
    y = (1 - u) ** 2 * sy + 2 * (1 - u) * u * cy + u ** 2 * ey
    return x, y
def swoosh(base, u):
    im = base.copy().convert("RGBA")
    layer = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    dr = ImageDraw.Draw(layer)
    n = 480
    for i in range(int(n * u) + 1):
        t = i / n
        x, y = curve(t)
        w = 0.6 + 3.2 * math.sin(math.pi * t) ** 0.8
        dr.ellipse(((x - w) * SS, (y - w) * SS, (x + w) * SS, (y + w) * SS), fill=CORAL + (255,))
    layer = layer.resize((W, H), Image.LANCZOS)
    im.alpha_composite(layer)
    return im.convert("RGB")
def dot(base, s):
    im = base.copy().convert("RGBA")
    layer = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    dr = ImageDraw.Draw(layer)
    cx, cy = bx1 + 18, by - 10
    r = 7.5 * s
    dr.ellipse(((cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS), fill=CORAL + (255,))
    layer = layer.resize((W, H), Image.LANCZOS)
    im.alpha_composite(layer)
    return im.convert("RGB")

frames, delays = [], []
blank = Image.new("RGB", (W, H), BG)
# 0: pennan vilar (det här är första bilden i program som inte spelar GIF, t.ex. Outlook)
rest = blank.copy(); dr = ImageDraw.Draw(rest, "RGBA"); dr.ellipse((x0 - 3, ycen - 3, x0 + 3, ycen + 3), fill=CORAL + (255,))
frames.append(rest); delays.append(450)
N = 40
for i in range(1, N + 1):
    frames.append(ink_frame(ease(i / N)) if i < N else ink_frame(1.0, pen=False)); delays.append(38)
final_ink = ink_frame(1.0, pen=False)
M = 14
for i in range(1, M + 1):
    frames.append(swoosh(final_ink, ease(i / M))); delays.append(30)
sw_full = swoosh(final_ink, 1.0)
for s in (0.5, 1.25, 1.0):
    frames.append(dot(sw_full, s)); delays.append(55)
final = dot(sw_full, 1.0)
frames.append(final); delays.append(7000)        # sista bilden stannar kvar

pal = [f.quantize(colors=48, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE) for f in frames]
pal[0].save(OUT + r"\signature.gif", save_all=True, append_images=pal[1:], duration=delays, optimize=True, disposal=1)
final.save(OUT + r"\signature_final.png")

sheet = Image.new("RGB", (W * 3, H * 2), BG)
picks = [14, 26, 38, 50, 57, len(frames) - 1]
for k, idx in enumerate(picks):
    sheet.paste(frames[idx], ((k % 3) * W, (k // 3) * H))
sheet.save(OUT + r"\signature_sheet.png")
print("frames", len(frames), "size KB", round(os.path.getsize(OUT + r"\signature.gif") / 1024), "total ms", sum(delays))
