"""Split a narrative into accomplishment items, the unit Jev assesses one at a time."""

import re
from dataclasses import dataclass

from .blocks import parse_blocks, sentences

# The employee's own calibration-factor tags, e.g. "[OV, TE]".
_TAGS = re.compile(r"\s*\[\s*((?:OV|QR|TE)(?:\s*,\s*(?:OV|QR|TE))*)\s*\]", re.IGNORECASE)
MIN_BULLETS = 3


@dataclass
class Item:
    index: int
    text: str  # as written
    content: str  # factor tags removed; this is what Jev assesses
    tags: frozenset[str]
    section: str | None
    parent: int | None  # index of the parent bullet, for sub-bullets
    source: str  # "narrative" or "rating official"


def split_items(text: str, source: str = "narrative", start: int = 0) -> tuple[list[Item], int]:
    """Return (items, skipped paragraph count).

    Bullets (and sub-bullets) are items. If there are fewer than MIN_BULLETS bullets, the text is
    treated as prose and every sentence is an item. Paragraphs in a bulleted narrative (such as
    a "Calibration factor key" line) are skipped and counted.
    """
    blocks = parse_blocks(text)
    bullet_count = sum(1 for _ in _bullets(blocks, None))
    items: list[Item] = []
    skipped = 0
    section = None

    def add(raw: str, parent: int | None) -> int:
        tags = frozenset(t.strip().upper() for m in _TAGS.finditer(raw) for t in m.group(1).split(","))
        item = Item(start + len(items), raw, _TAGS.sub("", raw).strip(), tags, section, parent, source)
        items.append(item)
        return item.index

    def add_bullet(block, parent):
        index = add(block.text, parent)
        for child in block.children:
            add_bullet(child, index)

    for block in blocks:
        if block.kind == "heading":
            section = block.text or section
        elif block.kind == "bullet":
            add_bullet(block, None)
        elif bullet_count >= MIN_BULLETS:
            skipped += 1
        else:
            for sentence in sentences(block.text):
                add(sentence, None)
    return [i for i in items if len(i.content.split()) >= 3], skipped


def _bullets(blocks, parent):
    for block in blocks:
        if block.kind == "bullet":
            yield block
            yield from _bullets(block.children, block)
