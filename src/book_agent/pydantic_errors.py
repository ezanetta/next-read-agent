"""Compact, one-line descriptions of pydantic validation errors.

For text that a model reads (tool errors) or a person scans (log lines): no
documentation links, no multi-line layout.
"""

from pydantic import ValidationError


def describe(error: ValidationError) -> str:
    """One "location: message" item per invalid field, joined on one line."""
    problems = (
        f"{'.'.join(str(part) for part in issue['loc']) or 'value'}: {issue['msg']}"
        for issue in error.errors(include_url=False)
    )
    return "; ".join(problems)
