#!/usr/bin/python3
"""Draw the ZeroSend icon. Usage: icon.py OUT.png [SIZE]   (100x100 like the other APPLaunch icons)

A CardputerZero seen from the front (grey body, LCD on top, four rows of round
keys with the coloured sym/shift/fn/ctrl/alt labels, red power switch) on
LocalSend's teal, with a send arrow on its screen.
"""
import sys
from PIL import Image, ImageDraw

size = int(sys.argv[2]) if len(sys.argv) > 2 else 100
S = 8 * size                      # draw big, scale down for smooth edges
k = S / 100


def box(x0, y0, x1, y1):
    return (x0 * k, y0 * k, x1 * k, y1 * k)


im = Image.new('RGBA', (S, S), (0, 0, 0, 0))

# Background: LocalSend teal, light at the top.
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

# Body (84 x 54 mm -> 80 x 51), with a darker lower edge for depth.
x0, y0, x1, y1 = 10, 26, 90, 77
d.rounded_rectangle(box(x0 + 1, y0 + 3, x1 + 1, y1 + 3), radius=7 * k, fill=(0, 60, 52, 110))   # shadow
d.rounded_rectangle(box(x0, y0 + 2, x1, y1), radius=7 * k, fill='#8e9497')
d.rounded_rectangle(box(x0, y0, x1, y1 - 2), radius=7 * k, fill='#e3e6e7')

# LCD, top centre, black with a thin bezel.
sx0, sy0, sx1, sy1 = 28, 30, 66, 50
d.rounded_rectangle(box(sx0 - 1, sy0 - 1, sx1 + 1, sy1 + 1), radius=3 * k, fill='#2b2f31')
d.rounded_rectangle(box(sx0, sy0, sx1, sy1), radius=2 * k, fill='#0e1513')
# Send arrow on the screen: a teal arrow leaving to the right, with motion lines.
teal = '#82d5c8'
cy = (sy0 + sy1) / 2
d.line(box(40, cy, 55, cy), fill=teal, width=round(3.2 * k))
d.polygon([(53 * k, (cy - 6) * k), (61 * k, cy * k), (53 * k, (cy + 6) * k)], fill=teal)
for dy, x in ((-4.5, 33), (4.5, 33)):
    d.line(box(x, cy + dy, x + 5, cy + dy), fill='#4f9c92', width=round(1.8 * k))

# Red power switch top right, "ZERO" label strip top left.
d.rounded_rectangle(box(73, 30, 84, 35), radius=1.5 * k, fill='#e5483b')
d.rounded_rectangle(box(15, 31, 24, 34), radius=1 * k, fill='#f5c518')

# Keyboard: 4 rows x 10 round dark keys.
key = '#4a5053'
cols, rows = 10, 4
kx0, kx1, ky0, ky1 = 17.5, 82.5, 56, 72
for r in range(rows):
    for c in range(cols):
        cx = kx0 + (kx1 - kx0) * c / (cols - 1)
        cyk = ky0 + (ky1 - ky0) * r / (rows - 1)
        d.ellipse(box(cx - 2.4, cyk - 2.1, cx + 2.4, cyk + 2.1), fill=key)
# Coloured modifier labels on the left of rows 2-4, like the real keycaps' legends.
labels = [(1, 0, '#9b59b6'), (2, 0, '#ec5f9a'), (3, 0, '#f39c12'), (3, 1, '#f5c518'), (3, 2, '#3fbf6b')]
for r, c, colour in labels:
    cx = kx0 + (kx1 - kx0) * c / (cols - 1)
    cyk = ky0 + (ky1 - ky0) * r / (rows - 1)
    d.rounded_rectangle(box(cx - 3.1, cyk - 1.3, cx + 3.1, cyk + 1.3), radius=1 * k, fill=colour)

im.resize((size, size), Image.LANCZOS).save(sys.argv[1])
