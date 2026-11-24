#!/usr/bin/env python3
"""Holographic collectible card: the GitHub profile README graphics for oshada101.

Regenerates every SVG in assets/ from data/*.json and the TTFs in fonts/, and keeps the XP
figure in README.md in step. Python 3 standard library only:

    python3 scripts/build.py            # rebuild from the data on disk
    python3 scripts/build.py --fetch    # refresh data/ from GitHub first (the daily Action does this)

Only public data is read: the contribution calendar as the public profile shows it, and
commit counts for the public repos named on the card.

Text is set as outlined glyphs (read straight from the TTFs, with GPOS kerning), so no
SVG depends on a web font loading inside an <img>.
"""
import argparse
import json
import math
import os
import random
import re
import struct
import subprocess
import sys
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGIN = "oshada101"
DATA = ROOT / "data" / "contributions.json"
REPOS = ROOT / "data" / "repos.json"
FONTS = ROOT / "fonts"
ASSETS = ROOT / "assets"
README = ROOT / "README.md"

# ----------------------------------------------------------------- palette
BONE = "#efe9dd"     # print
BONE2 = "#b4ada1"
BONE3 = "#7b756c"
PANEL = "#0e0e13"
# Iridescent spectrum. It is colour-dodged onto the metal and screened onto the etched
# lines, then multiplied by a moving light, so on the card it reads as foil, not a rainbow.
SPECTRUM = ["#ff6b9a", "#ffb86b", "#e8f27a", "#6ff2c2", "#62c8ff", "#8c7bff"]


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def f(v):
    s = ("%.1f" % v).rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def stops(colors, n_periods):
    total = n_periods * len(colors)
    return "".join('<stop offset="%s%%" stop-color="%s"/>' % (f(100.0 * k / total), colors[k % len(colors)])
                   for k in range(total + 1))


def load_counts():
    return [(date.fromisoformat(d), c) for d, c in json.loads(DATA.read_text())]


# ================================================================= TrueType: outlines, metrics, kerning
def u16(d, o): return struct.unpack_from(">H", d, o)[0]
def s16(d, o): return struct.unpack_from(">h", d, o)[0]
def u32(d, o): return struct.unpack_from(">I", d, o)[0]


class Font:
    def __init__(self, path, key):
        d = Path(path).read_bytes()
        self.d, self.key, self.t, self.paths = d, key, {}, {}
        for i in range(u16(d, 4)):
            r = 12 + 16 * i
            self.t[d[r:r + 4].decode("latin1")] = (u32(d, r + 8), u32(d, r + 12))
        head = self.t["head"][0]
        self.upm = u16(d, head + 18)
        long_loca = s16(d, head + 50)
        self.ng = u16(d, self.t["maxp"][0] + 4)
        nhm = u16(d, self.t["hhea"][0] + 34)
        hm = self.t["hmtx"][0]
        self.adv = [u16(d, hm + 4 * min(i, nhm - 1)) for i in range(self.ng)]
        lo = self.t["loca"][0]
        self.loca = ([u32(d, lo + 4 * i) for i in range(self.ng + 1)] if long_loca
                     else [u16(d, lo + 2 * i) * 2 for i in range(self.ng + 1)])
        self.cmap = self._cmap()
        self.kern = self._kern() if "GPOS" in self.t else {}

    def _cmap(self):
        d, base = self.d, self.t["cmap"][0]
        best = None
        for i in range(u16(d, base + 2)):
            pid, off = u16(d, base + 4 + 8 * i), u32(d, base + 8 + 8 * i)
            fmt = u16(d, base + off)
            if fmt in (4, 12) and pid in (0, 3) and (best is None or fmt == 12):
                best = (fmt, base + off)
        fmt, o = best
        m = {}
        if fmt == 4:
            seg = u16(d, o + 6)
            ends, starts = o + 14, o + 16 + seg
            deltas, ranges = starts + seg, starts + 2 * seg
            for s in range(seg // 2):
                e, st = u16(d, ends + 2 * s), u16(d, starts + 2 * s)
                dl, ro = s16(d, deltas + 2 * s), u16(d, ranges + 2 * s)
                for c in range(st, min(e, 0xFFFE) + 1):
                    if ro == 0:
                        g = (c + dl) & 0xFFFF
                    else:
                        g = u16(d, ranges + 2 * s + ro + 2 * (c - st))
                        g = (g + dl) & 0xFFFF if g else 0
                    if g:
                        m[c] = g
        else:
            for k in range(u32(d, o + 12)):
                st, e, g0 = u32(d, o + 16 + 12 * k), u32(d, o + 20 + 12 * k), u32(d, o + 24 + 12 * k)
                for c in range(st, e + 1):
                    m[c] = g0 + c - st
        return m

    def _coverage(self, o):
        d = self.d
        if u16(d, o) == 1:
            return [u16(d, o + 4 + 2 * i) for i in range(u16(d, o + 2))]
        out = []
        for i in range(u16(d, o + 2)):
            out.extend(range(u16(d, o + 4 + 6 * i), u16(d, o + 6 + 6 * i) + 1))
        return out

    def _classdef(self, o):
        d, m = self.d, {}
        if u16(d, o) == 1:
            st = u16(d, o + 2)
            for i in range(u16(d, o + 4)):
                m[st + i] = u16(d, o + 6 + 2 * i)
        else:
            for i in range(u16(d, o + 2)):
                for g in range(u16(d, o + 4 + 6 * i), u16(d, o + 6 + 6 * i) + 1):
                    m[g] = u16(d, o + 8 + 6 * i)
        return m

    def _kern(self):
        """x-advance adjustments from the GPOS 'kern' feature (PairPos formats 1 and 2)."""
        d = self.d
        g = self.t["GPOS"][0]
        fl, ll = g + u16(d, g + 6), g + u16(d, g + 8)
        lookups = []
        for i in range(u16(d, fl)):
            if d[fl + 2 + 6 * i: fl + 6 + 6 * i] == b"kern":
                fo = fl + u16(d, fl + 6 + 6 * i)
                lookups += [u16(d, fo + 4 + 2 * j) for j in range(u16(d, fo + 2))]
        vsize = lambda vf: 2 * bin(vf & 0xFF).count("1")
        xadv = lambda o, vf: s16(d, o + 2 * bin(vf & 3).count("1")) if vf & 4 else 0
        pairs = {}
        for li in sorted(set(lookups)):
            lo = ll + u16(d, ll + 2 + 2 * li)
            for k in range(u16(d, lo + 4)):
                so, t = lo + u16(d, lo + 6 + 2 * k), u16(d, lo)
                if t == 9:
                    t, so = u16(d, so + 2), so + u32(d, so + 4)
                if t != 2:
                    continue
                cov = self._coverage(so + u16(d, so + 2))
                vf1, vf2 = u16(d, so + 4), u16(d, so + 6)
                step = vsize(vf1) + vsize(vf2)
                if u16(d, so) == 1:
                    for idx, g1 in enumerate(cov):
                        ps = so + u16(d, so + 10 + 2 * idx)
                        for r in range(u16(d, ps)):
                            rec = ps + 2 + r * (2 + step)
                            v = xadv(rec + 2, vf1)
                            if v:
                                pairs.setdefault((g1, u16(d, rec)), v)
                else:
                    cd1, cd2 = self._classdef(so + u16(d, so + 8)), self._classdef(so + u16(d, so + 10))
                    n1, n2 = u16(d, so + 12), u16(d, so + 14)
                    by2 = {}
                    for g2 in range(self.ng):
                        by2.setdefault(cd2.get(g2, 0), []).append(g2)
                    for g1 in cov:
                        c1 = cd1.get(g1, 0)
                        for c2 in range(n2):
                            v = xadv(so + 16 + (c1 * n2 + c2) * step, vf1)
                            if v:
                                for g2 in by2.get(c2, []):
                                    pairs.setdefault((g1, g2), v)
        return pairs

    def _contours(self, gid):
        d = self.d
        o = self.t["glyf"][0] + self.loca[gid]
        if self.loca[gid] == self.loca[gid + 1]:
            return []
        nc = s16(d, o)
        if nc >= 0:
            ends = [u16(d, o + 10 + 2 * i) for i in range(nc)]
            p = o + 12 + 2 * nc + u16(d, o + 10 + 2 * nc)
            npts = ends[-1] + 1 if ends else 0
            flags = []
            while len(flags) < npts:
                fl = d[p]
                p += 1
                flags.append(fl)
                if fl & 8:
                    flags.extend([fl] * d[p])
                    p += 1
            coords = []
            for short, same in ((2, 16), (4, 32)):
                v, vals = 0, []
                for fl in flags[:npts]:
                    if fl & short:
                        v += d[p] if fl & same else -d[p]
                        p += 1
                    elif not fl & same:
                        v += s16(d, p)
                        p += 2
                    vals.append(v)
                coords.append(vals)
            xs, ys = coords
            out, st = [], 0
            for en in ends:
                out.append([(xs[i], ys[i], bool(flags[i] & 1)) for i in range(st, en + 1)])
                st = en + 1
            return out
        out, p = [], o + 10
        while True:
            fl, gi = u16(d, p), u16(d, p + 2)
            p += 4
            if fl & 1:
                dx, dy = s16(d, p), s16(d, p + 2)
                p += 4
            else:
                dx, dy = struct.unpack_from(">bb", d, p)
                p += 2
            a, b, c, e = 1.0, 0.0, 0.0, 1.0
            if fl & 8:
                a = e = s16(d, p) / 16384.0
                p += 2
            elif fl & 0x40:
                a, e = s16(d, p) / 16384.0, s16(d, p + 2) / 16384.0
                p += 4
            elif fl & 0x80:
                a, b, c, e = [s16(d, p + 2 * k) / 16384.0 for k in range(4)]
                p += 8
            for cont in self._contours(gi):
                out.append([(a * x + c * y + dx, b * x + e * y + dy, on) for (x, y, on) in cont])
            if not fl & 0x20:
                return out

    def path(self, gid):
        """outline as SVG path data in font units, y pointing down"""
        if gid not in self.paths:
            parts = []
            for cont in self._contours(gid):
                pts = [(x, -y, on) for (x, y, on) in cont]
                if not pts:
                    continue
                k0 = next((i for i, q in enumerate(pts) if q[2]), None)
                if k0 is None:
                    pts = [((pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2, True)] + pts
                    k0 = 0
                pts = pts[k0:] + pts[:k0]
                n = len(pts)
                s = "M%d %d" % (round(pts[0][0]), round(pts[0][1]))
                i = 1
                while i <= n:
                    q = pts[i % n]
                    if q[2]:
                        s += "L%d %d" % (round(q[0]), round(q[1]))
                        i += 1
                        continue
                    r = pts[(i + 1) % n]
                    if r[2]:
                        s += "Q%d %d %d %d" % (round(q[0]), round(q[1]), round(r[0]), round(r[1]))
                        i += 2
                    else:
                        s += "Q%d %d %d %d" % (round(q[0]), round(q[1]), round((q[0] + r[0]) / 2), round((q[1] + r[1]) / 2))
                        i += 1
                parts.append(s + "Z")
            self.paths[gid] = "".join(parts)
        return self.paths[gid]


FONT = {
    "S": Font(FONTS / "instr400.ttf", "S"),       # Instrument Serif
    "I": Font(FONTS / "instr400i.ttf", "I"),      # Instrument Serif Italic
    "N": Font(FONTS / "intertight500.ttf", "N"),  # Inter Tight 500
    "B": Font(FONTS / "intertight700.ttf", "B"),  # Inter Tight 700
    "M": Font(FONTS / "martian500.ttf", "M"),     # Martian Mono 500, width 112.5
}


class Doc:
    """Typesetter for one SVG: places glyphs as <use> and collects the outlines it needs."""

    def __init__(self):
        self.used = set()

    def _layout(self, s, font, size, ls):
        fo = FONT[font]
        k = size / fo.upm
        gs = []
        for ch in s:
            if ord(ch) not in fo.cmap:
                raise KeyError("font %s has no glyph for %r" % (font, ch))
            gs.append(fo.cmap[ord(ch)])
        xs, pen = [], 0.0
        for i, g in enumerate(gs):
            xs.append(pen)
            pen += fo.adv[g]
            if i + 1 < len(gs):
                pen += fo.kern.get((g, gs[i + 1]), 0) + ls / k
        return fo, k, gs, xs, pen * k

    def width(self, s, font, size, ls=0.0):
        return self._layout(s, font, size, ls)[4]

    def text(self, s, x, y, font, size, fill, anchor="start", ls=0.0, extra=""):
        fo, k, gs, xs, w = self._layout(s, font, size, ls)
        x -= {"start": 0, "middle": w / 2, "end": w}[anchor]
        uses = []
        for g, gx in zip(gs, xs):
            if fo.path(g):
                self.used.add((font, g))
                uses.append('<use href="#g%s%d" x="%d"/>' % (font, g, round(gx)))
        return '<g transform="translate(%s %s) scale(%s)" fill="%s"%s>%s</g>' % (
            "%.2f" % x, "%.2f" % y, ("%.6f" % k).rstrip("0"), fill, extra, "".join(uses))

    def ring(self, s, cx, cy, r, font, size, fill):
        """s spread evenly round a full circle, clockwise from the top, glyphs facing out"""
        fo = FONT[font]
        k = size / fo.upm
        gs = [fo.cmap[ord(ch)] for ch in s]
        gap = (2 * math.pi * r - sum(fo.adv[g] for g in gs) * k) / len(gs)
        out, pos = [], 0.0
        for g in gs:
            a = fo.adv[g] * k
            th = (pos + a / 2) / r - math.pi / 2
            if fo.path(g):
                self.used.add((font, g))
                out.append('<use href="#g%s%d" transform="translate(%.2f %.2f) rotate(%.2f) scale(%s) translate(%d 0)"/>' % (
                    font, g, cx + r * math.cos(th), cy + r * math.sin(th), math.degrees(th) + 90, ("%.6f" % k).rstrip("0"), -fo.adv[g] // 2))
            pos += a + gap
        return '<g fill="%s">%s</g>' % (fill, "".join(out))

    def defs(self):
        return "".join('<path id="g%s%d" d="%s"/>' % (fk, g, FONT[fk].path(g)) for fk, g in sorted(self.used))


# ================================================================= shapes and textures
def glitter_tile(seed, w, h, n):
    rnd = random.Random(seed)
    return "".join('<circle cx="%s" cy="%s" r="%s" opacity="%s"/>' % (
        f(rnd.random() * w), f(rnd.random() * h), f(rnd.choice([0.5, 0.6, 0.75, 0.9, 1.15])), f(rnd.uniform(0.4, 1)))
        for _ in range(n))


def star(cx, cy, s, t=0.18):
    """four-point glint"""
    a = s * t
    return ("M{x} {y1}Q{xa} {ya} {x2} {y}Q{xa} {yb} {x} {y2}Q{xb} {yb} {x0} {y}Q{xb} {ya} {x} {y1}Z"
            .format(x=f(cx), y=f(cy), y1=f(cy - s), y2=f(cy + s), x0=f(cx - s), x2=f(cx + s),
                    xa=f(cx + a), xb=f(cx - a), ya=f(cy - a), yb=f(cy + a)))


def diamond(cx, cy, r):
    return "M%s %sL%s %sL%s %sL%s %sZ" % (f(cx), f(cy - r), f(cx + r), f(cy), f(cx), f(cy + r), f(cx - r), f(cy))


def hexagon(cx, cy, r):
    return "M" + "L".join("%s %s" % (f(cx + r * math.cos(math.radians(60 * k - 90))), f(cy + r * math.sin(math.radians(60 * k - 90))))
                          for k in range(6)) + "Z"


def set_symbol(cx, cy, r, disc, line):
    """the set symbol is his avatar: a black disc with stacked white waves"""
    out = ['<circle cx="%s" cy="%s" r="%s" fill="%s"/>' % (f(cx), f(cy), f(r), disc),
           '<g fill="none" stroke="%s" stroke-width="%s" stroke-linecap="round">' % (line, f(max(r * 0.11, 0.6)))]
    for dy, amp, span in [(-0.45, 0.10, 0.62), (-0.12, 0.25, 0.86), (0.2, 0.42, 0.9), (0.5, 0.22, 0.72)]:
        y, hw = cy + dy * r, span * r
        out.append('<path d="M%s %sC%s %s %s %s %s %sS%s %s %s %s"/>' % (
            f(cx - hw), f(y), f(cx - 0.45 * hw), f(y), f(cx - 0.3 * hw), f(y - amp * r * 1.5), f(cx), f(y - amp * r * 1.5),
            f(cx + 0.45 * hw), f(y), f(cx + hw), f(y)))
    return "".join(out) + "</g>"


def pl(pts):
    return "M" + "L".join("%s %s" % (f(x), f(y)) for x, y in pts)


def soft_shadow(x, y, w, h, r, layers, alpha, spread=3.0, drop=1.6):
    """a blur-free soft shadow: nested rounded rects of low opacity"""
    out = []
    for i in range(layers):
        g = spread * (i + 1)
        out.append('<rect x="%s" y="%s" width="%s" height="%s" rx="%s" fill="#000" opacity="%s"/>' % (
            f(x - g), f(y - g + drop * (i + 1)), f(w + 2 * g), f(h + 2 * g), f(r + g), alpha))
    return "".join(out)


# ================================================================= the foil
def shared_paint(I, light_r=400, band_w=110, glit=(190, 170, 46)):
    """gradients every foil surface uses"""
    D = ['<linearGradient id="{I}metal" x1="0" y1="0" x2="1" y2="1">'
         '<stop offset="0" stop-color="#3e3c44"/><stop offset=".3" stop-color="#7c7982"/>'
         '<stop offset=".52" stop-color="#34333a"/><stop offset=".78" stop-color="#8a867f"/>'
         '<stop offset="1" stop-color="#3a3940"/></linearGradient>',
         '<linearGradient id="{I}face" x1="0" y1="0" x2="0" y2="1">'
         '<stop offset="0" stop-color="#131318"/><stop offset=".55" stop-color="#0e0e12"/><stop offset="1" stop-color="#0b0b0e"/></linearGradient>',
         '<linearGradient id="{I}spec" gradientUnits="userSpaceOnUse" x1="-900" y1="-700" x2="1460" y2="1500">%s</linearGradient>' % stops(SPECTRUM, 5),
         '<linearGradient id="{I}pill" gradientUnits="userSpaceOnUse" x1="-900" y1="1500" x2="1460" y2="-700">%s</linearGradient>'
         % stops(["#7d7d7d", "#c6c6c6", "#ffffff", "#c6c6c6"], 30),
         '<radialGradient id="{I}light" gradientUnits="userSpaceOnUse" cx="0" cy="0" r="%s">'
         '<stop offset="0" stop-color="#d6d6d6"/><stop offset=".3" stop-color="#b0b0b0"/>'
         '<stop offset=".65" stop-color="#4a4a4a"/><stop offset="1" stop-color="#000"/></radialGradient>' % light_r,
         '<linearGradient id="{I}band" gradientUnits="userSpaceOnUse" x1="-%s" y1="0" x2="%s" y2="0">'
         '<stop offset="0" stop-color="#000"/><stop offset=".5" stop-color="#fff"/><stop offset="1" stop-color="#000"/></linearGradient>' % (band_w, band_w),
         '<linearGradient id="{I}sheen" gradientUnits="userSpaceOnUse" x1="-90" y1="0" x2="90" y2="0">'
         '<stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".34" stop-color="#ff6b9a" stop-opacity=".14"/>'
         '<stop offset=".45" stop-color="#fff" stop-opacity=".26"/><stop offset=".5" stop-color="#fff" stop-opacity=".5"/>'
         '<stop offset=".55" stop-color="#fff" stop-opacity=".26"/><stop offset=".66" stop-color="#62c8ff" stop-opacity=".14"/>'
         '<stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>',
         # foil-stamped ink for names, in font units (glyphs are drawn at baseline 0)
         '<linearGradient id="{I}ink" gradientUnits="userSpaceOnUse" x1="0" y1="-760" x2="0" y2="40">'
         '<stop offset="0" stop-color="#fffaf1"/><stop offset=".5" stop-color="#ebe4d7"/><stop offset="1" stop-color="#bfb6a6"/></linearGradient>',
         '<pattern id="{I}glit" width="%d" height="%d" patternUnits="userSpaceOnUse"><g fill="#fff">%s</g></pattern>'
         % (glit[0], glit[1], glitter_tile(7, glit[0], glit[1], glit[2]))]
    return "".join(D).replace("{I}", I)


def foil_layers(I, mask, blend, ambient, w, h, cls):
    """spectrum x diffraction pillars, plus glitter, all lit by (ambient + moving light + sweep)"""
    return (
        '<g mask="url(#{I}{m})" style="mix-blend-mode:{b};isolation:isolate">'
        '<rect class="{c}spec" x="-900" y="-700" width="2360" height="2200" fill="url(#{I}spec)"/>'
        '<g class="{c}pill" style="mix-blend-mode:multiply"><rect x="-900" y="-700" width="2360" height="2200" fill="url(#{I}pill)"/></g>'
        '<rect width="{w}" height="{h}" fill="url(#{I}glit)"/>'
        '<g style="mix-blend-mode:multiply;isolation:isolate">'
        '<rect x="-300" y="-300" width="{w2}" height="{h2}" fill="{amb}"/>'
        '<g class="{c}light" style="mix-blend-mode:screen"><rect x="-900" y="-900" width="1800" height="1800" fill="url(#{I}light)"/></g>'
        '<g class="{c}sweep" style="mix-blend-mode:screen"><rect x="-1600" y="-300" width="3200" height="1400" fill="url(#{I}band)" transform="skewX(-20)"/></g>'
        '</g></g>'
    ).format(I=I, m=mask, b=blend, w=w, h=h, w2=w + 600, h2=h + 600, amb=ambient, c=cls)


def sheen_layer(I, cls):
    return ('<g class="{c}sweep" style="mix-blend-mode:overlay">'
            '<rect x="-1600" y="-300" width="3200" height="1400" fill="url(#{I}sheen)" transform="skewX(-20)"/></g>').format(I=I, c=cls)


# ================================================================= hero card No. 101/101
CW, CH = 560, 784
FR = 11
ART = (24, 132, 512, 300)
CX, CY, R = 280, 282, 126

ABILITIES = [  # name, tag, effect, cost pips, repo (its number on the card is his commit count there)
    ("Elizabeth", "ELECTRON · LLM", "Chat with your documents through a local or hosted LLM.", 2, "elizabeth"),
    ("WCT", "MULTI-AGENT", "Agents identify wildlife, track migrations and flag threats.", 3, "WCT"),
    ("Rescue Rush", "REAL-TIME · MAPS", "Dispatch the nearest ambulance and follow it on a live map.", 2, "rescueRush"),
    ("heartBeats", "ANDROID · BLE · RL", "Your heart rate over BLE; Q-learning reorders the playlist.", 2, "heartBeats"),
]
COMMITS = {}  # repo -> commits by LOGIN, loaded from data/repos.json in main()


def ridge_paths(counts):
    """One ridgeline per week of real contributions, oldest at the back. Inside the disc
    they are printed in bone (his avatar); outside it they are only etched into the foil
    and show when the light reaches them."""
    vals = [c for _, c in counts]
    weeks = [vals[i:i + 7] for i in range(0, len(vals), 7)]
    peak = max(vals)
    sp = 4.55
    first = CY + 0.94 * R - (len(weeks) - 1) * sp
    ax, ay, aw, ah = ART
    n_top = int((first - ay) / sp) + 1
    n_bot = int((ay + ah + 6 - (first + (len(weeks) - 1) * sp)) / sp)
    rows = [None] * n_top + weeks + [None] * n_bot
    y0 = first - n_top * sp
    rnd = random.Random(101)
    paths = []
    for i, wk in enumerate(rows):
        base = y0 + i * sp
        ph = [rnd.uniform(0, 6.283) for _ in range(6)]
        jit = [rnd.uniform(-0.018, 0.018) for _ in range(7)]
        sig = [rnd.uniform(0.045, 0.07) for _ in range(7)]
        pts, x = [], ax - 2
        while x <= ax + aw + 2.01:
            u = (x - CX) / R
            h = 1.6 + 0.9 * (1.7 * math.sin(2.1 * u + ph[0]) + 1.1 * math.sin(4.3 * u + ph[1]) + 0.6 * math.sin(9.7 * u + ph[2]))
            if wk is not None:
                env = math.exp(-(u / 0.62) ** 2)
                h += env * (0.9 + 0.35 * math.sqrt(sum(wk) / 7.0)) * (math.sin(23 * u + ph[3]) * 0.8 + math.sin(41 * u + ph[4]) * 0.45)
                for dd, c in enumerate(wk):
                    if c > 0:
                        h += 58.0 * math.sqrt(c / peak) * math.exp(-0.5 * ((u - (-0.43 + dd * 0.143 + jit[dd])) / sig[dd]) ** 2)
            yy = base - max(h, 0.0)
            if wk is not None and abs(x - CX) < R:      # keep data peaks inside the avatar disc
                yy = max(yy, min(base, CY - math.sqrt(R * R - (x - CX) ** 2) + 1.5))
            pts.append((x, yy))
            x += 3.0 if abs(u) < 0.75 else 7.0
        d = "M%s %s" % (f(pts[0][0]), f(pts[0][1]))
        for (px, py), (qx, qy) in zip(pts, pts[1:]):
            d += "l%s %s" % (f(qx - px), f(qy - py))
        paths.append(d + "V%sH%sZ" % (f(base + sp * 1.2), f(pts[0][0])))
    return paths


def card(doc, counts, I="c"):
    """the hero card in card-local units; returns (defs, body)"""
    total = "{:,}".format(sum(c for _, c in counts))
    first, last = counts[0][0], counts[-1][0]
    span = "%s %d – %s %d" % (first.strftime("%b").upper(), first.year, last.strftime("%b").upper(), last.year)
    ax, ay, aw, ah = ART
    D = [shared_paint(I, light_r=345)]
    D.append('<radialGradient id="{I}artbg" gradientUnits="userSpaceOnUse" cx="{cx}" cy="{cy}" r="300">'
             '<stop offset="0" stop-color="#1c1c24"/><stop offset="1" stop-color="#0c0c10"/></radialGradient>'.format(I=I, cx=CX, cy=CY))
    D.append('<radialGradient id="{I}vig" gradientUnits="userSpaceOnUse" cx="{cx}" cy="{cy}" r="330">'
             '<stop offset=".35" stop-color="#0a0a0d" stop-opacity="0"/><stop offset="1" stop-color="#0a0a0d" stop-opacity=".75"/></radialGradient>'.format(I=I, cx=CX, cy=CY))
    D.append('<radialGradient id="{I}halo" gradientUnits="userSpaceOnUse" cx="{cx}" cy="{cy}" r="{r}">'
             '<stop offset=".8" stop-color="#000" stop-opacity=".9"/><stop offset="1" stop-color="#000" stop-opacity="0"/></radialGradient>'.format(I=I, cx=CX, cy=CY, r=R + 26))
    D.append('<linearGradient id="{I}artfoot" x1="0" y1="0" x2="0" y2="1">'
             '<stop offset="0" stop-color="#0b0b0e" stop-opacity="0"/><stop offset="1" stop-color="#0b0b0e" stop-opacity=".92"/></linearGradient>'.format(I=I))
    D.append('<radialGradient id="{I}glare" gradientUnits="userSpaceOnUse" cx="0" cy="0" r="260">'
             '<stop offset="0" stop-color="#fff" stop-opacity=".2"/><stop offset=".5" stop-color="#fff" stop-opacity=".05"/>'
             '<stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>'.format(I=I))
    D.append('<clipPath id="{I}cardclip"><rect width="{w}" height="{h}" rx="26"/></clipPath>'.format(I=I, w=CW, h=CH))
    D.append('<clipPath id="{I}artclip"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12"/></clipPath>'.format(I=I, x=ax, y=ay, w=aw, h=ah))
    D.append('<clipPath id="{I}disc"><circle cx="{cx}" cy="{cy}" r="{r}"/></clipPath>'.format(I=I, cx=CX, cy=CY, r=R))
    D.append('<g id="{I}ridges">{d}</g>'.format(I=I, d="".join('<path d="%s"/>' % p for p in ridge_paths(counts))))

    sx, sy, sr = ax + aw - 50, ay + 50, 36   # XP seal
    hy = 644                                   # passive box
    row0, rowh = 470, 45
    pips = [(478 - k * 11, row0 + i * rowh - 5.5) for i, a in enumerate(ABILITIES) for k in range(a[3])]

    # ------------------------------------------------------------- foil masks
    # metal: frame, art border, seal rings, cost pips, passive mark, stamped name and number
    m = ['<rect width="{w}" height="{h}" rx="26" fill="#fff"/>'.format(w=CW, h=CH),
         '<rect x="{fr}" y="{fr}" width="{w}" height="{h}" rx="16" fill="#000"/>'.format(fr=FR, w=CW - 2 * FR, h=CH - 2 * FR),
         '<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="#fff"/>'.format(x=ax, y=ay, w=aw, h=ah),
         '<rect x="{x}" y="{y}" width="{w}" height="36" fill="#000" opacity=".85"/>'.format(x=ax + 1, y=ay + ah - 37, w=aw - 2),
         '<circle cx="{cx}" cy="{cy}" r="{r}" fill="#000"/>'.format(cx=CX, cy=CY, r=R + 2),
         '<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#fff" stroke-width="1.4"/>'.format(cx=CX, cy=CY, r=R + 0.7),
         '<circle cx="{x}" cy="{y}" r="{r}" fill="#000"/>'.format(x=sx, y=sy, r=sr + 1),
         '<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="none" stroke="#fff" stroke-width="2.2"/>'.format(x=ax, y=ay, w=aw, h=ah),
         '<circle cx="{x}" cy="{y}" r="{r}" fill="none" stroke="#fff" stroke-width="2.4"/>'.format(x=sx, y=sy, r=sr),
         '<circle cx="{x}" cy="{y}" r="{r}" fill="none" stroke="#fff" stroke-width="1"/>'.format(x=sx, y=sy, r=sr - 6),
         doc.text("Oshada Bandara", 32, 109, "S", 66, "#383838"),
         doc.text("No. 101/101", 526, 50, "M", 9.5, "#9a9a9a", "end", 2.2),
         '<path d="{d}" fill="none" stroke="#fff" stroke-width="1.3"/>'.format(d=hexagon(43, hy + 17.5, 6))]
    m += ['<circle cx="%s" cy="%s" r="3.3" fill="#fff"/>' % (f(px), f(py)) for px, py in pips]
    D.append('<mask id="{I}metalmask" maskUnits="userSpaceOnUse" x="0" y="0" width="{w}" height="{h}">{m}</mask>'.format(I=I, w=CW, h=CH, m="".join(m)))


    # ------------------------------------------------------------- print
    b = ['<rect width="{w}" height="{h}" rx="26" fill="url(#{I}metal)"/>'.format(I=I, w=CW, h=CH),
         '<rect x="{fr}" y="{fr}" width="{w}" height="{h}" rx="16" fill="url(#{I}face)"/>'.format(I=I, fr=FR, w=CW - 2 * FR, h=CH - 2 * FR),
         '<rect x="{x}" y="{x}" width="{w}" height="{h}" rx="15.5" fill="none" stroke="#000" stroke-opacity=".6"/>'.format(x=FR + 0.5, w=CW - 2 * FR - 1, h=CH - 2 * FR - 1),
         doc.text("BUILDER · AI / ML", 34, 50, "M", 9.5, BONE2, ls=2.2),
         doc.text("No. 101/101", 526, 50, "M", 9.5, BONE2, "end", 2.2),
         doc.text("Oshada Bandara", 32, 109, "S", 66, "url(#%sink)" % I)]
    # art window
    b.append('<g clip-path="url(#{I}artclip)">'.format(I=I))
    b.append('<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="url(#{I}artbg)"/>'.format(I=I, x=ax, y=ay, w=aw, h=ah))
    b.append('<use href="#{I}ridges" fill="#131318" stroke="#5a5a66" stroke-width="1.2"/>'.format(I=I))
    b.append('<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="url(#{I}vig)"/>'.format(I=I, x=ax, y=ay, w=aw, h=ah))
    b.append('<circle cx="{cx}" cy="{cy}" r="{r}" fill="url(#{I}halo)"/>'.format(I=I, cx=CX, cy=CY, r=R + 26))
    b.append('<circle cx="{cx}" cy="{cy}" r="{r}" fill="#030304"/>'.format(cx=CX, cy=CY, r=R))
    b.append('<g clip-path="url(#{I}disc)"><use href="#{I}ridges" fill="#030304" stroke="{c}" stroke-width="1.25"/></g>'.format(I=I, c=BONE))
    b.append('<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#56545c" stroke-width="1.4"/>'.format(cx=CX, cy=CY, r=R + 0.7))
    b.append('<rect x="{x}" y="{y}" width="{w}" height="44" fill="url(#{I}artfoot)"/>'.format(I=I, x=ax, y=ay + ah - 44, w=aw))
    weeks = (len(counts) + 6) // 7
    b.append(doc.text("ILLUS. %d WEEKS OF HIS COMMITS, ONE LINE EACH" % weeks, ax + 14, ay + ah - 13, "M", 7, BONE3, ls=1.4))
    b.append(doc.text(span, ax + aw - 14, ay + ah - 13, "M", 7, BONE3, "end", 1.4))
    b.append('<circle cx="{x}" cy="{y}" r="{r}" fill="#09090c"/>'.format(x=sx, y=sy, r=sr))
    b.append('<circle cx="{x}" cy="{y}" r="{r}" fill="none" stroke="#6b6871" stroke-width="2.4"/>'.format(x=sx, y=sy, r=sr))
    b.append('<circle cx="{x}" cy="{y}" r="{r}" fill="none" stroke="#4a4850" stroke-width="1"/>'.format(x=sx, y=sy, r=sr - 6))
    b.append('</g>')
    b.append('<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="none" stroke="#55535b" stroke-width="1.2"/>'.format(x=ax, y=ay, w=aw, h=ah))
    b.append(doc.text(total, sx, sy + 4, "S", 21, BONE, "middle"))
    b.append(doc.text("XP", sx, sy + 15, "M", 6.5, BONE2, "middle", 1.8))
    # abilities
    b.append(doc.text("COMMITS", 526, row0 - 22, "M", 6.5, BONE3, "end", 1.6))
    for i, (name, tag, eff, cost, repo) in enumerate(ABILITIES):
        val = COMMITS[repo]
        y = row0 + i * rowh
        b.append(doc.text(name, 34, y, "I", 21, BONE))
        b.append(doc.text(tag, 34 + doc.width(name, "I", 21) + 10, y - 1, "M", 7.5, BONE3, ls=1.4))
        b.append(doc.text(str(val), 526, y, "M", 14, BONE, "end"))
        b.append(doc.text(eff, 34, y + 18, "N", 12, BONE2))
        b.append('<line x1="34" x2="526" y1="{y}" y2="{y}" stroke="#fff" stroke-opacity=".07"/>'.format(y=y + 29))
    b += ['<circle cx="%s" cy="%s" r="3.3" fill="#8f8b93"/>' % (f(px), f(py)) for px, py in pips]
    # passive
    b.append('<rect x="24" y="{y}" width="512" height="64" rx="10" fill="#15151b" stroke="#fff" stroke-opacity=".08"/>'.format(y=hy))
    b.append('<path d="{d}" fill="none" stroke="#8f8b93" stroke-width="1.3"/>'.format(d=hexagon(43, hy + 17.5, 6)))
    b.append(doc.text("PASSIVE · NEURAL_NETWORK", 56, hy + 20.5, "M", 7.5, BONE2, ls=2))
    b.append(doc.text("Each ability you play makes the next one stronger.", 38, hy + 39, "N", 12.5, BONE))
    b.append(doc.text("A feedforward neural network in pure NumPy · oshada101/neural_network", 38, hy + 55, "N", 11, BONE2))
    # flavour
    b.append(doc.text("“Turning ideas into reality.”", 280, hy + 94, "I", 15, BONE2, "middle"))
    # footer
    b.append(set_symbol(41, 755.5, 5.5, "#000", BONE2))
    b.append(doc.text("SERIES 01 · LOCAL-FIRST", 52, 758, "M", 7.5, BONE3, ls=1.4))
    b.append(doc.text("HOLO · © 2026 OSHADA.DEV", 526, 758, "M", 7.5, BONE3, "end", 1.4))

    # ------------------------------------------------------------- foil and light
    fo = [foil_layers(I, "metalmask", "color-dodge", "#242424", CW, CH, ""),
          '<g class="light" style="mix-blend-mode:overlay"><rect x="-900" y="-900" width="1800" height="1800" fill="url(#{I}glare)"/></g>',
          sheen_layer(I, ""),
          '<rect x=".5" y=".5" width="{w}" height="{h}" rx="25.5" fill="none" stroke="#fff" stroke-opacity=".14"/>'.format(w=CW - 1, h=CH - 1),
          # the seal's ring text sits above the foil so it stays crisp
          doc.ring("CONTRIBUTIONS · LAST 12 MONTHS · ", sx, sy, sr - 5.2, "M", 4.9, BONE3)]
    body = '<g clip-path="url(#{I}cardclip)" style="isolation:isolate">{b}{fo}</g>'.format(I=I, b="".join(b), fo="".join(fo))
    return "".join(D), body


CSS = """
.tilt{animation:tilt 12s ease-in-out infinite}
.bob{animation:bob 6s ease-in-out infinite}
.spec{animation:spec 12s ease-in-out infinite;transform:translate(-120px,-90px)}
.pill{animation:pill 12s ease-in-out infinite}
.light{animation:light 12s ease-in-out infinite;transform:translate(120px,180px)}
.sweep{animation:sweep 12s cubic-bezier(.5,0,.3,1) infinite;transform:translate(-500px,0)}
@keyframes tilt{0%,100%{transform:rotate(-.9deg)}50%{transform:rotate(.9deg)}}
@keyframes bob{0%,100%{transform:translate(0,0)}50%{transform:translate(0,-7px)}}
@keyframes spec{0%,100%{transform:translate(-170px,-120px)}50%{transform:translate(170px,120px)}}
@keyframes pill{0%,100%{transform:translate(36px,36px)}50%{transform:translate(-36px,-36px)}}
@keyframes light{0%,100%{transform:translate(110px,170px)}25%{transform:translate(470px,230px)}50%{transform:translate(450px,560px)}75%{transform:translate(110px,470px)}}
@keyframes sweep{0%,36%{transform:translate(-500px,0)}52%,100%{transform:translate(1100px,0)}}
.d0{animation:d0 18s ease-in-out infinite alternate}.d1{animation:d1 22s ease-in-out infinite alternate}.d2{animation:d2 16s ease-in-out infinite alternate}
@keyframes d0{from{transform:translate(0,0)}to{transform:translate(14px,-46px)}}
@keyframes d1{from{transform:translate(0,0)}to{transform:translate(-18px,-30px)}}
@keyframes d2{from{transform:translate(0,0)}to{transform:translate(8px,-60px)}}
.bounce{animation:bounce 12s ease-in-out infinite;transform:translate(-120px,0)}
@keyframes bounce{0%,100%{transform:translate(-170px,0)}50%{transform:translate(170px,0)}}
@media (prefers-reduced-motion:reduce){*{animation:none!important}}
"""

GLINTS = [  # (x, y, size, second of the 12 s cycle): where the light or the sweep catches the foil
    (468, 150, 9, 2.6), (536, 300, 7, 3.3), (64, 300, 8, 5.55), (300, 776, 7, 6.05),
    (250, 140, 6, 4.95), (36, 520, 7, 8.8), (520, 690, 6, 6.3)]


def glints(I):
    css, out = [], []
    for k, (x, y, sz, t) in enumerate(GLINTS):
        p = 100.0 * t / 12.0
        css.append("@keyframes g%d{0%%,%s%%,%s%%,100%%{transform:scale(0) rotate(0deg);opacity:0}%s%%{transform:scale(1) rotate(45deg);opacity:1}}"
                   % (k, f(max(p - 3.2, 0)), f(min(p + 4.2, 100)), f(p)))
        css.append(".g%d{animation:g%d 12s linear infinite;transform:scale(0)}" % (k, k))
        out.append('<g transform="translate(%s %s)"><g class="g%d"><circle r="%s" fill="url(#%sbloom)"/><path d="%s" fill="#fff"/></g></g>'
                   % (f(x), f(y), k, f(sz * 0.9), I, star(0, 0, sz, 0.12)))
    return "".join(css), '<g style="mix-blend-mode:screen">%s</g>' % "".join(out)


def dust(seed, n, box):
    rnd = random.Random(seed)
    x, y, w, h = box
    return "".join('<circle class="d%d" cx="%s" cy="%s" r="%s" fill="#fff" opacity="%s" style="animation-duration:%ss;animation-delay:-%ss"/>' % (
        i % 3, f(x + rnd.random() * w), f(y + rnd.random() * h), f(rnd.choice([0.6, 0.8, 1.0, 1.3])), f(rnd.uniform(0.12, 0.4)),
        f(rnd.uniform(14, 26)), f(rnd.uniform(0, 20))) for i in range(n))


def hero(counts, theme):
    W, H = 640, 912
    dark = theme == "dark"
    doc = Doc()
    defs, body = card(doc, counts, "c")
    gcss, gl = glints("c")
    oy = 52
    floor = oy + CH + 26
    page = "#0d1117" if dark else "#ffffff"
    d = []
    if dark:
        d.append('<radialGradient id="stage" cx=".5" cy=".46" r=".62"><stop offset="0" stop-color="#1b1d25"/><stop offset=".42" stop-color="#11141b"/><stop offset=".72" stop-color="%s"/></radialGradient>' % page)
        d.append('<linearGradient id="beam" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".06"/><stop offset=".7" stop-color="#fff" stop-opacity=".018"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>')
        d.append('<linearGradient id="beamx" x1="0" y1="0" x2="1" y2="0"><stop offset=".12" stop-color="#000"/><stop offset=".5" stop-color="#fff"/><stop offset=".88" stop-color="#000"/></linearGradient>')
        d.append('<mask id="beammask" maskUnits="userSpaceOnUse" x="0" y="0" width="{W}" height="{H}"><rect width="{W}" height="{H}" fill="url(#beamx)"/></mask>'.format(W=W, H=H))
    else:
        d.append('<radialGradient id="stage" cx=".5" cy=".46" r=".62"><stop offset="0" stop-color="#ece8e1"/><stop offset=".45" stop-color="#f6f4f0"/><stop offset=".72" stop-color="%s"/></radialGradient>' % page)
    d.append('<radialGradient id="pool" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#000" stop-opacity="%s"/><stop offset=".6" stop-color="#000" stop-opacity="%s"/><stop offset="1" stop-color="#000" stop-opacity="0"/></radialGradient>'
             % ((".75", ".25") if dark else (".30", ".10")))
    d.append('<radialGradient id="cbloom" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#fff" stop-opacity=".9"/><stop offset=".35" stop-color="#fff" stop-opacity=".25"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>')
    d.append('<linearGradient id="bouncegrad" gradientUnits="userSpaceOnUse" x1="-400" y1="0" x2="1280" y2="0">%s</linearGradient>' % stops(SPECTRUM, 3))
    d.append('<radialGradient id="bouncefade" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#fff"/><stop offset=".5" stop-color="#777"/><stop offset="1" stop-color="#000"/></radialGradient>')
    d.append('<mask id="bouncemask" maskUnits="userSpaceOnUse" x="0" y="0" width="{W}" height="{H}"><ellipse cx="{x}" cy="{y}" rx="290" ry="40" fill="url(#bouncefade)"/></mask>'.format(W=W, H=H, x=W / 2, y=floor + 4))
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-labelledby="t">'.format(W=W, H=H),
           '<title id="t">Oshada Bandara, card No. 101 of 101: a holographic collectible card. Builder, AI / ML. %s XP.</title>' % "{:,}".format(sum(c for _, c in counts)),
           '<style>%s%s</style>' % (CSS, gcss),
           '<defs>%s%s%s</defs>' % (doc.defs(), "".join(d), defs),
           '<rect width="{W}" height="{H}" fill="url(#stage)"/>'.format(W=W, H=H)]
    if dark:
        svg.append('<path d="M{a} 0H{b}L{c} {y}H{e}Z" fill="url(#beam)" mask="url(#beammask)"/>'.format(a=W / 2 - 200, b=W / 2 + 200, c=W / 2 + 440, e=W / 2 - 440, y=floor))
        svg.append('<g>%s</g>' % dust(11, 30, (W / 2 - 300, 20, 600, floor - 60)))
    svg.append('<ellipse cx="{x}" cy="{y}" rx="300" ry="28" fill="url(#pool)"/>'.format(x=W / 2, y=floor))
    svg.append('<g mask="url(#bouncemask)" opacity="{o}" style="mix-blend-mode:{b}"><rect class="bounce" x="-400" y="{y}" width="1680" height="120" fill="url(#bouncegrad)"/></g>'
               .format(o=".34" if dark else ".2", b="screen" if dark else "multiply", y=floor - 60))
    svg.append(soft_shadow(W / 2 - CW / 2 + 4, oy + 10, CW - 8, CH - 4, 26, 10, ".03" if dark else ".016", 3.2, 1.8))
    svg.append('<g transform="translate({x} {y})"><g class="bob"><g class="tilt"><g transform="translate({a} {b})">{body}{gl}</g></g></g></g>'
               .format(x=W / 2, y=oy + CH / 2, a=-CW / 2, b=-CH / 2, body=body, gl=gl))
    svg.append('</svg>')
    return "".join(svg)


# ================================================================= the set: smaller project cards
MW, MH = 300, 420
MFR = 7
MART = (16, 80, 268, 188)

SET = [
    dict(key="talk", name="talk", no="014", kind="TOOL", tag="HOTKEY · SPEECH-TO-TEXT",
         eff=["Hold a hotkey and speak. The words", "type themselves into any app."]),
    dict(key="mnemo", name="mnemo", no="033", kind="STUDY", tag="SM-2 · SPACED REPETITION",
         eff=["Flashcards that come back just", "before you would forget them."]),
    dict(key="pyvidsync", name="pyVidSync", no="058", kind="MEDIA", tag="WATCH TOGETHER · SYNC",
         eff=["Two screens, one film. Play, pause", "and seek stay in step."]),
    dict(key="campus", name="smart-campus-os", no="077", kind="SYSTEM", tag="ROOMS · RESOURCES · TICKETS",
         eff=["Book rooms, manage resources and", "resolve tickets across a campus."]),
]


def art_talk(doc):
    """typed words, a blinking cursor, and the speech still arriving"""
    ax, ay, aw, ah = MART
    cy = ay + ah / 2 + 4
    rnd = random.Random(3)
    out = []
    x = ax + 22
    for w in [30, 18, 42, 24]:
        out.append('<rect x="%s" y="%s" width="%s" height="11" rx="5.5" fill="%s"/>' % (f(x - 2), f(cy - 28), f(w + 4), PANEL))
        out.append('<rect x="%s" y="%s" width="%s" height="7" rx="3.5" fill="%s" opacity=".5"/>' % (f(x), f(cy - 26), f(w), BONE))
        x += w + 6
    x = ax + 22
    for w in [26, 38]:
        out.append('<rect x="%s" y="%s" width="%s" height="11" rx="5.5" fill="%s"/>' % (f(x - 2), f(cy - 6), f(w + 4), PANEL))
        out.append('<rect x="%s" y="%s" width="%s" height="7" rx="3.5" fill="%s"/>' % (f(x), f(cy - 4), f(w), BONE))
        x += w + 6
    cur_x = x + 2
    bars, bx = [], cur_x + 16
    while bx < ax + aw - 16:
        t = (bx - (cur_x + 16)) / (ax + aw - 32 - cur_x)
        env = (0.35 + 0.65 * math.sin(math.pi * min(1, t * 1.05)) ** 0.7) * (0.55 + 0.45 * abs(math.sin(t * 9.5 + 0.6)))
        bars.append((bx, 4 + 52 * env * (0.45 + 0.55 * rnd.random())))
        bx += 4.2
    bar_d = "".join("M%s %sV%s" % (f(x), f(cy - h / 2 - 0.5), f(cy + h / 2 - 0.5)) for x, h in bars)
    out.append('<path d="%s" stroke="%s" stroke-width="5" stroke-linecap="round"/>' % (bar_d, PANEL))
    out.append('<path d="%s" stroke="%s" stroke-width="1.9" stroke-linecap="round"/>' % (bar_d, BONE))
    out.append('<rect class="blink" x="%s" y="%s" width="2.4" height="30" fill="%s"/>' % (f(cur_x), f(cy - 19), BONE))
    return "".join(out)


def art_mnemo(doc):
    """forgetting curves: each review resets memory, and it fades more slowly every time"""
    ax, ay, aw, ah = MART
    x0, x1 = ax + 22, ax + aw - 18
    ytop, ybot = ay + 34, ay + ah - 30
    reviews, span, keep = [0, 1, 3, 7, 15, 30], 38.0, 0.62
    X = lambda t: x0 + (x1 - x0) * t / span
    Y = lambda r: ybot - (ybot - ytop) * (r - 0.5) / 0.5
    out = ['<path d="M%s %sH%s" stroke="%s" stroke-width="1" stroke-dasharray="2 4"/>' % (f(x0), f(Y(keep)), f(x1), BONE3),
           doc.text("FORGET", x1, Y(keep) + 11, "M", 6, BONE3, "end", 1.2)]
    d = ""
    for i, t0 in enumerate(reviews):
        t1 = reviews[i + 1] if i + 1 < len(reviews) else span
        dt = (t1 - t0) if i + 1 < len(reviews) else (reviews[i] - reviews[i - 1]) * 1.6
        S = dt / -math.log(keep)
        pts = [(X(t0 + (t1 - t0) * j / 40), Y(math.exp(-((t1 - t0) * j / 40) / S))) for j in range(41)]
        d += pl(pts)
        if i + 1 < len(reviews):
            out.append('<path d="M%s %sV%s" stroke="%s" stroke-width="1" stroke-dasharray="1.5 2.5"/>' % (f(X(t1)), f(pts[-1][1]), f(Y(1)), BONE2))
    out.append('<path d="%s" stroke="%s" stroke-width="5" fill="none"/>' % (d, PANEL))
    out.append('<path d="%s" stroke="%s" stroke-width="1.6" fill="none" stroke-linejoin="round"/>' % (d, BONE))
    out += ['<circle cx="%s" cy="%s" r="2.8" fill="%s"/>' % (f(X(t0)), f(Y(1)), BONE) for t0 in reviews]
    return "".join(out)


def art_sync(doc):
    """two film strips, one playhead"""
    ax, ay, aw, ah = MART
    x0, x1 = ax + 16, ax + aw - 16
    rnd = random.Random(5)
    heights = [0.3 + 0.7 * abs(math.sin(i * 0.37 + 1.1)) * (0.5 + 0.5 * rnd.random()) for i in range(120)]
    out = []
    for ty in (ay + 38, ay + 100):
        th = 40
        out.append('<rect x="%s" y="%s" width="%s" height="%s" rx="4" fill="%s" stroke="%s" stroke-width="1.3"/>' % (f(x0), f(ty), f(x1 - x0), th, PANEL, BONE))
        holes = "".join('<rect x="%s" y="%s" width="4" height="3" rx="1"/>' % (f(x), f(yy))
                        for x in range(int(x0) + 6, int(x1) - 6, 10) for yy in (ty + 3.5, ty + th - 6.5))
        out.append('<g fill="%s" opacity=".75">%s</g>' % (BONE2, holes))
        cy = ty + th / 2
        wave = "".join("M%s %sV%s" % (f(x), f(cy - hh / 2), f(cy + hh / 2)) for x, hh in
                       ((x, 22 * heights[i % len(heights)] * (0.55 + 0.45 * math.sin(x * 0.05) ** 2)) for i, x in enumerate(range(int(x0) + 6, int(x1) - 5, 2))))
        out.append('<path d="%s" stroke="%s" stroke-width="1"/>' % (wave, BONE))
    out.append(doc.text("HERE", x0, ay + 32, "M", 6.5, BONE3, ls=1.4))
    out.append(doc.text("THERE", x0, ay + 94, "M", 6.5, BONE3, ls=1.4))
    px = ax + aw * 0.42
    out.append('<g class="playhead"><path d="M%s %sV%s" stroke="%s" stroke-width="1.6"/><path d="%s" fill="%s"/></g>'
               % (f(px), f(ay + 30), f(ay + 146), BONE, diamond(px, ay + 26, 4.5), BONE))
    return "".join(out)


def art_campus(doc):
    """a floor plan with one room booked"""
    ax, ay, aw, ah = MART
    X0, Y0, X1, Y1 = ax + 18, ay + 20, ax + aw - 18, ay + ah - 20
    cy0, cy1 = ay + ah / 2 - 11, ay + ah / 2 + 11
    top = [X0, X0 + 52, X0 + 92, X0 + 150, X0 + 194, X1]
    bot = [X0, X0 + 74, X0 + 130, X1]
    walls = ["M%s %sH%sV%sH%sZ" % (f(X0), f(Y0), f(X1), f(Y1), f(X0)),
             "M%s %sH%sM%s %sH%s" % (f(X0), f(cy0), f(X1), f(X0), f(cy1), f(X1))]
    walls += ["M%s %sV%s" % (f(x), f(Y0), f(cy0)) for x in top[1:-1]]
    walls += ["M%s %sV%s" % (f(x), f(cy1), f(Y1)) for x in bot[1:-1]]
    bk = (top[2], Y0, top[3] - top[2], cy0 - Y0)
    hatch = "".join("M%s %sL%s %s" % (f(bk[0] + k * 6), f(bk[1] + bk[3]), f(bk[0] + k * 6 + bk[3]), f(bk[1])) for k in range(-20, 40))
    out = ['<rect x="%s" y="%s" width="%s" height="%s" fill="%s"/>' % (f(X0), f(Y0), f(X1 - X0), f(Y1 - Y0), PANEL),
           '<clipPath id="bk"><rect x="%s" y="%s" width="%s" height="%s"/></clipPath>' % tuple(f(v) for v in bk),
           '<g clip-path="url(#bk)"><path d="%s" stroke="%s" stroke-width=".9" opacity=".55"/></g>' % (hatch, BONE2),
           '<path d="%s" stroke="%s" stroke-width="1.7" fill="none" stroke-linejoin="round"/>' % ("".join(walls), BONE)]
    doors = [(a + 8, cy0, -1) for a in top[:-1]] + [(b - 22, cy1, 1) for b in bot[1:]]
    for x, y, sgn in doors:
        dw = 14
        out.append('<path d="M%s %sH%s" stroke="%s" stroke-width="3"/>' % (f(x), f(y), f(x + dw), PANEL))
        out.append('<path d="M%s %sV%s" stroke="%s" stroke-width="1.2"/>' % (f(x), f(y), f(y + sgn * dw), BONE))
        out.append('<path d="M%s %sA%s %s 0 0 %d %s %s" stroke="%s" stroke-width=".8" stroke-dasharray="1.5 2" fill="none"/>'
                   % (f(x + dw), f(y), dw, dw, 0 if sgn < 0 else 1, f(x), f(y + sgn * dw), BONE2))
    bx, by = bk[0] + bk[2] / 2, Y0 + (cy0 - Y0) / 2
    out.append('<rect x="%s" y="%s" width="46" height="13" rx="6.5" fill="%s" stroke="%s" stroke-width=".8"/>' % (f(bx - 23), f(by - 6.5), PANEL, BONE))
    out.append(doc.text("BOOKED", bx + 0.5, by + 2.2, "M", 6, BONE, "middle", 1.1))
    return "".join(out)


ARTS = {"talk": art_talk, "mnemo": art_mnemo, "pyvidsync": art_sync, "campus": art_campus}


def etched_lines(box, seed, sp=5.2, amp=2.2):
    ax, ay, aw, ah = box
    rnd = random.Random(seed)
    out, y = [], ay + 3
    while y < ay + ah:
        ph = [rnd.uniform(0, 6.3) for _ in range(3)]
        out.append(pl([(x, y + amp * (math.sin(6 * (x - ax) / aw + ph[0]) * 0.6 + math.sin(13 * (x - ax) / aw + ph[1]) * 0.3
                                       + math.sin(29 * (x - ax) / aw + ph[2]) * 0.15)) for x in range(int(ax) - 2, int(ax + aw) + 4, 6)]))
        y += sp
    return "".join(out)


MINI_CSS = """
.mspec{animation:mspec 12s ease-in-out infinite;transform:translate(-60px,-40px)}
.mpill{animation:mpill 12s ease-in-out infinite}
.mlight{animation:mlight 12s ease-in-out infinite;transform:translate(70px,110px)}
.msweep{animation:msweep 12s cubic-bezier(.5,0,.3,1) infinite;transform:translate(-300px,0)}
.blink{animation:blink 1.1s steps(1) infinite}
.playhead{animation:play 12s linear infinite}
@keyframes mspec{0%,100%{transform:translate(-110px,-80px)}50%{transform:translate(110px,80px)}}
@keyframes mpill{0%,100%{transform:translate(24px,24px)}50%{transform:translate(-24px,-24px)}}
@keyframes mlight{0%,100%{transform:translate(60px,100px)}25%{transform:translate(250px,130px)}50%{transform:translate(240px,330px)}75%{transform:translate(60px,290px)}}
@keyframes msweep{0%,40%{transform:translate(-300px,0)}56%,100%{transform:translate(700px,0)}}
@keyframes blink{0%{opacity:1}50%{opacity:0}}
@keyframes play{0%{transform:translate(-90px,0)}100%{transform:translate(120px,0)}}
@media (prefers-reduced-motion:reduce){*{animation:none!important}}
"""


def mini(p, k, theme):
    I = "m"
    dark = theme == "dark"
    doc = Doc()
    ax, ay, aw, ah = MART
    W, H = MW + 20, MH + 24
    ox, oy = 10, 7
    name_size = 31 if len(p["name"]) < 12 else 27
    et = etched_lines(MART, 20 + k)
    art = ARTS[p["key"]](doc)
    D = [shared_paint(I, light_r=170, band_w=70, glit=(120, 110, 22)),
         '<radialGradient id="{I}artbg" cx=".5" cy=".5" r=".7"><stop offset="0" stop-color="#1a1a21"/><stop offset="1" stop-color="#0c0c10"/></radialGradient>'.format(I=I),
         '<clipPath id="{I}cardclip"><rect width="{w}" height="{h}" rx="16"/></clipPath>'.format(I=I, w=MW, h=MH),
         '<clipPath id="{I}artclip"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8"/></clipPath>'.format(I=I, x=ax, y=ay, w=aw, h=ah),
         '<mask id="{I}metalmask" maskUnits="userSpaceOnUse" x="0" y="0" width="{w}" height="{h}">'
         '<rect width="{w}" height="{h}" rx="16" fill="#fff"/><rect x="{fr}" y="{fr}" width="{iw}" height="{ih}" rx="10" fill="#000"/>'
         '<rect x="{x}" y="{y}" width="{aw}" height="{ah}" rx="8" fill="#fff"/>{nm}</mask>'
         .format(I=I, w=MW, h=MH, fr=MFR, iw=MW - 2 * MFR, ih=MH - 2 * MFR, x=ax, y=ay, aw=aw, ah=ah,
                 nm=doc.text(p["name"], 19, 64, "S", name_size, "#333")),
         ]
    b = ['<rect width="{w}" height="{h}" rx="16" fill="url(#{I}metal)"/>'.format(I=I, w=MW, h=MH),
         '<rect x="{fr}" y="{fr}" width="{w}" height="{h}" rx="10" fill="url(#{I}face)"/>'.format(I=I, fr=MFR, w=MW - 2 * MFR, h=MH - 2 * MFR),
         doc.text("%s/101" % p["no"], 20, 32, "M", 8, BONE2, ls=1.8),
         doc.text(p["kind"], 280, 32, "M", 8, BONE3, "end", 1.8),
         doc.text(p["name"], 19, 64, "S", name_size, "url(#%sink)" % I),
         '<g clip-path="url(#{I}artclip)"><rect x="{x}" y="{y}" width="{w}" height="{h}" fill="url(#{I}artbg)"/>'
         '<path d="{et}" fill="none" stroke="#50505c" stroke-width="1.1"/><g fill="none">{art}</g></g>'.format(I=I, x=ax, y=ay, w=aw, h=ah, et=et, art=art),
         '<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="none" stroke="#55535b" stroke-width="1"/>'.format(x=ax, y=ay, w=aw, h=ah),
         doc.text(p["tag"], 20, 292, "M", 7.5, BONE3, ls=1.4)]
    b += [doc.text(line, 20, 316 + i * 18, "N", 13.5, BONE2) for i, line in enumerate(p["eff"])]
    b += ['<line x1="20" x2="280" y1="370" y2="370" stroke="#fff" stroke-opacity=".08"/>',
          doc.text("OPEN REPO", 20, 393, "M", 7.5, BONE3, ls=1.6),
          '<g transform="translate(272 389)" fill="none" stroke="{c}" stroke-width="1.3" stroke-linecap="round"><path d="M-4 4L4 -4M-2 -4H4V2"/></g>'.format(c=BONE2)]
    fo = [foil_layers(I, "metalmask", "color-dodge", "#141414", MW, MH, "m"),
          sheen_layer(I, "m"),
          '<rect x=".5" y=".5" width="{w}" height="{h}" rx="15.5" fill="none" stroke="#fff" stroke-opacity=".14"/>'.format(w=MW - 1, h=MH - 1)]
    body = '<g clip-path="url(#{I}cardclip)" style="isolation:isolate">{b}{fo}</g>'.format(I=I, b="".join(b), fo="".join(fo))
    css = MINI_CSS + ".mspec,.mpill,.mlight,.msweep{animation-delay:%ss}" % f(k * 0.22)
    shadow = soft_shadow(ox + 3, oy + 4, MW - 6, MH - 6, 16, 5, ".05" if dark else ".028", 1.6, 0.9)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-labelledby="t">'
            '<title id="t">{n}, card {no} of 101. {e}</title><style>{css}</style><defs>{glyphs}{D}</defs>{sh}'
            '<g transform="translate({ox} {oy})">{body}</g></svg>').format(
        W=W, H=H, n=esc(p["name"]), no=p["no"], e=esc(" ".join(p["eff"])), css=css, glyphs=doc.defs(), D="".join(D),
        sh=shadow, ox=ox, oy=oy, body=body)


# ================================================================= section titles and trade tokens
def title(word, note, right, theme):
    dark = theme == "dark"
    ink = "#e6edf3" if dark else "#1f2328"
    mute = "#7d8590" if dark else "#656d76"
    rule = "#30363d" if dark else "#d0d7de"
    doc = Doc()
    W, H = 880, 76
    t = [doc.text(word, 2, 50, "S", 42, ink),
         doc.text(note, 2 + doc.width(word, "S", 42) + 16, 49, "I", 22, mute),
         '<path d="M2 66H%d" stroke="%s"/>' % (W - 2, rule),
         doc.text(right, W - 2, 49, "M", 10.5, mute, "end", 1.8)]
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-labelledby="t">'
            '<title id="t">{w}: {n}</title><defs>{g}</defs>{t}</svg>').format(W=W, H=H, w=esc(word), n=esc(note), g=doc.defs(), t="".join(t))


TRADE = [
    dict(key="site", label="SITE", main="oshada.dev", sub="PORTFOLIO · PROJECTS"),
    dict(key="mail", label="MAIL", main="Email", sub="OSHADA1188@GMAIL.COM"),
    dict(key="linkedin", label="LINKEDIN", main="LinkedIn", sub="/IN/OSHADA-BANDARA"),
]


def token(t, k, theme):
    I = "t"
    dark = theme == "dark"
    doc = Doc()
    TW, TH = 280, 96
    W, H = TW + 12, TH + 14
    D = [shared_paint(I, light_r=150, band_w=60, glit=(90, 80, 10)),
         '<clipPath id="{I}cardclip"><rect width="{w}" height="{h}" rx="14"/></clipPath>'.format(I=I, w=TW, h=TH),
         '<mask id="{I}metalmask" maskUnits="userSpaceOnUse" x="0" y="0" width="{w}" height="{h}">'
         '<rect width="{w}" height="{h}" rx="14" fill="#fff"/><rect x="5" y="5" width="{iw}" height="{ih}" rx="10" fill="#000"/>'
         '<circle cx="{cx}" cy="{cy}" r="17" fill="none" stroke="#fff" stroke-width="1.4"/></mask>'.format(I=I, w=TW, h=TH, iw=TW - 10, ih=TH - 10, cx=TW - 40, cy=TH / 2)]
    b = ['<rect width="{w}" height="{h}" rx="14" fill="url(#{I}metal)"/>'.format(I=I, w=TW, h=TH),
         '<rect x="5" y="5" width="{w}" height="{h}" rx="10" fill="url(#{I}face)"/>'.format(I=I, w=TW - 10, h=TH - 10),
         doc.text(t["label"], 22, 32, "M", 8, BONE3, ls=2),
         doc.text(t["main"], 21, 61, "S", 27, "url(#%sink)" % I),
         doc.text(t["sub"], 22, 78, "M", 7, BONE3, ls=1.2),
         '<circle cx="{cx}" cy="{cy}" r="17" fill="none" stroke="#5d5b63" stroke-width="1.2"/>'.format(cx=TW - 40, cy=TH / 2),
         '<g transform="translate({cx} {cy})" fill="none" stroke="{c}" stroke-width="1.5" stroke-linecap="round"><path d="M-5 5L5 -5M-2 -5H5V2"/></g>'.format(cx=TW - 40, cy=TH / 2, c=BONE)]
    fo = [foil_layers(I, "metalmask", "color-dodge", "#121212", TW, TH, "t"), sheen_layer(I, "t")]
    css = (".tspec{animation:tspec 12s ease-in-out infinite;transform:translate(-60px,-40px)}"
           ".tlight{animation:tlight 12s ease-in-out infinite;transform:translate(40px,20px)}"
           ".tsweep{animation:tsweep 12s cubic-bezier(.5,0,.3,1) infinite;transform:translate(-300px,0)}"
           ".tspec,.tlight,.tsweep{animation-delay:%ss}" % f(k * 0.3) +
           "@keyframes tspec{0%,100%{transform:translate(-100px,-60px)}50%{transform:translate(100px,60px)}}"
           "@keyframes tlight{0%,100%{transform:translate(30px,10px)}50%{transform:translate(260px,90px)}}"
           "@keyframes tsweep{0%,60%{transform:translate(-300px,0)}74%,100%{transform:translate(600px,0)}}"
           "@media (prefers-reduced-motion:reduce){*{animation:none!important}}")
    body = '<g clip-path="url(#{I}cardclip)" style="isolation:isolate">{b}{fo}</g>'.format(I=I, b="".join(b), fo="".join(fo))
    shadow = soft_shadow(6 + 3, 4 + 3, TW - 6, TH - 4, 14, 4, ".05" if dark else ".03", 1.5, 0.8)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-labelledby="t">'
            '<title id="t">{m}</title><style>{css}</style><defs>{g}{D}</defs>{sh}<g transform="translate(6 4)">{body}</g></svg>').format(
        W=W, H=H, m=esc(t["main"]), css=css, g=doc.defs(), D="".join(D), sh=shadow, body=body)


# ================================================================= build
def write(name, s):
    (ASSETS / name).write_text(s)
    print("%-26s %4d KB" % (name, len(s.encode()) // 1024))


def graphql(query, **variables):
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or subprocess.run(
        ["gh", "auth", "token"], capture_output=True, text=True, check=True).stdout.strip()
    req = urllib.request.Request("https://api.github.com/graphql",
                                 data=json.dumps({"query": query, "variables": variables}).encode(),
                                 headers={"Authorization": "bearer " + token, "User-Agent": LOGIN + "-readme"})
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.load(r)
    if payload.get("errors"):
        sys.exit("GraphQL error: %s" % payload["errors"])
    return payload["data"]


def fetch():
    """Refresh data/: the contribution calendar and commit counts for the repos on the card."""
    user = graphql('query($login:String!){user(login:$login){id contributionsCollection{contributionCalendar'
                   '{weeks{contributionDays{date contributionCount}}}}}}', login=LOGIN)["user"]
    weeks = user["contributionsCollection"]["contributionCalendar"]["weeks"]
    days = [[d["date"], d["contributionCount"]] for w in weeks for d in w["contributionDays"]]
    parts = ['r%d:repository(owner:"%s",name:"%s"){isPrivate defaultBranchRef{target{... on Commit{history(author:{id:$id}){totalCount}}}}}'
             % (i, LOGIN, a[4]) for i, a in enumerate(ABILITIES)]
    repos = graphql("query($id:ID!){%s}" % " ".join(parts), id=user["id"])
    commits = {}
    for i, a in enumerate(ABILITIES):
        r = repos["r%d" % i]
        if r["isPrivate"]:  # the README is public: never publish anything about a private repo
            sys.exit("%s is private; take it off the card" % a[4])
        commits[a[4]] = r["defaultBranchRef"]["target"]["history"]["totalCount"]
    DATA.parent.mkdir(exist_ok=True)
    DATA.write_text(json.dumps(days, separators=(",", ":")) + "\n")
    REPOS.write_text(json.dumps(commits, indent=1) + "\n")
    print("fetched %d days (%d contributions); commits %s" % (len(days), sum(c for _, c in days), commits))


def sync_readme(total):
    """README.md quotes the XP figure in alt text and in the text version; keep it current."""
    text = README.read_text()
    new = re.sub(r"\d[\d,]* XP", "{:,} XP".format(total), text)
    if new != text:
        README.write_text(new)
        print("README.md: %s XP" % "{:,}".format(total))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fetch", action="store_true", help="refresh data/ from GitHub before building")
    if ap.parse_args().fetch or not DATA.exists() or not REPOS.exists():
        fetch()
    counts = load_counts()
    COMMITS.update(json.loads(REPOS.read_text()))
    ASSETS.mkdir(exist_ok=True)
    for theme in ("dark", "light"):
        write("hero-%s.svg" % theme, hero(counts, theme))
        for k, p in enumerate(SET):
            write("set-%s-%s.svg" % (p["key"], theme), mini(p, k, theme))
        write("title-set-%s.svg" % theme, title("The set", "four more cards from series 01", "TAP A CARD TO OPEN IT", theme))
        write("title-trade-%s.svg" % theme, title("Trade", "make an offer", "NOT FOR SALE · OPEN TO TRADE", theme))
        for k, t in enumerate(TRADE):
            write("trade-%s-%s.svg" % (t["key"], theme), token(t, k, theme))
    sync_readme(sum(c for _, c in counts))


if __name__ == "__main__":
    main()
