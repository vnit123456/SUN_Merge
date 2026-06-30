"""Generate a sun icon (sun.ico + sun.png) for SUN Merge."""
import math
from PIL import Image, ImageDraw

SZ = 256
img = Image.new("RGBA", (SZ, SZ), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
cx = cy = SZ / 2

CORE = (255, 153, 0, 255)     # orange core
CORE_HI = (255, 200, 60, 255)  # lighter centre
RAY = (255, 170, 20, 255)     # ray colour

# --- rays: 12 tapered triangles around the centre ---
n_rays = 12
r_in = 78      # ray base radius
r_out = 120    # ray tip radius
half = math.radians(8)   # half-width of each ray base
for k in range(n_rays):
    a = math.radians(k * 360 / n_rays)
    tip = (cx + r_out * math.cos(a), cy + r_out * math.sin(a))
    b1 = (cx + r_in * math.cos(a - half), cy + r_in * math.sin(a - half))
    b2 = (cx + r_in * math.cos(a + half), cy + r_in * math.sin(a + half))
    d.polygon([tip, b1, b2], fill=RAY)

# --- core disc with a soft lighter centre ---
r = 70
d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=CORE)
r2 = 46
d.ellipse([cx - r2, cy - r2, cx + r2, cy + r2], fill=CORE_HI)

img.save("sun.png")
img.save("sun.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                           (64, 64), (128, 128), (256, 256)])
print("Wrote sun.png and sun.ico")
