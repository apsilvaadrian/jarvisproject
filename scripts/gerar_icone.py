"""Gera assets/jarvis.ico: um reator ciano, no mesmo estilo do núcleo da interface."""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 512
C = S / 2
CYAN = (46, 230, 255)


def ring(d: ImageDraw.ImageDraw, r: float, w: int, color, start=0, end=360):
    d.arc([C - r, C - r, C + r, C + r], start, end, fill=color, width=w)


img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
g = ImageDraw.Draw(glow)
g.ellipse([C - 230, C - 230, C + 230, C + 230], fill=(4, 18, 30, 255))
for r, a in ((150, 90), (110, 140), (70, 220)):
    g.ellipse([C - r, C - r, C + r, C + r], fill=(*CYAN, a // 3))
glow = glow.filter(ImageFilter.GaussianBlur(18))
img.alpha_composite(glow)

d = ImageDraw.Draw(img)
ring(d, 225, 10, (*CYAN, 255))
for i in range(12):                                   # anel segmentado
    a0 = i * 30 + 4
    ring(d, 190, 22, (*CYAN, 255 if i % 3 == 0 else 120), a0, a0 + 22)
for i in range(10):                                   # bobinas do reator
    a0 = math.radians(i * 36 + 5)
    a1 = math.radians(i * 36 + 31)
    pts = [(C + 145 * math.cos(a0), C + 145 * math.sin(a0)), (C + 145 * math.cos(a1), C + 145 * math.sin(a1)),
           (C + 95 * math.cos(a1), C + 95 * math.sin(a1)), (C + 95 * math.cos(a0), C + 95 * math.sin(a0))]
    d.polygon(pts, fill=(*CYAN, 90), outline=(*CYAN, 255))
tri = [(C + 80 * math.cos(math.radians(a)), C + 80 * math.sin(math.radians(a))) for a in (-90, 30, 150)]
d.polygon(tri, outline=(*CYAN, 255), width=8)
core = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(core).ellipse([C - 55, C - 55, C + 55, C + 55], fill=(220, 250, 255, 255))
img.alpha_composite(core.filter(ImageFilter.GaussianBlur(10)))

out = Path(__file__).resolve().parents[1] / "assets" / "jarvis.ico"
out.parent.mkdir(exist_ok=True)
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.resize((256, 256), Image.LANCZOS).save(out.with_suffix(".png"))
print(f"Ícone salvo em {out}")
