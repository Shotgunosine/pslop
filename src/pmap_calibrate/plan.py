"""Parse a performance plan into elements and their Level 3 and Level 5 standards."""

import re
from dataclasses import dataclass, field

from .blocks import Block, parse_blocks, sentences

_LEVEL_MARKER = re.compile(
    r"^\W*Level\s*([135])\b\s*(?:[–—-]\s*)?"
    r"(?:Fully\s+Successful|Outstanding|Unsuccessful|Unacceptable)?\s*"
    r"(?:\((?:FS|O|U)\))?\s*[:.\-–—]?\s*(.*)$",
    re.IGNORECASE,
)
# Plain-text plans (from PDF or Word without heading styles) usually label elements like this.
_ELEMENT_LINE = re.compile(r"^(?:required\b|(?:new\s+)?(?:non-)?critical\s+element\b)", re.IGNORECASE)
_TITLE_PREFIX = re.compile(
    r"^(?:required\s*[-–—:]\s*|(?:new\s+)?(?:non-)?critical\s+element\s*#?\s*\d*\s*[:.\-–—]\s*)",
    re.IGNORECASE,
)
_NUMBER = (
    r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)(?:\s*\(\d+\))?"
)
_NUMBER_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split()
)}
_MORE_THAN = re.compile(rf"\b(?:more than|greater than|over|in excess of)\s+{_NUMBER}\b", re.IGNORECASE)
_AT_LEAST = re.compile(
    rf"\b(?:at least|a minimum of|minimum of|no fewer than|no less than)\s+{_NUMBER}\b", re.IGNORECASE
)
_OR_MORE = re.compile(rf"\b{_NUMBER}\s+or\s+more\b", re.IGNORECASE)


@dataclass(frozen=True)
class Threshold:
    op: str  # ">" or ">="
    n: int

    def met(self, count: int) -> bool:
        return count > self.n if self.op == ">" else count >= self.n

    def describe(self) -> str:
        return f"more than {self.n}" if self.op == ">" else f"at least {self.n}"


@dataclass
class Standard:
    id: str
    level: int
    text: str
    threshold: Threshold | None = None


@dataclass
class Element:
    id: str
    title: str
    name: str
    description: str
    standards: list[Standard] = field(default_factory=list)

    def at_level(self, level: int) -> list[Standard]:
        return [s for s in self.standards if s.level == level]


def parse_threshold(text: str) -> Threshold | None:
    """Find a counted requirement such as "more than two projects" or "at least four projects"."""
    for pattern, op in ((_MORE_THAN, ">"), (_AT_LEAST, ">="), (_OR_MORE, ">=")):
        match = pattern.search(text)
        if match:
            word = match.group(1).lower()
            return Threshold(op, int(word) if word.isdigit() else _NUMBER_WORDS[word])
    return None


def parse_plan(text: str) -> list[Element]:
    """Elements start at a heading (or an "Critical Element ..." line); standards follow a
    "Level 3 – Fully Successful" or "Level 5 – Outstanding" marker."""
    elements: list[Element] = []
    title: str | None = None
    description: list[str] = []
    bodies: dict[int, list[Block]] = {}
    level: int | None = None

    def finish() -> None:
        if title and bodies:
            index = len(elements) + 1
            element = Element(
                id=f"e{index}",
                title=title,
                name=_TITLE_PREFIX.sub("", title).strip() or title,
                description=" ".join(description),
            )
            for lvl in sorted(bodies):
                for n, text in enumerate(_standard_texts(bodies[lvl]), start=1):
                    element.standards.append(
                        Standard(id=f"e{index}_l{lvl}_{n}", level=lvl, text=text, threshold=parse_threshold(text))
                    )
            elements.append(element)

    for block in parse_blocks(text):
        if block.kind == "heading" or (block.kind == "paragraph" and _ELEMENT_LINE.match(block.text)):
            if not block.text:
                continue
            if block.kind == "heading" and title and not bodies:
                # A sub-heading before any level marker belongs to the current element.
                description.append(block.text)
                continue
            finish()
            title, description, bodies, level = block.text, [], {}, None
            continue
        marker = _LEVEL_MARKER.match(block.text) if block.kind != "bullet" else None
        if marker and title:
            level = int(marker.group(1))
            bodies.setdefault(level, [])
            if marker.group(2).strip():
                bodies[level].append(Block("paragraph", marker.group(2).strip()))
        elif level is not None:
            bodies[level].append(block)
        elif title:
            description.append(_flatten(block))
    finish()
    return elements


def _flatten(block: Block) -> str:
    if not block.children:
        return block.text
    joiner = " " if block.text.endswith(":") else ": "
    return block.text + joiner + "; ".join(_flatten(child) for child in block.children)


def _standard_texts(blocks: list[Block]) -> list[str]:
    """One standard per top-level bullet (with its sub-bullets) or per paragraph sentence.
    A paragraph ending in ':' takes the bullets that follow it as its options."""
    texts: list[str] = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        if block.kind == "bullet":
            texts.append(_flatten(block))
        elif block.kind == "paragraph" and block.text.endswith(":"):
            options = []
            while i + 1 < len(blocks) and blocks[i + 1].kind == "bullet":
                i += 1
                options.append(_flatten(blocks[i]))
            texts.append(block.text + (" " + "; ".join(options) if options else ""))
        elif block.kind == "paragraph":
            texts.extend(sentences(block.text))
        i += 1
    return [t for t in texts if len(t.split()) >= 3]
