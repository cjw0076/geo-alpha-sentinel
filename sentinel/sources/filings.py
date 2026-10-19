"""Annual-report text: fetch the primary 10-K / 20-F document, convert to text,
isolate the risk-factor section and split it into quotable sentences.

Quotes are linked back with W3C text fragments (`#:~:text=start,end`) so a
reviewer lands on the highlighted sentence inside the SEC document.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import List, Optional, Tuple
from urllib.parse import quote

from sentinel.http import fetch_bytes
from sentinel.models import FilingRef

BLOCK_TAGS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "td"}
SKIP_TAGS = {"script", "style", "head", "title", "ix:header"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "td":
            self.parts.append(" ")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw: bytes) -> str:
    parser = _TextExtractor()
    parser.feed(raw.decode("utf-8", errors="replace"))
    text = "".join(parser.parts)
    text = html.unescape(text).replace("\xa0", " ").replace("​", "")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n[ \n]*", "\n", text)
    return text.strip()


@dataclass
class RiskSection:
    filing: FilingRef
    title: str
    text: str
    paragraphs: List[str] = field(default_factory=list)
    sentences: List[str] = field(default_factory=list)
    full_text: str = ""


# Section boundaries. Annual reports reuse the headings in the table of contents
# and in cross-reference indexes, so every candidate span is scored by the density
# of risk-factor language and the strongest span wins.
_START = re.compile(
    r"^\s*(item\s*1a\.?\s*[\-–—:]?\s*|(item\s*3\.?\s*)?d\.?\s*[\-–—:]?\s*)?risk\s+factors\.?\s*$", re.I | re.M
)
_END = re.compile(
    r"^\s*(item\s*(1b|1c|2|3|4|7)\b|unresolved\s+staff\s+comments|cybersecurity\s*$|properties\s*$|legal\s+proceedings"
    r"|information\s+on\s+the\s+company|quantitative\s+and\s+qualitative|market\s+for\s+(our|the)\s+|other\s+key\s+information"
    r"|operating\s+and\s+financial\s+review)",
    re.I | re.M,
)
_RISK_LANG = re.compile(r"\b(could|may|might)\s+(materially\s+)?(adversely|harm|negatively|disrupt|reduce)|material\s+adverse", re.I)


def _best_span(text: str) -> Optional[Tuple[int, int]]:
    best, best_score = None, 0.0
    for m in _START.finditer(text):
        e = _END.search(text, m.end() + 200)
        end = e.start() if e else len(text)
        end = min(end, m.end() + 400_000)
        length = end - m.end()
        if length < 5_000:
            continue
        hits = len(_RISK_LANG.findall(text, m.end(), end))
        score = hits * min(1.0, 40_000 / max(length, 1)) ** 0.25  # density-aware, mild length penalty
        if hits >= 15 and score > best_score:
            best, best_score = (m.end(), end), score
    return best


def split_sentences(block: str) -> List[str]:
    block = re.sub(r"\s+", " ", block).strip()
    if not block:
        return []
    pieces = re.split(r"(?<=[.!?])\s+(?=[A-Z\"“(])", block)
    return [p.strip() for p in pieces if 40 <= len(p.strip()) <= 900]


def risk_section(filing: FilingRef) -> RiskSection:
    raw, _ = fetch_bytes(filing.url, ttl=30 * 86400)
    text = html_to_text(raw)
    span = _best_span(text)
    title = "Item 3.D Risk Factors" if filing.form.startswith(("20-F", "40-F")) else "Item 1A. Risk Factors"
    if span is None:  # last resort: the whole document, capped
        span = (0, min(len(text), 400_000))
        title = "Annual report (risk-factor heading not detected)"
    body = text[span[0] : span[1]]
    paragraphs = [p.strip() for p in body.split("\n") if len(p.strip()) > 60]
    sentences: List[str] = []
    for p in paragraphs:
        sentences.extend(split_sentences(p))
    return RiskSection(filing=filing, title=title, text=body, paragraphs=paragraphs, sentences=sentences, full_text=text)


def _frag(s: str) -> str:
    return quote(s, safe="")


def text_fragment_url(base_url: str, sentence: str) -> str:
    """`#:~:text=<first 6 words>,<last 4 words>` — robust to inline tags in the middle."""
    words = re.sub(r"\s+", " ", sentence).strip().split(" ")
    words = [w for w in words if w]
    if len(words) <= 10:
        return f"{base_url}#:~:text={_frag(' '.join(words))}"
    start = " ".join(words[:6])
    end = " ".join(words[-4:]).rstrip(".;,")
    return f"{base_url}#:~:text={_frag(start)},{_frag(end)}"
