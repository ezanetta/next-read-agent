"""What makes up a request parser prompt version: its id, system prompt and user prompt."""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class ParserPrompt:
    prompt_id: str
    system: str
    build_user_prompt: Callable[[str], str]
