"""Delimiting reader-written text inside prompts.

Part of every prompt version that uses it: change it only together with a new version.
"""

import re

_REQUEST_TAG = re.compile(r"<\s*/?\s*request\s*>", re.IGNORECASE)


def request_block(text: str) -> str:
    """The reader's text between <request> tags, with any such tags inside it removed,
    so the text can't close the block early and pose as instructions."""
    return f"<request>\n{_REQUEST_TAG.sub('', text).strip()}\n</request>"
