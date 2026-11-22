"""agent_v3: agent_v2 with baseline_v2's user prompt, which states the minimum page count.

Only the user prompt changes (D-026); the system prompt and the tool descriptions are
agent_v2's. Frozen once used by evals: changes go in agent_v4.
"""

from book_agent.prompts import agent_v2, baseline_v2
from book_agent.prompts.agent_prompt import AgentPrompt

PROMPT_ID = "agent_v3"

SYSTEM = agent_v2.SYSTEM  # unchanged
TOOLS = agent_v2.TOOLS  # unchanged
build_user_prompt = baseline_v2.build_user_prompt

PROMPT = AgentPrompt(
    prompt_id=PROMPT_ID, system=SYSTEM, tools=TOOLS, build_user_prompt=build_user_prompt
)
