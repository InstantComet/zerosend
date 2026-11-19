#!/usr/bin/python3
"""Draw the Beam icon (100x100, like the other APPLaunch icons). Usage: icon.py OUT.png [SIZE]"""
import sys
from PIL import Image, ImageDraw

size = int(sys.argv[2]) if len(sys.argv) > 2 else 100
S = 4 * size                      # draw big, scale down for smooth edges
k = S / 100
im = Image.new('RGBA', (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
d.rounded_rectangle((0, 0, S - 1, S - 1), radius=22 * k, fill='#0d1a22')
# Two devices facing each other, a beam of dots and an arrow between them.
blue, white = '#3cc8f0', '#ffffff'
d.rounded_rectangle((12 * k, 30 * k, 34 * k, 70 * k), radius=5 * k, outline=white, width=int(4 * k))
d.rounded_rectangle((66 * k, 30 * k, 88 * k, 70 * k), radius=5 * k, outline=white, width=int(4 * k))
for i, x in enumerate((41, 50)):
    r = 3.2 * k
    d.ellipse((x * k - r, 50 * k - r, x * k + r, 50 * k + r), fill=blue)
d.polygon(((56 * k, 41 * k), (64 * k, 50 * k), (56 * k, 59 * k)), fill=blue)
d.line((18 * k, 62 * k, 28 * k, 62 * k), fill=white, width=int(3 * k))
d.line((72 * k, 62 * k, 82 * k, 62 * k), fill=white, width=int(3 * k))
im.resize((size, size), Image.LANCZOS).save(sys.argv[1])
