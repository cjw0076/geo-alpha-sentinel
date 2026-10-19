"""KIC investment-committee memo -> PDF.

`render_memo_pdf(result)` turns an `AnalysisResult` into a dense, printable
memo in the Geo-Alpha Sentinel visual language (Bloomberg terminal printed to
paper): black header band with amber type, white paper, thin grey rules,
tabular numbers.

Design notes
------------
* No external font files.  Latin text uses the PDF standard fonts
  (Helvetica / Courier); every run of characters outside cp1252 (Hangul,
  arrows, CJK) is wrapped in ReportLab's built-in CID font ``HYGothic-Medium``.
  Mixing per run keeps Latin metrics correct while Korean renders natively.
* Every ``[E#]`` tag in the memo becomes a superscript internal link to the
  matching row of Appendix A (the evidence ledger), which carries a
  ``<a name="E#"/>`` anchor and a clickable primary-source URL.
* The renderer never trusts the payload's shape: empty sections, missing
  histograms, missing filings, ``None`` values and very long quotes all degrade
  to placeholders instead of exceptions.
"""

from __future__ import annotations

import io
import json
import math
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlparse
from xml.sax.saxutils import escape as _xml_escape

from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Flowable,
    Frame,
    LongTable,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    XPreformatted,
)

from sentinel.models import AnalysisResult

__all__ = ["render_memo_pdf"]

# ---------------------------------------------------------------------------
# Palette & fonts
# ---------------------------------------------------------------------------

AMBER = HexColor("#FF9F1C")
AMBER_INK = HexColor("#C97500")      # amber dark enough for small type on white
INK = HexColor("#151515")
TEXT = HexColor("#2A2A2A")
MUTED = HexColor("#6F6F6F")
FAINT = HexColor("#9A9A9A")
RULE = HexColor("#D0D0D0")
ZEBRA = HexColor("#F6F6F6")
PANEL = HexColor("#FAFAFA")
CODE_BG = HexColor("#F4F4F4")
BAND = HexColor("#000000")
BAND_MUTED = HexColor("#B8B8B8")
RED = HexColor("#D7263D")
GREEN = HexColor("#1B998B")
GREY = HexColor("#6B6B6B")
TRACK = HexColor("#E9E9E9")

REC_COLORS = {
    "REDUCE": (RED, colors.white),
    "HEDGE": (AMBER, BAND),
    "HOLD": (GREY, colors.white),
    "ACCUMULATE": (GREEN, colors.white),
}

SANS, SANS_B = "Helvetica", "Helvetica-Bold"
MONO, MONO_B = "Courier", "Courier-Bold"
CJK = "HYGothic-Medium"
CJK_SERIF = "HYSMyeongJo-Medium"

PAGE_W, PAGE_H = A4
MARGIN = 16 * mm
CW = PAGE_W - 2 * MARGIN                # content width
FIRST_BAND_H = 27 * mm
LATER_BAND_H = 9 * mm

REPO_URL = "https://github.com/cjw0076/geo-alpha-sentinel"
FOOTER_TEXT = (
    "Geo-Alpha Sentinel · open source (Apache-2.0) · github.com/cjw0076/geo-alpha-sentinel"
    " · Not investment advice / 투자 권유 아님"
)

_FONTS_READY = False


def _register_fonts() -> None:
    global _FONTS_READY
    if _FONTS_READY:
        return
    for name in (CJK, CJK_SERIF):
        try:
            pdfmetrics.getFont(name)
        except Exception:  # noqa: BLE001 - not registered yet
            pdfmetrics.registerFont(UnicodeCIDFont(name))
    _FONTS_READY = True


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

# Characters neither cp1252 (Helvetica/Courier) nor the Korean CID font can draw.
_SUBS = str.maketrans({
    "−": "–",   # minus sign -> en dash
    "―": "—",   # horizontal bar -> em dash
    "✓": "OK", "✔": "OK", "✅": "OK",
    "✗": "X", "✘": "X", "❌": "X",
    "­": "", "​": "", "‌": "", "‍": "", "﻿": "",
    " ": " ", " ": " ", " ": " ", "　": " ",
    "\t": "    ",
})
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_ASTRAL = re.compile("[\U00010000-\U0010FFFF]")
_CP1252_HIGH = bytes(range(0x80, 0xA0)).decode("cp1252", errors="ignore")
_NONLATIN_RUN = re.compile("([^\\x00-\\x7f\\xa0-\\xff" + re.escape(_CP1252_HIGH) + "]+)")
_TAG = re.compile(r"\[\s*(E\d+(?:\s*[,;/]\s*E\d+)*)\s*\]")
_EID = re.compile(r"E\d+")


def _clean(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).translate(_SUBS)
    s = _CTRL.sub(" ", s)
    return _ASTRAL.sub("?", s)


def _clip(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: max(0, n - 1)].rstrip() + "…"


def _runs(text: str) -> List[Tuple[bool, str]]:
    """Split cleaned text into (is_cjk, chunk) runs."""
    out: List[Tuple[bool, str]] = []
    for i, part in enumerate(_NONLATIN_RUN.split(text)):
        if part:
            out.append((i % 2 == 1, part))
    return out


def _mk(value: Any, cjk: str = CJK) -> str:
    """Escape text for Paragraph markup, wrapping non-cp1252 runs in the CJK font."""
    out = []
    for is_cjk, chunk in _runs(_clean(value)):
        e = _xml_escape(chunk)
        out.append(f'<font name="{cjk}">{e}</font>' if is_cjk else e)
    return "".join(out)


def _attr(value: str) -> str:
    return _xml_escape(value, {'"': "&quot;"})


def _safe_url(url: Any) -> Optional[str]:
    u = _clean(url).strip()
    if not u:
        return None
    try:
        p = urlparse(u)
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    return u


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc
    except ValueError:
        return url
    return host[4:] if host.startswith("www.") else host


def _ref(eid: str, known: Set[str], size: float = 5.6) -> str:
    if eid in known:
        return (f'<super><font name="{SANS_B}" size="{size}">'
                f'<a href="#{eid}" color="{AMBER_INK.hexval()}">{eid}</a></font></super>')
    return f'<super><font name="{SANS}" size="{size}" color="{FAINT.hexval()}">{eid}?</font></super>'


def _rich(value: Any, known: Set[str], strip_tags: bool = False) -> str:
    """Markup with every [E#] tag rendered as an amber superscript link."""
    text = _clean(value)
    out: List[str] = []
    pos = 0
    for m in _TAG.finditer(text):
        out.append(_mk(text[pos:m.start()]))
        if not strip_tags:
            out.append("".join(_ref(e, known) for e in _EID.findall(m.group(1))))
        pos = m.end()
    out.append(_mk(text[pos:]))
    return "".join(out).replace("\n", "<br/>")


def _draw_mixed(canv, x: float, y: float, text: str, font: str, size: float,
                color=None, align: str = "left", cjk_size: Optional[float] = None) -> float:
    """Draw text on a canvas mixing a Latin font with the CJK font. Returns width."""
    runs = _runs(_clean(text))
    cs = cjk_size or size
    widths = [pdfmetrics.stringWidth(c, CJK if k else font, cs if k else size) for k, c in runs]
    total = sum(widths)
    if align == "right":
        x -= total
    elif align == "center":
        x -= total / 2
    if color is not None:
        canv.setFillColor(color)
    for (is_cjk, chunk), w in zip(runs, widths):
        canv.setFont(CJK if is_cjk else font, cs if is_cjk else size)
        canv.drawString(x, y, chunk)
        x += w
    return total


def _mixed_width(text: str, font: str, size: float) -> float:
    return sum(pdfmetrics.stringWidth(c, CJK if k else font, size) for k, c in _runs(_clean(text)))


# ---------------------------------------------------------------------------
# Number formatting
# ---------------------------------------------------------------------------

def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _money(v: Any) -> str:
    f = _num(v)
    if f is None:
        return "—"
    a = abs(f)
    sign = "-" if f < 0 else ""
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if a >= div:
            return f"{sign}${a / div:,.1f}{suf}"
    return f"{sign}${a:,.0f}"


def _plain(v: Any) -> str:
    f = _num(v)
    if f is None:
        return "—"
    if f.is_integer() and abs(f) < 1e15:
        return f"{f:,.0f}"
    if abs(f) >= 1000:
        return f"{f:,.1f}"
    return f"{f:,.4f}".rstrip("0").rstrip(".")


def _pct(v: Any, digits: int = 1, signed: bool = True) -> str:
    f = _num(v)
    if f is None:
        return "—"
    return f"{f:+.{digits}f}%" if signed and f != 0 else f"{f:.{digits}f}%"


def _ts(value: Any, with_time: bool = True) -> str:
    s = _clean(value).strip()
    if not s:
        return "—"
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s
    return dt.strftime("%Y-%m-%d %H:%M UTC" if with_time else "%Y-%m-%d")


def _ts_short(value: Any) -> str:
    s = _clean(value).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s or "—"
    return dt.strftime("%Y-%m-%d %H:%MZ")


def _band_color(value: float, reverse: bool = False):
    """green -> amber -> red as risk rises (reversed for FAR, where high is good)."""
    t = max(0.0, min(1.0, (value or 0.0) / 100.0))
    if reverse:
        t = 1.0 - t
    a, b = (GREEN, AMBER) if t < 0.5 else (AMBER, RED)
    u = t * 2 if t < 0.5 else (t - 0.5) * 2
    return colors.Color(a.red + (b.red - a.red) * u, a.green + (b.green - a.green) * u,
                        a.blue + (b.blue - a.blue) * u)


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

def _style(name: str, **kw) -> ParagraphStyle:
    base = dict(fontName=SANS, fontSize=8.6, leading=13.0, textColor=TEXT, alignment=TA_LEFT,
                splitLongWords=1)
    base.update(kw)
    return ParagraphStyle(name, **base)


S = {
    "body": _style("body", spaceAfter=4),
    "h2": _style("h2", fontName=SANS_B, fontSize=9.4, leading=13, textColor=INK, spaceBefore=7,
                 spaceAfter=2.5, keepWithNext=1),
    "company": _style("company", fontName=SANS_B, fontSize=19, leading=22, textColor=INK),
    "companysub": _style("companysub", fontSize=7.6, leading=10, textColor=MUTED),
    "kv": _style("kv", fontSize=7.4, leading=9.6, textColor=INK),
    "title": _style("title", fontName=SANS_B, fontSize=11.6, leading=15, textColor=INK, spaceAfter=3),
    "summary": _style("summary", fontSize=8.4, leading=12.6),
    "badge": _style("badge", fontName=SANS_B, fontSize=15, leading=17, alignment=TA_CENTER),
    "formula": _style("formula", fontName=MONO, fontSize=5.9, leading=7.4, textColor=MUTED),
    "cell": _style("cell", fontSize=6.6, leading=8.5),
    "cellsm": _style("cellsm", fontSize=5.9, leading=7.4, textColor=MUTED),
    "cellmono": _style("cellmono", fontName=MONO, fontSize=6.5, leading=8.5),
    "th": _style("th", fontName=MONO_B, fontSize=6.2, leading=7.6, textColor=AMBER),
    "statlabel": _style("statlabel", fontName=SANS_B, fontSize=6.0, leading=7.4, textColor=MUTED),
    "statval": _style("statval", fontName=MONO_B, fontSize=8.0, leading=9.6, textColor=INK,
                      alignment=TA_RIGHT),
    "note": _style("note", fontSize=6.6, leading=8.8, textColor=MUTED),
    "caption": _style("caption", fontSize=6.8, leading=9.2, textColor=MUTED, spaceAfter=4),
    "code": _style("code", fontName=MONO, fontSize=6.3, leading=7.9, textColor=INK, backColor=CODE_BG,
                   borderColor=RULE, borderWidth=0.5, borderPadding=(5, 6, 5, 6), spaceBefore=4,
                   spaceAfter=10),
    "empty": _style("empty", fontSize=7.6, leading=10, textColor=FAINT),
}


def P(markup: str, style: str = "body") -> Paragraph:
    return Paragraph(markup, S[style])


# ---------------------------------------------------------------------------
# Custom flowables
# ---------------------------------------------------------------------------

class SectionHeader(Flowable):
    """Amber function-code chip + heading + rule; also a PDF outline entry."""

    def __init__(self, code: str, title: str, key: Optional[str] = None, space_before: float = 12):
        super().__init__()
        self.code, self.title, self.key = code, title, key
        self.spaceBefore = space_before
        self.spaceAfter = 5
        self.height = 17

    def wrap(self, aw, ah):
        self.width = aw
        return aw, self.height

    def draw(self):
        c = self.canv
        chip_w = max(28, pdfmetrics.stringWidth(self.code, MONO_B, 7.4) + 10)
        c.setFillColor(AMBER)
        c.rect(0, 4, chip_w, 11, stroke=0, fill=1)
        c.setFillColor(BAND)
        c.setFont(MONO_B, 7.4)
        c.drawCentredString(chip_w / 2, 7, self.code)
        _draw_mixed(c, chip_w + 7, 6.4, self.title, SANS_B, 10.2, color=INK, cjk_size=9.8)
        c.setStrokeColor(INK)
        c.setLineWidth(0.8)
        c.line(0, 0.5, self.width, 0.5)
        if self.key:
            c.bookmarkHorizontal(self.key, 0, self.height + 4)
            try:
                c.addOutlineEntry(_clean(self.title), self.key, level=0)
            except Exception:  # noqa: BLE001 - outline is a convenience only
                pass


class ScenarioStrip(Flowable):
    """One-row scenario panel: four severity sliders + horizon."""

    def __init__(self, scenario):
        super().__init__()
        self.sc = scenario
        self.height = 27

    def wrap(self, aw, ah):
        self.width = aw
        return aw, self.height

    def draw(self):
        c, h = self.canv, self.height
        lab_w = 56
        c.setFillColor(BAND)
        c.rect(0, 0, lab_w, h, stroke=0, fill=1)
        c.setFillColor(AMBER)
        c.setFont(MONO_B, 7.6)
        c.drawString(7, h - 12, "SCENARIO")
        c.setFillColor(BAND_MUTED)
        c.setFont(MONO, 5.6)
        c.drawString(7, 6, "severity 0-100%")
        items = [
            ("TAIWAN STRAIT", getattr(self.sc, "taiwan", None)),
            ("CHINA EXPORT CTRL", getattr(self.sc, "china_export", None)),
            ("HORMUZ", getattr(self.sc, "hormuz", None)),
            ("MALACCA", getattr(self.sc, "malacca", None)),
        ]
        hz_w = 62
        cell_w = (self.width - lab_w - hz_w) / len(items)
        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.rect(lab_w, 0, self.width - lab_w, h, stroke=1, fill=0)
        for i, (label, v) in enumerate(items):
            x = lab_w + i * cell_w
            if i:
                c.line(x, 0, x, h)
            c.setFillColor(MUTED)
            c.setFont(SANS_B, 5.6)
            c.drawString(x + 7, h - 9.5, label)
            f = _num(v)
            c.setFillColor(INK)
            c.setFont(MONO_B, 10)
            c.drawRightString(x + cell_w - 7, h - 11, "—" if f is None else f"{f * 100:.0f}%")
            bx, bw = x + 7, cell_w - 14
            c.setFillColor(TRACK)
            c.rect(bx, 6, bw, 3.4, stroke=0, fill=1)
            if f is not None:
                c.setFillColor(_band_color(f * 100))
                c.rect(bx, 6, bw * max(0.0, min(1.0, f)), 3.4, stroke=0, fill=1)
        x = self.width - hz_w
        c.setStrokeColor(RULE)
        c.line(x, 0, x, h)
        c.setFillColor(MUTED)
        c.setFont(SANS_B, 5.6)
        c.drawString(x + 7, h - 9.5, "HORIZON")
        c.setFillColor(INK)
        c.setFont(MONO_B, 12)
        c.drawString(x + 7, 6, _clean(getattr(self.sc, "horizon", "—")))


class MetricTiles(Flowable):
    """CPI / GPI / SRS / FAR tiles with gauge bars."""

    GAP = 6

    def __init__(self, metrics: Dict[str, Any], refs: Dict[str, str], known: Set[str]):
        super().__init__()
        self.metrics, self.refs, self.known = metrics, refs, known
        self.height = 60

    @classmethod
    def tile_width(cls, aw: float) -> float:
        return (aw - 3 * cls.GAP) / 4

    def wrap(self, aw, ah):
        self.width = aw
        return aw, self.height

    def draw(self):
        c, h = self.canv, self.height
        tw = self.tile_width(self.width)
        for i, code in enumerate(("CPI", "GPI", "SRS", "FAR")):
            x = i * (tw + self.GAP)
            m = self.metrics.get(code)
            value = _num(getattr(m, "value", None)) if m is not None else None
            name = _clean(getattr(m, "name", "")) if m is not None else ""
            c.setStrokeColor(RULE)
            c.setLineWidth(0.6)
            c.setFillColor(colors.white)
            c.rect(x, 0, tw, h, stroke=1, fill=1)
            # black strip
            c.setFillColor(BAND)
            c.rect(x, h - 13, tw, 13, stroke=0, fill=1)
            c.setFillColor(AMBER)
            c.setFont(MONO_B, 8.6)
            c.drawString(x + 5, h - 9.6, code)
            size = 5.8
            room = tw - 10 - pdfmetrics.stringWidth(code, MONO_B, 8.6) - 6
            while size > 4.2 and _mixed_width(name, SANS, size) > room:
                size -= 0.2
            _draw_mixed(c, x + tw - 5, h - 9.2, name, SANS, size, color=BAND_MUTED, align="right")
            reverse = code == "FAR"
            if value is None:
                c.setFillColor(FAINT)
                c.setFont(MONO_B, 18)
                c.drawString(x + 6, 22, "n/a")
            elif code == "FAR":
                rating = _clean(getattr(m, "rating", None) or "—")
                c.setFillColor(INK)
                c.setFont(SANS_B, 22)
                c.drawString(x + 6, 21, rating)
                rw = pdfmetrics.stringWidth(rating, SANS_B, 22)
                c.setFont(MONO_B, 9)
                c.drawString(x + 6 + rw + 6, 21, f"{value:.1f}")
                c.setFillColor(MUTED)
                c.setFont(SANS, 5.8)
                c.drawString(x + 6 + rw + 6 + pdfmetrics.stringWidth(f"{value:.1f}", MONO_B, 9) + 1.5,
                             21, "/100")
            else:
                txt = f"{value:.1f}"
                c.setFillColor(INK)
                c.setFont(MONO_B, 21)
                c.drawString(x + 5, 21, txt)
                c.setFillColor(MUTED)
                c.setFont(SANS, 5.8)
                c.drawString(x + 6 + pdfmetrics.stringWidth(txt, MONO_B, 21), 21, "/100")
            if value is not None:
                hi, mid, lo = ("STRONG", "ADEQUATE", "WEAK") if reverse else ("HIGH", "ELEVATED", "LOW")
                word = hi if value >= 66.67 else mid if value >= 33.33 else lo
                col = _band_color(value, reverse)
                c.setFillColor(col)
                c.setFont(SANS_B, 5.8)
                c.drawRightString(x + tw - 5, 33, word)
            # gauge
            gx, gw = x + 6, tw - 12
            c.setFillColor(TRACK)
            c.rect(gx, 10, gw, 4, stroke=0, fill=1)
            if value is not None:
                c.setFillColor(_band_color(value, reverse))
                c.rect(gx, 10, gw * max(0.0, min(1.0, value / 100.0)), 4, stroke=0, fill=1)
            c.setStrokeColor(colors.white)
            c.setLineWidth(0.8)
            for q in (1 / 3, 2 / 3):
                c.line(gx + gw * q, 10, gx + gw * q, 14)
            c.setFillColor(FAINT)
            c.setFont(MONO, 4.8)
            c.drawString(gx, 4, "0")
            c.drawRightString(gx + gw, 4, "100")
            ref = self.refs.get(code)
            if ref and ref in self.known:
                c.setFillColor(AMBER_INK)
                c.setFont(SANS_B, 5.4)
                label = f"{ref} >"
                lw = pdfmetrics.stringWidth(label, SANS_B, 5.4)
                c.drawRightString(x + tw - 5, 22.5, label)
                c.linkRect("", ref, (x + tw - 5 - lw, 21, x + tw - 5, 28), relative=1)


# ---------------------------------------------------------------------------
# Page furniture
# ---------------------------------------------------------------------------

class _Furniture:
    def __init__(self, result: AnalysisResult):
        self.ticker = _clean(result.ticker or getattr(result.company, "ticker", "")) or "—"
        self.name = _clean(getattr(result.company, "name", "")) or self.ticker
        self.run_id = _clean(result.run_id) or "—"
        self.generated = _ts(result.generated_at)
        self.date = _ts(result.generated_at, with_time=False)

    def footer(self, c):
        c.setStrokeColor(RULE)
        c.setLineWidth(0.5)
        c.line(MARGIN, 11.5 * mm, PAGE_W - MARGIN, 11.5 * mm)
        _draw_mixed(c, MARGIN, 8 * mm, FOOTER_TEXT, SANS, 6.2, color=MUTED)
        c.linkURL(REPO_URL, (MARGIN, 7 * mm, MARGIN + 260, 10.5 * mm), relative=0)
        c.setFillColor(INK)
        c.setFont(MONO_B, 7.2)
        c.drawRightString(PAGE_W - MARGIN, 8 * mm, f"p.{c.getPageNumber()}")

    def first(self, c, doc):
        c.saveState()
        top = PAGE_H
        c.setFillColor(BAND)
        c.rect(0, top - FIRST_BAND_H, PAGE_W, FIRST_BAND_H, stroke=0, fill=1)
        c.setFillColor(AMBER)
        c.rect(0, top - FIRST_BAND_H, PAGE_W, 1.6, stroke=0, fill=1)
        c.setFillColor(AMBER)
        c.setFont(SANS_B, 17)
        c.drawString(MARGIN, top - 13 * mm, "GEO-ALPHA SENTINEL", charSpace=1.6)
        _draw_mixed(c, MARGIN, top - 19.5 * mm, "KIC INVESTMENT COMMITTEE MEMO · 투자심의 메모", SANS_B, 8.4,
                    color=AMBER, cjk_size=8.2)
        right = PAGE_W - MARGIN
        c.setFillColor(AMBER)
        c.setFont(MONO_B, 13)
        c.drawRightString(right, top - 12.5 * mm, f"{self.ticker} <MEMO>")
        c.setFillColor(BAND_MUTED)
        c.setFont(MONO, 6.8)
        c.drawRightString(right, top - 17 * mm, f"RUN {self.run_id}")
        c.drawRightString(right, top - 20.6 * mm, self.generated)
        self.footer(c)
        c.restoreState()

    def later(self, c, doc):
        c.saveState()
        top = PAGE_H
        c.setFillColor(BAND)
        c.rect(0, top - LATER_BAND_H, PAGE_W, LATER_BAND_H, stroke=0, fill=1)
        c.setFillColor(AMBER)
        c.rect(0, top - LATER_BAND_H, PAGE_W, 1.0, stroke=0, fill=1)
        y = top - 5.8 * mm
        c.setFillColor(AMBER)
        c.setFont(SANS_B, 7.6)
        c.drawString(MARGIN, y, "GEO-ALPHA SENTINEL", charSpace=0.9)
        x = MARGIN + pdfmetrics.stringWidth("GEO-ALPHA SENTINEL", SANS_B, 7.6) + 17 * 0.9 + 8
        _draw_mixed(c, x, y, f"{self.ticker} · {_clip(self.name, 48)} · 투자심의 메모", SANS, 7.0,
                    color=BAND_MUTED, cjk_size=6.8)
        c.setFillColor(BAND_MUTED)
        c.setFont(MONO, 6.6)
        c.drawRightString(PAGE_W - MARGIN, y, f"RUN {self.run_id} · {self.date}")
        self.footer(c)
        c.restoreState()


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------

def _grid_style(header: bool = True, zebra: bool = True, n_rows: int = 0) -> List[Tuple]:
    st: List[Tuple] = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
    ]
    if header:
        st += [
            ("BACKGROUND", (0, 0), (-1, 0), BAND),
            ("LINEBELOW", (0, 0), (-1, 0), 1.0, AMBER),
            ("TOPPADDING", (0, 0), (-1, 0), 3.4),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 3.0),
        ]
    if zebra:
        start = 1 if header else 0
        for r in range(start + 1, n_rows, 2):
            st.append(("BACKGROUND", (0, r), (-1, r), ZEBRA))
    return st


def _company_block(result: AnalysisResult, known: Set[str]) -> List[Flowable]:
    co = result.company
    ticker = _clean(result.ticker or co.ticker)
    sub_bits = [b for b in [
        ", ".join(_clean(x) for x in (co.exchanges or [])),
        f"SIC {_clean(co.sic)} {_clean(co.sic_description)}".strip() if co.sic else _clean(co.sic_description),
        f"Inc. {_clean(co.state_of_incorporation)}" if co.state_of_incorporation else "",
        f"HQ {_clean(co.hq_country)}" if co.hq_country else "",
        _clean(co.filer_category),
        f"FYE {_clean(co.fiscal_year_end)}" if co.fiscal_year_end else "",
    ] if b]
    title = Table(
        [[P(_mk(_clip(_clean(co.name) or ticker, 90)), "company"),
          P(f'<font name="{MONO_B}" size="15" color="{AMBER.hexval()}">{_mk(ticker)}</font>', "badge")]],
        colWidths=[CW - 34 * mm, 34 * mm],
    )
    title.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("BACKGROUND", (1, 0), (1, 0), BAND),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))

    la = co.latest_annual
    if la is not None:
        url = _safe_url(la.url)
        txt = f"{_mk(la.form)} · filed {_mk(la.filed)}"
        if url:
            txt = (f'<a href="{_attr(url)}" color="{AMBER_INK.hexval()}">{txt} '
                   f'<font name="{CJK}">↗</font></a>')
        if la.period:
            txt += f'<font color="{MUTED.hexval()}" size="6.2">  period {_mk(la.period)}</font>'
        filing = txt
    else:
        filing = f'<font color="{FAINT.hexval()}">no annual filing on record</font>'

    eng = result.engine or {}
    claims = list(result.memo.claims or []) if result.memo else []
    n_ok = eng.get("verified_claims", sum(1 for c in claims if c.verified))
    n_bad = eng.get("rejected_claims", sum(1 for c in claims if not c.verified))
    engine_txt = _mk(eng.get("llm") or "—")
    if eng.get("replans") is not None:
        engine_txt += f'<font color="{MUTED.hexval()}"> · {_mk(eng.get("replans"))} replans</font>'

    def kv(label: str, value_markup: str, mono: bool = True) -> Paragraph:
        face = MONO_B if mono else SANS
        return P(f'<font name="{SANS_B}" size="5.6" color="{MUTED.hexval()}">{label}</font><br/>'
                 f'<font name="{face}">{value_markup}</font>', "kv")

    rows = [
        [kv("TICKER", _mk(ticker)), kv("CIK", _mk(co.cik or "—")),
         kv("LATEST ANNUAL FILING", filing, mono=False), kv("EVIDENCE LEDGER", f"{len(result.evidence or [])} items")],
        [kv("GENERATED", _mk(_ts(result.generated_at))), kv("RUN ID", _mk(result.run_id or "—")),
         kv("ENGINE", engine_txt, mono=False),
         kv("CLAIM VERIFICATION",
            f'<font color="{GREEN.hexval()}">{_mk(n_ok)} OK</font> · '
            f'<font color="{RED.hexval() if n_bad else MUTED.hexval()}">{_mk(n_bad)} REJECTED</font>')],
    ]
    grid = Table(rows, colWidths=[CW * 0.20, CW * 0.20, CW * 0.36, CW * 0.24])
    grid.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEABOVE", (0, 0), (-1, 0), 0.9, INK),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
        ("LINEBEFORE", (1, 0), (-1, -1), 0.4, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]))
    out: List[Flowable] = [title, P(_mk("  ·  ".join(sub_bits)), "companysub"), Spacer(1, 5), grid,
                           Spacer(1, 6), ScenarioStrip(result.scenario)]
    q = _clean(getattr(result.scenario, "question", None)).strip()
    if q:
        out += [Spacer(1, 3), P(f'<font name="{SANS_B}" color="{AMBER_INK.hexval()}">ANALYST QUESTION</font>  '
                                f'{_mk(_clip(q, 600))}', "note")]
    return out


def _recommendation_block(result: AnalysisResult, known: Set[str]) -> Flowable:
    memo = result.memo
    rec = _clean(getattr(memo, "recommendation", "") or "HOLD").upper()
    bg, fg = REC_COLORS.get(rec, (GREY, colors.white))
    badge = P(f'<font name="{SANS_B}" size="5.4">IC RECOMMENDATION</font><br/>'
              f'<font name="{SANS_B}" size="15">{_mk(rec)}</font>', "badge")
    badge.style = ParagraphStyle("badge_c", parent=S["badge"], textColor=fg, leading=15)
    body = [P(_rich(_clip(_clean(memo.title), 300), known), "title"),
            P(_rich(_clip(_clean(memo.summary), 1800), known), "summary")]
    t = Table([[badge, body]], colWidths=[31 * mm, CW - 31 * mm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (0, 0), "MIDDLE"),
        ("VALIGN", (1, 0), (1, 0), "TOP"),
        ("BACKGROUND", (0, 0), (0, 0), bg),
        ("BACKGROUND", (1, 0), (1, 0), PANEL),
        ("BOX", (0, 0), (-1, -1), 0.6, RULE),
        ("LEFTPADDING", (1, 0), (1, 0), 9),
        ("RIGHTPADDING", (1, 0), (1, 0), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _metrics_block(result: AnalysisResult, known: Set[str]) -> List[Flowable]:
    by_code = {m.code: m for m in (result.metrics or [])}
    refs: Dict[str, str] = {}
    for e in result.evidence or []:
        lab = _clean(e.label)
        for code in ("CPI", "GPI", "SRS", "FAR"):
            if code not in refs and lab.startswith(code + " ") and e.provenance.source == "model":
                refs[code] = e.id
    tiles = MetricTiles(by_code, refs, known)
    tw = MetricTiles.tile_width(CW)
    cells = []
    for code in ("CPI", "GPI", "SRS", "FAR"):
        m = by_code.get(code)
        cells.append(P(_mk(_clip(_clean(m.formula), 220)) if m is not None else "—", "formula"))
    g = MetricTiles.GAP
    ft = Table([cells], colWidths=[tw + g, tw + g, tw + g, tw])
    ft.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 1),
        ("RIGHTPADDING", (0, 0), (2, 0), g + 1),
        ("RIGHTPADDING", (3, 0), (3, 0), 1),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return [tiles, ft]


def _memo_sections(result: AnalysisResult, known: Set[str]) -> List[Flowable]:
    out: List[Flowable] = [SectionHeader("MEMO", "Investment thesis · 투자 논지", key="sec-memo")]
    sections = list(getattr(result.memo, "sections", None) or [])
    if not sections:
        out.append(P(_mk("No memo sections were produced for this run. / 이 실행에는 본문 섹션이 없습니다."), "empty")
        return out
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        heading = _clean(sec.get("heading")).strip()
        body = _clean(sec.get("body")).strip()
        if heading:
            out.append(P(_rich(_clip(heading, 200), known), "h2"))
        paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()] or ([] if heading else ["—"])
        for p in paras:
            out.append(P(_rich(p.strip(), known), "body"))
    return out


def _histogram(stress, width: float, height: float) -> Drawing:
    d = Drawing(width, height)
    hist = getattr(stress, "histogram", None) or {}
    edges = [_num(x) for x in (hist.get("edges") or [])]
    counts = [_num(x) for x in (hist.get("counts") or [])]
    ok = (len(counts) >= 1 and len(edges) == len(counts) + 1
          and all(v is not None for v in edges + counts) and sum(counts) > 0 and edges[-1] > edges[0])
    d.add(String(0, height - 8, "REVENUE SHOCK DISTRIBUTION  % vs base revenue", fontName=SANS_B,
                 fontSize=6.4, fillColor=INK))
    paths = _num(getattr(stress, "paths", None))
    if paths:
        d.add(String(width, height - 8, f"{paths:,.0f} paths", fontName=MONO, fontSize=6,
                     fillColor=MUTED, textAnchor="end"))
    left, bottom, top = 22, 15, height - 15
    pw, ph = width - left - 2, top - bottom
    if not ok:
        d.add(Rect(left, bottom, pw, ph, strokeColor=RULE, strokeDashArray=[2, 2], fillColor=PANEL,
                   strokeWidth=0.6))
        d.add(String(left + pw / 2, bottom + ph / 2 - 2, "Histogram unavailable for this run",
                     fontName=SANS, fontSize=7, fillColor=FAINT, textAnchor="middle"))
        return d
    lo, hi = edges[0], edges[-1]
    total = sum(counts)
    cmax = max(counts)

    def xmap(v: float) -> float:
        return left + (v - lo) / (hi - lo) * pw

    rev = getattr(stress, "revenue_shock_pct", None) or {}
    p05, p50 = _num(rev.get("p05")), _num(rev.get("p50"))
    # grid + y labels (share of paths)
    for q in (0.25, 0.5, 0.75, 1.0):
        y = bottom + ph * q
        d.add(Line(left, y, left + pw, y, strokeColor=TRACK, strokeWidth=0.4))
        d.add(String(left - 3, y - 2, f"{cmax * q / total * 100:.0f}%", fontName=MONO, fontSize=5,
                     fillColor=FAINT, textAnchor="end"))
    for i, cnt in enumerate(counts):
        x0, x1 = xmap(edges[i]), xmap(edges[i + 1])
        bh = ph * cnt / cmax
        mid = (edges[i] + edges[i + 1]) / 2
        col = RED if (p05 is not None and mid <= p05) else AMBER
        if bh > 0:
            d.add(Rect(x0 + 0.35, bottom, max(0.3, x1 - x0 - 0.7), bh, fillColor=col, strokeColor=None))
    d.add(Line(left, bottom, left + pw, bottom, strokeColor=INK, strokeWidth=0.6))
    # x ticks every 10%
    step = 10 if (hi - lo) > 15 else 5 if (hi - lo) > 6 else 1
    t = math.ceil(lo / step) * step
    while t <= hi + 1e-9:
        x = xmap(t)
        d.add(Line(x, bottom, x, bottom - 2, strokeColor=INK, strokeWidth=0.5))
        d.add(String(x, bottom - 8, f"{t:.0f}%", fontName=MONO, fontSize=5.4, fillColor=MUTED,
                     textAnchor="middle"))
        t += step
    for val, label, col, yoff in ((p05, "p05", RED, 0), (p50, "p50", INK, 8)):
        if val is None or not (lo <= val <= hi):
            continue
        x = xmap(val)
        d.add(Line(x, bottom, x, top - 2, strokeColor=col, strokeWidth=0.7, strokeDashArray=[2, 1.5]))
        anchor = "start" if x < left + pw * 0.75 else "end"
        dx = 2 if anchor == "start" else -2
        d.add(String(x + dx, top - 8 - yoff, f"{label} {val:.1f}%", fontName=MONO_B, fontSize=5.6,
                     fillColor=col, textAnchor=anchor))
    return d


def _stress_block(result: AnalysisResult, known: Set[str]) -> List[Flowable]:
    st = result.stress
    rev = getattr(st, "revenue_shock_pct", None) or {}
    fcf = getattr(st, "fcf_shock_pct", None) or {}
    prob = _num(getattr(st, "prob_fcf_drawdown_gt_20", None))

    def val(markup: str, v: Optional[float] = None) -> Paragraph:
        col = RED if (v is not None and v < 0) else INK
        return P(f'<font color="{col.hexval()}">{markup}</font>', "statval")

    rows = [
        ("REVENUE SHOCK p05 (tail)", _pct(rev.get("p05")), _num(rev.get("p05"))),
        ("REVENUE SHOCK p50", _pct(rev.get("p50")), _num(rev.get("p50"))),
        ("REVENUE SHOCK p95", _pct(rev.get("p95")), _num(rev.get("p95"))),
        ("REVENUE SHOCK mean", _pct(rev.get("mean")), _num(rev.get("mean"))),
        ("FCF SHOCK p05 · p50", f'{_pct(fcf.get("p05"))} · {_pct(fcf.get("p50"))}', _num(fcf.get("p05"))),
        ("P(FCF DRAWDOWN > 20%)", "—" if prob is None else f"{prob * 100:.1f}%", None),
        ("PATHS · SEED", f'{_plain(getattr(st, "paths", None))} · {_plain(getattr(st, "seed", None))}', None),
        ("BASE REVENUE · BASE FCF", f'{_money(getattr(st, "base_revenue", None))} · '
                                    f'{_money(getattr(st, "base_fcf", None))}', None),
    ]
    stat_rows = [[P(_mk(a), "statlabel"), val(_mk(b), v)] for a, b, v in rows]
    left_w = CW * 0.58
    right_w = CW - left_w
    stats = Table(stat_rows, colWidths=[right_w * 0.52, right_w * 0.48 - 8])
    stats.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
        ("LINEABOVE", (0, 0), (-1, 0), 0.9, INK),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2.6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.6),
        ("BACKGROUND", (0, 0), (-1, 0), HexColor("#FFF4E5")),
    ]))
    hist = _histogram(st, left_w - 10, 50 * mm)
    t = Table([[hist, stats]], colWidths=[left_w, right_w])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 10),
        ("RIGHTPADDING", (1, 0), (1, 0), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
    ]))
    out: List[Flowable] = [CondPageBreak(75 * mm),
                           SectionHeader("STRS", "Scenario stress · 시나리오 스트레스", key="sec-stress"), t]

    # channel table from the sandbox inputs (if the run recorded them)
    inputs = getattr(st, "inputs", None) or {}
    sev, expo = inputs.get("severity"), inputs.get("exposure")
    mloss = inputs.get("max_revenue_loss") or {}
    if isinstance(sev, dict) and isinstance(expo, dict) and sev:
        names = {"taiwan": "Taiwan Strait / supply", "china_export": "China critical-mineral export ctrl",
                 "hormuz": "Strait of Hormuz energy", "malacca": "Strait of Malacca sea lane"}
        head = [P(x, "th") for x in ("CHANNEL", "SEVERITY", "EXPOSURE 0-1", "MAX REV LOSS")]
        body = []
        for k in sev:
            s_v, e_v, m_v = _num(sev.get(k)), _num(expo.get(k)), _num(mloss.get(k) if isinstance(mloss, dict) else None)
            body.append([P(_mk(names.get(k, k)), "cell"),
                         P(_mk("—" if s_v is None else f"{s_v * 100:.0f}%"), "cellmono"),
                         P(_mk("—" if e_v is None else f"{e_v:.2f}"), "cellmono"),
                         P(_mk("—" if m_v is None else f"{m_v * 100:.0f}%"), "cellmono")])
        ct = Table([head] + body, colWidths=[CW * 0.40, CW * 0.2, CW * 0.2, CW * 0.2], repeatRows=1)
        ct.setStyle(TableStyle(_grid_style(n_rows=len(body) + 1)))
        out += [Spacer(1, 6), ct]
    note = (f'Computed in the deterministic sandbox; program text in appendix '
            f'(<a href="#appendix-b" color="{AMBER_INK.hexval()}">Appendix B</a>). '
            f'Same inputs + seed reproduce the same distribution.')
    out += [Spacer(1, 4), P(note, "note")]
    return out


def _claims_block(result: AnalysisResult, known: Set[str]) -> List[Flowable]:
    claims = list(getattr(result.memo, "claims", None) or [])
    out: List[Flowable] = [CondPageBreak(35 * mm),
                           SectionHeader("CLMS", "Claim verification · 주장 검증", key="sec-claims")]
    if not claims:
        out.append(P(_mk("No claims were extracted for verification."), "empty"))
        return out
    head = [P(_mk(x), "th") for x in ("#", "CLAIM · 주장", "EVIDENCE", "VERIFIER")]
    rows = [head]
    for i, c in enumerate(claims, 1):
        ids = [e for e in (c.evidence_ids or []) if e]
        ev = ", ".join(
            (f'<a href="#{_attr(e)}" color="{AMBER_INK.hexval()}">{_mk(e)}</a>' if e in known else
             f'<font color="{FAINT.hexval()}">{_mk(e)}?</font>') for e in ids) or "—"
        if c.verified:
            status = f'<font name="{MONO_B}" color="{GREEN.hexval()}">OK</font>'
        else:
            status = f'<font name="{MONO_B}" color="{RED.hexval()}">REJECTED</font>'
        if c.reason:
            status += f'<br/><font size="5.8" color="{MUTED.hexval()}">{_mk(_clip(_clean(c.reason), 300))}</font>'
        rows.append([P(f"{i:02d}", "cellmono"), P(_rich(_clip(_clean(c.text), 900), known, strip_tags=True), "cell"),
                     P(ev, "cellmono"), P(status, "cell")])
    t = LongTable(rows, colWidths=[9 * mm, CW - 9 * mm - 30 * mm - 36 * mm, 30 * mm, 36 * mm], repeatRows=1)
    t.setStyle(TableStyle(_grid_style(n_rows=len(rows))))
    out.append(t)
    return out


def _hedges_block(result: AnalysisResult, known: Set[str]) -> List[Flowable]:
    hedges = [h for h in (getattr(result.memo, "hedges", None) or []) if isinstance(h, dict)]
    out: List[Flowable] = [CondPageBreak(30 * mm),
                           SectionHeader("HDGE", "Friend-shoring hedge ideas · 헤지 아이디어", key="sec-hedges")]
    if not hedges:
        out.append(P(_mk("No hedge ideas were proposed for this run."), "empty"))
        return out
    rows = [[P("#", "th"), P("THEME", "th"), P("RATIONALE", "th")]]
    for i, h in enumerate(hedges, 1):
        rows.append([P(f'<font color="{AMBER_INK.hexval()}">{i:02d}</font>', "cellmono"),
                     P(f"<b>{_rich(_clip(_clean(h.get('theme')), 200), known)}</b>", "cell"),
                     P(_rich(_clip(_clean(h.get("rationale")), 900), known), "cell")])
    t = LongTable(rows, colWidths=[9 * mm, 58 * mm, CW - 67 * mm], repeatRows=1)
    t.setStyle(TableStyle(_grid_style(n_rows=len(rows))))
    out.append(t)
    return out


def _evidence_value_markup(e) -> str:
    bits: List[str] = []
    v = _num(e.value)
    unit = _clean(e.unit).strip()
    if v is not None:
        if unit.upper() == "USD":
            bits.append(f'<font name="{MONO_B}" color="{INK.hexval()}">{_money(v)}</font>'
                        f'<font name="{MONO}" color="{MUTED.hexval()}"> ({_plain(v)} USD)</font>')
        else:
            bits.append(f'<font name="{MONO_B}" color="{INK.hexval()}">{_mk(_plain(v))}</font>'
                        + (f' <font color="{MUTED.hexval()}">{_mk(unit)}</font>' if unit else ""))
    if e.text:
        q = _clip(" ".join(_clean(e.text).split()), 600)
        bits.append(f'<font color="{TEXT.hexval()}">“{_mk(q)}”</font>')
    if not bits:
        if e.data is not None:
            n = len(e.data) if isinstance(e.data, (list, dict)) else None
            bits.append(f'<font color="{MUTED.hexval()}">{_mk(e.kind)} data'
                        f'{"" if n is None else f" · {n} entries"}</font>')
        else:
            bits.append(f'<font color="{FAINT.hexval()}">—</font>')
    return "<br/>".join(bits)


def _evidence_block(result: AnalysisResult) -> List[Flowable]:
    ev = list(result.evidence or [])
    out: List[Flowable] = [PageBreak(),
                           SectionHeader("EVID", "Appendix A · Evidence ledger · 증거 원장", key="appendix-a",
                                         space_before=0),
                           P(_mk(f"{len(ev)} records. Every [E#] tag in this memo links to its row; each row "
                                 f"links to the public primary source (filing quotes open at the quoted "
                                 f"sentence via #:~:text= fragments)."), "caption")]
    if not ev:
        out.append(P(_mk("The evidence ledger is empty for this run."), "empty"))
        return out
    head = [P("ID", "th"), P("EVIDENCE · VALUE / VERBATIM QUOTE", "th"), P("SOURCE · LOCATOR", "th"),
            P("RETRIEVED · LICENSE", "th"), P("PRIMARY SOURCE", "th")]
    rows = [head]
    seen: Set[str] = set()
    for e in ev:
        eid = _clean(e.id).strip() or "—"
        anchor = ""
        if eid not in seen and re.fullmatch(r"[A-Za-z0-9_.:-]+", eid):
            anchor = f'<a name="{_attr(eid)}"/>'
            seen.add(eid)
        idcell = (f'{anchor}<font name="{MONO_B}" size="7" color="{INK.hexval()}">{_mk(eid)}</font><br/>'
                  f'<font size="5.2" color="{FAINT.hexval()}">{_mk(_clean(e.kind).upper())}</font>')
        label = f'<font name="{SANS_B}">{_mk(_clip(_clean(e.label), 240))}</font>'
        pv = e.provenance
        src = (f'{_mk(_clip(_clean(pv.title), 200))}'
               + (f'<br/><font color="{MUTED.hexval()}">{_mk(_clip(_clean(pv.locator), 160))}</font>'
                  if pv.locator else "")
               + f'<br/><font name="{MONO_B}" size="5.2" color="{AMBER_INK.hexval()}">'
                 f'{_mk(_clean(pv.source).upper())}</font>')
        ret = (f'<font name="{MONO}">{_mk(_ts_short(pv.retrieved_at))}</font><br/>'
               f'<font size="5.6" color="{MUTED.hexval()}">{_mk(_clip(_clean(pv.license), 140))}</font>')
        url = _safe_url(pv.url)
        if url:
            link = (f'<a href="{_attr(url)}" color="{AMBER_INK.hexval()}">{_mk(_clip(_domain(url), 28))} '
                    f'<font name="{CJK}">↗</font></a>')
            if "#:~:text=" in url:
                link += f'<br/><font size="5.2" color="{MUTED.hexval()}">deep link to quote</font>'
            elif urlparse(url).path not in ("", "/"):
                link += (f'<br/><font size="5.2" color="{MUTED.hexval()}">'
                         f'{_mk(_clip(urlparse(url).path.rsplit("/", 1)[-1] or urlparse(url).path, 26))}</font>')
        else:
            link = f'<font color="{FAINT.hexval()}">{_mk(_clip(_clean(pv.url), 40)) or "—"}</font>'
        rows.append([P(idcell, "cell"), P(label + "<br/>" + _evidence_value_markup(e), "cell"),
                     P(src, "cell"), P(ret, "cell"), P(link, "cell")])
    widths = [13 * mm, 74 * mm, 44 * mm, 27 * mm]
    widths.append(CW - sum(widths))
    t = LongTable(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle(_grid_style(n_rows=len(rows))))
    out.append(t)
    return out


def _wrap_code(text: str, width: int = 112, max_lines: int = 400) -> Tuple[str, int]:
    lines: List[str] = []
    for raw in _clean(text).splitlines():
        raw = raw.rstrip()
        while len(raw) > width:
            lines.append(raw[:width])
            raw = "    " + raw[width:]
        lines.append(raw)
    dropped = max(0, len(lines) - max_lines)
    return "\n".join(lines[:max_lines]), dropped


def _sandbox_block(result: AnalysisResult) -> List[Flowable]:
    st = result.stress
    out: List[Flowable] = [CondPageBreak(60 * mm),
                           SectionHeader("CODE", "Appendix B · Sandbox program · 샌드박스 프로그램", key="appendix-b")]
    inputs = getattr(st, "inputs", None) or {}
    meta = [f"seed {_plain(getattr(st, 'seed', None))}", f"{_plain(getattr(st, 'paths', None))} paths"]
    if _num(inputs.get("sandbox_ms")) is not None:
        meta.append(f"executed in {_num(inputs.get('sandbox_ms')):.1f} ms")
    out.append(P(_mk("Exact Python source executed in the deterministic sandbox (numpy only, no I/O). "
                     + " · ".join(meta) + "."), "caption"))
    code, dropped = _wrap_code(getattr(st, "code", "") or "# (no program text recorded)")
    if dropped:
        code += f"\n# … {dropped} more lines truncated"
    out.append(XPreformatted(_mk(code), S["code"]))
    try:
        js = json.dumps(inputs, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        js = _clean(inputs)
    js_lines = [ln if len(ln) <= 118 else ln[:117] + "…" for ln in js.splitlines()]
    if len(js_lines) > 60:
        extra = len(js_lines) - 60
        js_lines = js_lines[:60] + [f"… ({extra} more lines)"]
    out += [P(f'<font name="{SANS_B}" color="{INK.hexval()}">Sandbox inputs</font>  '
              + _mk("(JSON, as passed to the program)"), "caption"),
            XPreformatted(_mk("\n".join(js_lines)), S["code"])]
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def render_memo_pdf(result: AnalysisResult) -> bytes:
    """Render an AnalysisResult as a KIC investment-committee memo PDF (bytes)."""
    _register_fonts()
    known: Set[str] = {_clean(e.id).strip() for e in (result.evidence or []) if e.id}
    known = {k for k in known if re.fullmatch(r"[A-Za-z0-9_.:-]+", k)}
    furn = _Furniture(result)

    buf = io.BytesIO()
    first_top = PAGE_H - FIRST_BAND_H - 6 * mm
    later_top = PAGE_H - MARGIN - 1 * mm
    bottom = MARGIN
    first = Frame(MARGIN, bottom, CW, first_top - bottom, id="first",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    later = Frame(MARGIN, bottom, CW, later_top - bottom, id="later",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc = BaseDocTemplate(
        buf, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN,
        title=_clean(f"{furn.ticker} — KIC investment committee memo (Geo-Alpha Sentinel)"),
        author="Geo-Alpha Sentinel", subject=_clean(getattr(result.memo, "title", "")),
        creator="Geo-Alpha Sentinel (Apache-2.0) — github.com/cjw0076/geo-alpha-sentinel",
        keywords=["KIC", "geopolitical risk", furn.ticker, f"run {furn.run_id}"],
        invariant=1,
    )
    doc.addPageTemplates([
        PageTemplate(id="First", frames=[first], onPage=furn.first, autoNextPageTemplate="Later"),
        PageTemplate(id="Later", frames=[later], onPage=furn.later),
    ])

    story: List[Flowable] = []
    story += _company_block(result, known)
    story += [Spacer(1, 7), _recommendation_block(result, known), Spacer(1, 7)]
    story += _metrics_block(result, known)
    story += _memo_sections(result, known)
    story += _stress_block(result, known)
    story += _claims_block(result, known)
    story += _hedges_block(result, known)
    story += _evidence_block(result)
    story += _sandbox_block(result)
    doc.build(story)
    return buf.getvalue()
