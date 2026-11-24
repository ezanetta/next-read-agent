"""judge_v3: judge_v2's instructions and examples, answered through a forced tool call.

v1 and v2 answered with JSON text, which failed on format: a fits_request given without
a request (F-015), a key split by a line break and a nested object (F-016). The
judgment now comes back as the arguments of `submit_judgment`, with a schema generated
from Pydantic models and the call forced by tool choice, as D-010 chose for the agent's
final answer. Runs without request text get a tool without fits_request.

Output format only: the criteria, the examples and the user prompt are v2's, so this
is not tuning on the calibration labels (D-037).
"""

from book_agent.prompts import judge_v2
from book_agent.prompts.judge_v1 import JudgeInput

PROMPT_ID = "judge_v3"
TOOL_NAME = "submit_judgment"

_V2_ANSWER = "\n\nAnswer with JSON only"
if judge_v2.SYSTEM.count(_V2_ANSWER) != 1:  # pragma: no cover - guards the derivation
    raise RuntimeError("judge_v2.SYSTEM changed: judge_v3 can no longer derive from it")

# v2's instructions and examples, unchanged: everything before its answer format.
INSTRUCTIONS = judge_v2.SYSTEM.split(_V2_ANSWER)[0]

SYSTEM = (
    INSTRUCTIONS
    + f"""

Answer by calling {TOOL_NAME} once. For each criterion, write the justification first, \
then the verdict. When there is no <request> block, the tool has no fits_request: \
judge reason_quality only."""
)

TOOL_DESCRIPTION = (
    "Submit your judgment of the recommended book: for each criterion, a one-sentence "
    "justification and then the verdict (yes, partial or no)."
)


def build_user_prompt(item: JudgeInput) -> str:
    """v2's user prompt, unchanged."""
    return judge_v2.build_user_prompt(item)
