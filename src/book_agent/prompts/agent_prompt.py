"""What makes up an agent prompt version: system prompt, tools and user prompt.

Tool descriptions are read by the model like the system prompt, so a version fixes all
three together and evals compare versions as a whole.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from book_agent.domain.models import Profile
from book_agent.domain.recommendations import Filters
from book_agent.llm.base import ToolSpec

UserPromptBuilder = Callable[[Profile, Filters, str | None], str]


@dataclass(frozen=True)
class AgentPrompt:
    prompt_id: str
    system: str
    tools: Sequence[ToolSpec]
    build_user_prompt: UserPromptBuilder
