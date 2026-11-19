#!/usr/bin/python3
"""Draw the ZeroSend icon. Usage: icon.py OUT.png [SIZE]   (100x100 like the other APPLaunch icons)

A flat CardputerZero traced from M5Stack's front product photo (84 x 54 mm,
light grey body, LCD centred in the top half between two grooves, CARDPUTER
ZERO label and ESC key top left, power switch and TAB key top right, four rows
of 11 black keys under white legend strips with the coloured sym / shift /
fn / ctrl / alt legends) on LocalSend's teal, with a send arrow on its screen.
All positions are fractions of the body measured on that photo.
"""
import sys
from PIL import Image, ImageDraw

size = int(sys.argv[2]) if len(sys.argv) > 2 else 100
S = 8 * size
k = S / 100

im = Image.new('RGBA', (S, S), (0, 0, 0, 0))

# Background: LocalSend teal, lighter at the top.
top, bottom = (0x26, 0xa6, 0x9a), (0x00, 0x69, 0x5c)
gradient = Image.new('RGB', (1, S))
for y in range(S):
    t = y / (S - 1)
    gradient.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
gradient = gradient.resize((S, S))
mask = Image.new('L', (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle((0, 0, S - 1, S - 1), radius=22 * k, fill=255)
im.paste(gradient, (0, 0), mask)
d = ImageDraw.Draw(im)

# Body: 84 x 54 mm, centred.
BW, BH = 86, 86 * 54 / 84
BX, BY = (100 - BW) / 2, (100 - BH) / 2 + 1


def P(fx, fy):
    """A point given as fractions of the body (measured on the photo)."""
    return ((BX + fx * BW) * k, (BY + fy * BH) * k)


def R(fx0, fy0, fx1, fy1):
    return (*P(fx0, fy0), *P(fx1, fy1))


d.rounded_rectangle((BX * k + 1.0 * k, BY * k + 2.2 * k, (BX + BW) * k + 1.0 * k, (BY + BH) * k + 2.2 * k),
                    radius=6 * k, fill=(0, 55, 48, 120))                       # shadow
d.rounded_rectangle((BX * k, BY * k, (BX + BW) * k, (BY + BH) * k), radius=6 * k, fill='#c9c9c9')
d.rounded_rectangle((BX * k, BY * k, (BX + BW) * k, (BY + BH - 1.2) * k), radius=6 * k, fill='#d9d9d9')

# Grooves either side of the screen.
for fx in (0.197, 0.788):
    d.rounded_rectangle(R(fx, 0.03, fx + 0.016, 0.47), radius=0.6 * k, fill='#c4c4c4')

# LCD, centred: x 0.245-0.755, y 0.047-0.47 of the body, black.
d.rounded_rectangle(R(0.245, 0.047, 0.755, 0.470), radius=1.6 * k, fill='#000000')
# Send arrow on the screen in LocalSend teal.
teal, dim = '#82d5c8', '#3f7f77'
cx0, cy = P(0.40, 0.258)
cx1, _ = P(0.60, 0.258)
d.line((cx0, cy, cx1, cy), fill=teal, width=round(3.0 * k))
ax, _ = P(0.575, 0)
tip, _ = P(0.665, 0)
h = 5.8 * k
d.polygon([(ax, cy - h), (tip, cy), (ax, cy + h)], fill=teal)
for dy in (-4.2 * k, 4.2 * k):
    lx0, _ = P(0.315, 0)
    lx1, _ = P(0.37, 0)
    d.line((lx0, cy + dy, lx1, cy + dy), fill=dim, width=round(1.6 * k))

# Top left: CARDPUTER ZERO label (white with magenta corners), black ESC/HOME strip.
d.rectangle(R(0.015, 0.215, 0.180, 0.345), fill='#f4f4f4')
for x0, x1 in ((0.015, 0.032), (0.163, 0.180)):
    d.rectangle(R(x0, 0.215, x1, 0.235), fill='#c42891')
    d.rectangle(R(x0, 0.325, x1, 0.345), fill='#c42891')
d.rectangle(R(0.045, 0.262, 0.150, 0.300), fill='#222222')                     # "ZERO"
d.rectangle(R(0.015, 0.345, 0.180, 0.405), fill='#0a0a0a')
# Top right: white panel with the red-orange power switch, black LOCK/TAB strip.
d.rectangle(R(0.812, 0.030, 0.985, 0.345), fill='#f4f4f4')
d.rounded_rectangle(R(0.822, 0.055, 0.880, 0.150), radius=0.5 * k, fill='#c84a2a')
d.rounded_rectangle(R(0.885, 0.070, 0.945, 0.130), radius=1 * k, fill='#d6452e')
d.rectangle(R(0.812, 0.345, 0.985, 0.405), fill='#0a0a0a')

KEY = '#1c1c1c'
KW, KH = 0.056, 0.052                                                            # key width, height


def key(fx, fy):
    d.rounded_rectangle(R(fx - KW / 2, fy - KH / 2, fx + KW / 2, fy + KH / 2),
                        radius=1.3 * k, fill=KEY)


# ESC and TAB keys at the corners of the top row.
key(0.050, 0.452)
key(0.945, 0.452)

# Four rows of 11 keys under white legend strips.
cols = [0.050 + (0.945 - 0.050) * i / 10 for i in range(11)]
strips = [0.515, 0.638, 0.762, 0.884]
rows = [0.575, 0.695, 0.818, 0.940]
for sy in strips:
    d.rectangle(R(0.008, sy - 0.024, 0.992, sy + 0.024), fill='#f4f4f4')
for ry in rows:
    for cx in cols:
        key(cx, ry)
# Coloured legends: sym (blue), shift (purple), fn (orange), ctrl (yellow), alt (green).
legends = [(1, 0, '#31628f'), (2, 0, '#8e2aa8'), (3, 0, '#ee6a2a'), (3, 1, '#f5c518'), (3, 2, '#3fbf8b')]
for row, col, colour in legends:
    sy = strips[row]
    d.rectangle(R(cols[col] - 0.040, sy - 0.022, cols[col] + 0.040, sy + 0.022), fill=colour)

im.resize((size, size), Image.LANCZOS).save(sys.argv[1])
