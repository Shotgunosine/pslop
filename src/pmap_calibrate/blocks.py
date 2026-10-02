"""Split Markdown-like text into headings, paragraphs, and nested bullets."""

import re
from dataclasses import dataclass, field

_HEADING = re.compile(r"^\s{0,3}#{1,6}[ \t]*(.*?)[ \t#]*$")
_BULLET = re.compile(r"^([ \t]*)(?:[*+\-•◦▪‣·]|\d{1,2}[.)])[ \t]+(.*)$")
_ABBREVIATIONS = ("e.g.", "i.e.", "etc.", "dr.", "drs.", "mr.", "ms.", "mrs.", "prof.", "u.s.", "vs.", "approx.", "no.")


@dataclass
class Block:
    kind: str  # "heading", "paragraph", or "bullet"
    text: str
    children: list["Block"] = field(default_factory=list)


def clean(text: str) -> str:
    """Remove bold markers and collapse whitespace."""
    return " ".join(text.replace("**", "").replace("__", "").split())


def parse_blocks(text: str) -> list[Block]:
    """Return top-level blocks in order; sub-bullets are nested under their parent bullet."""
    blocks: list[Block] = []
    stack: list[tuple[int, Block]] = []  # (indent, bullet) for the current bullet nesting
    current: Block | None = None  # block that a continuation line would extend
    after_blank = False

    for raw in text.splitlines():
        line = raw.expandtabs(4)
        if not line.strip():
            after_blank = True
            continue
        heading = _HEADING.match(line)
        bullet = _BULLET.match(line)
        if heading:
            blocks.append(Block("heading", heading.group(1)))
            stack, current = [], None
        elif bullet:
            indent = len(bullet.group(1))
            block = Block("bullet", bullet.group(2))
            while stack and stack[-1][0] >= indent:
                stack.pop()
            (stack[-1][1].children if stack else blocks).append(block)
            stack.append((indent, block))
            current = block
        elif current is not None and (
            not after_blank or (current.kind == "bullet" and line[:1].isspace())
        ):
            # A wrapped line (common in PDF text) or an indented continuation of a bullet.
            current.text += " " + line.strip()
        else:
            current = Block("paragraph", line.strip())
            blocks.append(current)
            stack = []
        after_blank = False
    for block in walk(blocks):
        block.text = clean(block.text)
    return blocks


def walk(blocks: list[Block]):
    for block in blocks:
        yield block
        yield from walk(block.children)


def sentences(text: str) -> list[str]:
    """Split a paragraph into sentences, keeping common abbreviations intact."""
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(\"“])", text.strip())
    merged: list[str] = []
    for part in parts:
        if merged and merged[-1].lower().endswith(_ABBREVIATIONS):
            merged[-1] += " " + part
        else:
            merged.append(part)
    return [s for s in merged if s]
