"""Delimiting reader-written text inside prompts.

Part of every prompt version that uses it: change a function's output only together
with a new version of the prompts that use it.
"""

import re


def without_tags(text: str, *names: str) -> str:
    """`text` with any opening or closing <name> tags removed (any case, any spacing),
    so the text can't close its block early and pose as instructions."""
    pattern = re.compile(rf"<\s*/?\s*(?:{'|'.join(map(re.escape, names))})\s*>", re.IGNORECASE)
    return pattern.sub("", text)


def one_line(text: str) -> str:
    """Collapse all whitespace, newlines included, so a value can't start a fake list item."""
    return " ".join(text.split())


def request_block(text: str) -> str:
    """The reader's text between <request> tags, with any such tags inside it removed."""
    return f"<request>\n{without_tags(text, 'request').strip()}\n</request>"
