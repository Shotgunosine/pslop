"""Optional name pseudonymization so personal names never leave the machine."""

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

EMPLOYEE_PLACEHOLDER = "[EMPLOYEE]"
_HONORIFICS = {"dr", "drs", "mr", "mrs", "ms", "mx", "prof"}
_WORD = r"[^\W\d_][\w'’\-]*"


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return unicodedata.normalize("NFC", "".join(c for c in decomposed if not unicodedata.combining(c)))


def _parts(name: str) -> list[str]:
    return [p for p in name.split() if p.rstrip(".").casefold() not in _HONORIFICS]


def read_names_file(path: Path) -> list[str]:
    """One name per line; blank lines and lines starting with '#' are ignored."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


class Pseudonymizer:
    """Replace listed names with placeholders like [PERSON_1] before text is sent.

    Each listed name is matched in full and by each of its parts (so "Daniel Pine" also covers
    "Dr. Pine"), case-insensitively, with or without accents. A one-word entry that is part of a
    longer listed name ("Zoë" next to "Zoë Lakey") shares that person's placeholder.
    """

    def __init__(self, names: Iterable[str] = (), employee_name: str | None = None):
        employee_name = (employee_name or "").strip() or None
        cleaned = []
        for name in [employee_name or "", *names]:
            name = unicodedata.normalize("NFC", name.strip())
            if name and name.casefold() not in {c.casefold() for c in cleaned}:
                cleaned.append(name)
        employee = cleaned.pop(0) if employee_name and cleaned else None
        covered = {p.casefold() for n in [employee or "", *cleaned] if len(_parts(n)) > 1 for p in _parts(n)}
        others = [n for n in cleaned if len(_parts(n)) > 1 or n.casefold() not in covered]

        people = [(EMPLOYEE_PLACEHOLDER, employee)] if employee else []
        people += [(f"[PERSON_{i}]", name) for i, name in enumerate(others, start=1)]
        self.placeholders: dict[str, str] = dict(people)
        variants: dict[str, str] = {}
        for placeholder, name in people:
            for variant in [name, *(p for p in _parts(name) if len(p) >= 2)]:
                for form in {variant, _strip_accents(variant)}:
                    variants.setdefault(form.casefold(), placeholder)
        self._variants = variants
        if variants:
            alternation = "|".join(re.escape(v) for v in sorted(variants, key=len, reverse=True))
            self._pattern = re.compile(rf"(?<!\w)(?:{alternation})(?!\w)", re.IGNORECASE)
        else:
            self._pattern = None

    @property
    def active(self) -> bool:
        return self._pattern is not None

    def apply(self, text: str) -> str:
        if self._pattern is None:
            return text
        text = unicodedata.normalize("NFC", text)
        return self._pattern.sub(lambda m: self._variants[m.group(0).casefold()], text)


# Capitalized words that commonly start organization, venue, or document names rather than people.
_NOT_NAMES = {
    "level", "element", "factor", "customer", "experience", "scientific", "research", "innovation",
    "presentations", "publications", "training", "mentoring", "institutional", "community",
    "service", "journal", "university", "college", "institute", "center", "centre", "branch",
    "unit", "section", "lab", "laboratory", "department", "office", "committee", "workshop",
    "seminar", "conference", "meeting", "psychiatry", "psychology", "neuroscience",
    "neurosciences", "reports", "disorders", "association", "society", "american", "national",
    "international", "health", "human", "services", "the", "a", "an", "and", "of", "in", "on",
    "for", "with", "to", "by", "at", "as", "from", "this", "that", "these", "our", "my", "we",
    "i", "it", "its", "their", "within", "during", "after", "before", "while", "provided",
    "served", "supported", "advanced", "proposed", "prevented", "organized", "invited",
    "co-authored", "supervised", "continued", "trained", "added", "reviewed", "rearranged",
    "completed", "validation", "longitudinal", "cognition", "free-text", "resting-state",
    "windows", "bayesian", "likert", "staff", "scientist", "leadership", "agenda", "dataset",
    "biological", "translational", "affective", "pediatrics", "january", "february", "march",
    "april", "may", "june", "july", "august", "september", "october", "november", "december",
    "monday", "tuesday", "wednesday", "thursday", "friday", "fiscal", "year", "fy", "hhs", "nih",
    "organizational", "value", "quality", "reliability", "timeliness", "efficiency", "results",
    "computational", "decision", "novel", "therapeutics", "medical", "informatics", "clinical",
    "mental", "depression", "anxiety", "mood", "adolescent", "environmental", "action",
    "fully", "successful", "outstanding", "unsuccessful",
}


def _is_name_word(word: str) -> bool:
    bare = re.sub(r"['’]s$", "", word)
    return (
        bare[:1].isupper()
        and not re.search(r"[A-Z]{2}", bare)
        and bare.casefold() not in _NOT_NAMES
        and bare.casefold() not in _HONORIFICS
        and len(bare) >= 2
    )


def suggest_names(text: str) -> list[tuple[str, int]]:
    """Return likely personal names in `text` with counts, in order of first appearance.

    This is a heuristic for building a names file; review its output before relying on it.
    """
    found: Counter[str] = Counter()
    order: list[str] = []

    def add(name: str) -> None:
        name = re.sub(r"['’]s$", "", name.strip(" ,.;:()"))
        if name and name not in found:
            order.append(name)
        if name:
            found[name] += 1

    # "Dr. Pine", "Drs. Lopez-Guzman and Chen", "Drs. Ballard, Baker, Gilbert, and Zarate"
    honorific = re.compile(
        rf"\b(?:Drs?|Mrs?|Ms|Mx|Prof)\.?\s+({_WORD}(?:(?:\s*,\s*(?:and\s+)?|\s+and\s+){_WORD})*)"
    )
    for match in honorific.finditer(text):
        for word in re.split(r"\s*,\s*(?:and\s+)?|\s+and\s+", match.group(1)):
            if _is_name_word(word):
                add(word)

    # Exactly two capitalized words in a row: "Zoë Lakey", "Anthony Matar". Longer runs of
    # capitalized words are usually organization or venue names, so they are skipped.
    run: list[re.Match] = []

    def flush() -> None:
        # Drop leading non-name words, such as a capitalized verb starting a bullet.
        words = list(run)
        while words and not _is_name_word(words[0].group(0)):
            words.pop(0)
        if len(words) == 2 and _is_name_word(words[1].group(0)):
            add(f"{words[0].group(0)} {words[1].group(0)}")

    for word in re.finditer(_WORD, text):
        if not word.group(0)[0].isupper():
            flush()
            run = []
        elif run and text[run[-1].end():word.start()] == " ":
            run.append(word)
        else:
            flush()
            run = [word]
    flush()
    return [(name, found[name]) for name in order]
