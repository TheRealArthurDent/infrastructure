"""The table of contents at the top of ansible/README.md links every section heading, with GitHub's anchors."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
README = ROOT / "ansible" / "README.md"
TOC_LEVELS = (2, 3)


def headings(text):
    """(level, title) of each heading outside fenced code blocks."""
    found, fenced = [], False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
            continue
        match = re.match(r"(#{1,6}) (.+)", line)
        if match and not fenced:
            found.append((len(match.group(1)), match.group(2).strip()))
    return found


def anchor(title):
    """GitHub's heading anchor: lower case, punctuation dropped, blanks to hyphens."""
    title = re.sub(r"`([^`]*)`", r"\1", title).lower()
    return re.sub(r"[^\w\- ]", "", title).replace(" ", "-")


def toc_links(text):
    """Anchors linked from the list between the title and the first section."""
    head = text.split("\n## ", 1)[0]
    return re.findall(r"^\s*- \[[^\]]+\]\(#([^)]+)\)", head, re.MULTILINE)


def test_headings_have_unique_anchors():
    anchors = [anchor(title) for _, title in headings(README.read_text())]
    assert len(anchors) == len(set(anchors))


def test_toc_lists_every_section_in_order():
    text = README.read_text()
    expected = [anchor(title) for level, title in headings(text) if level in TOC_LEVELS]
    assert toc_links(text) == expected
