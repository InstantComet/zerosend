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


BODY_R = 2.0 * k        # the case's corners are tight (about 1.5% of its width)
d.rounded_rectangle((BX * k + 1.0 * k, BY * k + 2.2 * k, (BX + BW) * k + 1.0 * k, (BY + BH) * k + 2.2 * k),
                    radius=BODY_R, fill=(0, 55, 48, 120))                      # shadow
d.rounded_rectangle((BX * k, BY * k, (BX + BW) * k, (BY + BH) * k), radius=BODY_R, fill='#c9c9c9')
d.rounded_rectangle((BX * k, BY * k, (BX + BW) * k, (BY + BH - 1.0) * k), radius=BODY_R, fill='#d9d9d9')

# Embossed "M5" top left (a shade darker than the case).
GROOVE = '#c8c8c8'
EMBOSS = '#cecece'
for x0, y0, x1, y1 in ((0.031, 0.048, 0.046, 0.172), (0.082, 0.048, 0.097, 0.172),     # M legs
                       (0.112, 0.048, 0.166, 0.068), (0.112, 0.048, 0.127, 0.110),     # 5 top, upright
                       (0.112, 0.100, 0.166, 0.118), (0.150, 0.100, 0.166, 0.172),     # 5 middle, right
                       (0.112, 0.155, 0.166, 0.172)):                                  # 5 bottom
    d.rectangle(R(x0, y0, x1, y1), fill=EMBOSS)
d.line((*P(0.038, 0.055), *P(0.064, 0.130), *P(0.090, 0.055)), fill=EMBOSS, width=round(1.0 * k))

# Grooves either side of the screen, and one at the right edge above the power panel.
for x0, x1, y1 in ((0.196, 0.219, 0.453), (0.778, 0.801, 0.453), (0.963, 0.988, 0.170)):
    d.rounded_rectangle(R(x0, 0.028, x1, y1), radius=0.6 * k, fill=GROOVE)

# LCD, centred: x 0.245-0.755, y 0.05-0.47 of the body, black.
d.rounded_rectangle(R(0.245, 0.050, 0.755, 0.470), radius=1.4 * k, fill='#000000')
# Send arrow on the screen in LocalSend teal.
teal, dim = '#82d5c8', '#3f7f77'
cx0, cy = P(0.40, 0.26)
cx1, _ = P(0.60, 0.26)
d.line((cx0, cy, cx1, cy), fill=teal, width=round(3.0 * k))
ax, _ = P(0.575, 0)
tip, _ = P(0.665, 0)
h = 5.8 * k
d.polygon([(ax, cy - h), (tip, cy), (ax, cy + h)], fill=teal)
for dy in (-4.2 * k, 4.2 * k):
    lx0, _ = P(0.315, 0)
    lx1, _ = P(0.37, 0)
    d.line((lx0, cy + dy, lx1, cy + dy), fill=dim, width=round(1.6 * k))

LABEL, INK, CASE = '#f2f2f2', '#0a0a0a', '#d9d9d9'


def screw(fx, fy):
    x, y = P(fx, fy)
    r = 0.022 * BW * k
    d.ellipse((x - r, y - r, x + r, y + r), fill='#9a9a9a')
    d.ellipse((x - r * 0.72, y - r * 0.72, x + r * 0.72, y + r * 0.72), fill='#c4c4c4')


# Top left: CARDPUTER ZERO label (magenta corner brackets), black ESC/HOME strip, ESC key, screw.
d.rounded_rectangle(R(0.011, 0.212, 0.184, 0.415), radius=1.2 * k, fill=LABEL)
d.ellipse(R(0.128, 0.392, 0.200, 0.505), fill=CASE)                            # notch above the screw
d.rounded_rectangle(R(0.011, 0.352, 0.114, 0.418), radius=1.0 * k, fill=INK)
d.rectangle(R(0.011, 0.352, 0.114, 0.380), fill=INK)
d.rectangle(R(0.027, 0.244, 0.167, 0.262), fill='#2a2a2a')                    # CARDPUTER
d.rectangle(R(0.027, 0.280, 0.167, 0.326), fill='#111111')                    # ZERO
for x, sx in ((0.018, 1), (0.176, -1)):
    for y, sy in ((0.224, 1), (0.342, -1)):
        d.line((*P(x, y + sy * 0.03), *P(x, y), *P(x + sx * 0.014, y)), fill='#c42891', width=round(0.7 * k))
d.rounded_rectangle(R(0.100, 0.430, 0.108, 0.479), radius=0.4 * k, fill=INK)  # small slot
screw(0.152, 0.444)

# Top right: label with the power switch (upper part leaves room for the edge groove,
# lower part curves round the screw), black LOCK/TAB strip.
d.rounded_rectangle(R(0.815, 0.019, 0.954, 0.205), radius=1.2 * k, fill=LABEL)
d.rounded_rectangle(R(0.815, 0.180, 0.984, 0.360), radius=1.2 * k, fill=LABEL)
d.ellipse(R(0.790, 0.170, 0.890, 0.345), fill=CASE)                            # notch round the screw
d.rounded_rectangle(R(0.815, 0.349, 0.984, 0.420), radius=1.0 * k, fill=INK)
d.rectangle(R(0.815, 0.349, 0.984, 0.380), fill=INK)
d.rounded_rectangle(R(0.825, 0.057, 0.877, 0.127), radius=0.6 * k, fill='#b8401f')   # slide switch
d.rounded_rectangle(R(0.884, 0.061, 0.945, 0.116), radius=2.5 * k, fill='#d6452e')   # "ON" badge
d.ellipse(R(0.912, 0.067, 0.940, 0.110), fill='#ffffff')
d.rectangle(R(0.828, 0.140, 0.936, 0.168), fill='#151515')                    # POWER
screw(0.833, 0.257)

KEY = '#1c1c1c'
KW, KH = 0.057, 0.051                                                            # key width, height


def key(fx, fy):
    d.rounded_rectangle(R(fx - KW / 2, fy - KH / 2, fx + KW / 2, fy + KH / 2),
                        radius=1.3 * k, fill=KEY)


# ESC and TAB keys at the corners of the top row.
key(0.050, 0.454)
key(0.945, 0.454)

# Four rows of 11 keys under white legend strips.
cols = [0.0515 + (0.9455 - 0.0515) * i / 10 for i in range(11)]
strips = [0.518, 0.640, 0.760, 0.880]
rows = [0.577, 0.697, 0.817, 0.938]
for sy in strips:
    d.rectangle(R(0.010, sy - 0.023, 0.987, sy + 0.023), fill=LABEL)
for ry in rows:
    for cx in cols:
        key(cx, ry)
# Coloured legends: sym (blue), shift (purple), fn (orange), ctrl (yellow), alt (green).
legends = [(1, 0, '#31628f'), (2, 0, '#8e2aa8'), (3, 0, '#ee6a2a'), (3, 1, '#f5c518'), (3, 2, '#3fbf8b')]
for row, col, colour in legends:
    sy = strips[row]
    d.rectangle(R(cols[col] - 0.040, sy - 0.022, cols[col] + 0.040, sy + 0.022), fill=colour)

im.resize((size, size), Image.LANCZOS).save(sys.argv[1])
