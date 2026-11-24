"""What makes up an agent prompt version: system prompt, tools and user prompt.

Tool descriptions are read by the model like the system prompt, so a version fixes all
three together and evals compare versions as a whole.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from book_agent.domain.library import RelatedBook
from book_agent.domain.models import Profile
from book_agent.domain.recommendations import Filters
from book_agent.llm.base import ToolSpec

UserPromptBuilder = Callable[[Profile, Filters, str | None], str]
RelatedPromptBuilder = Callable[[Profile, Filters, str | None, Sequence[RelatedBook]], str]
"""A user prompt that also lists the read books retrieved for the request (D-041)."""


@dataclass(frozen=True)
class AgentPrompt:
    prompt_id: str
    system: str
    tools: Sequence[ToolSpec]
    build_user_prompt: UserPromptBuilder
    # Versions that use library retrieval (agent_v4 on) also build the prompt with the
    # retrieved books; build_user_prompt is then the fallback when retrieval is off.
    build_related_prompt: RelatedPromptBuilder | None = None

    @property
    def uses_retrieval(self) -> bool:
        return self.build_related_prompt is not None
