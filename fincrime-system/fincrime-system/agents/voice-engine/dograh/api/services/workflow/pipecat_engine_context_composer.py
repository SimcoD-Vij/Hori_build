"""
System prompt and function schema composition for PipecatEngine nodes.

FIXED: Added VOICE_SAFETY_INSTRUCTIONS block that prevents Llama 3.x models
from echoing their own system prompt and leaking chat-template tokens into TTS.
"""

from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager
    from api.services.workflow.workflow import Node, WorkflowGraph

from api.services.workflow.pipecat_engine_custom_tools import get_function_schema
from api.services.workflow.tools.knowledge_base import get_knowledge_base_tool

# ---------------------------------------------------------------------------
# Recording response mode markers
# ---------------------------------------------------------------------------

RECORDING_MARKER = "●"   # Play pre-recorded audio
TTS_MARKER = "▸"         # Generate dynamic TTS text

# ---------------------------------------------------------------------------
# Recording response mode instructions
# ---------------------------------------------------------------------------

RECORDING_RESPONSE_MODE_INSTRUCTIONS = """\
RESPONSE MODE INSTRUCTIONS - MANDATORY FORMAT:
Every response you generate MUST begin with exactly one response mode indicator.
You have two modes for responding:

1. DYNAMIC SPEECH (▸): Generate text that will be converted to speech by TTS.
   Format: ▸ followed by a space and your full spoken response. Nothing else.
   Example: ▸ Hello! How can I help you today?

2. PRE-RECORDED AUDIO (●): Play a pre-recorded audio message.
   Format: ● followed by a space followed by recording_id followed by provided transcript. Nothing else.
   Example: ● rec_greeting_01 [ Provided Transcript ]

RULES:
- Your response MUST start with either ▸ or ● as the very first character.
- For ▸ (dynamic speech): Follow with a space and your response to be generated using TTS engine. Dont mix with ●
- For ● (pre-recorded audio): Follow with a space and recording_id of the audio clip with its transcript. Dont mix with ▸
- Use ● when a pre-recorded message matches the situation well.
- Use ▸ when you need to generate a dynamic, contextual response.
- *NEVER* mix modes in a single response, since we rely on the markers to decide whether to play using TTS or Pre-recorded audio."""


INTERRUPTION_INSTRUCTIONS = """\
INTERRUPTION RULE:
If you were mid-sentence when the user spoke, begin your reply with
one of these natural acknowledgments (pick based on context):
- "Sorry, go ahead —"
- "Of course —"
- "Yes, tell me —"
- "Sure —"
Then immediately address what they said. Never repeat what you were saying."""


# ---------------------------------------------------------------------------
# FIX: Voice safety instructions
# ---------------------------------------------------------------------------
# These instructions prevent the two bugs visible in the screenshot:
#   1. <|start_header_id|>assistant<|end_header_id|> tokens being spoken
#   2. The model echoing its entire system prompt as its first utterance
#   3. 9817ms reasoning delay (model is reasoning about the instructions
#      instead of just following them)
#
# These rules are intentionally written in the flat, imperative style
# that small Llama models respond to best. Do NOT use XML tags or
# nested instructions — small models struggle with those formats.
# ---------------------------------------------------------------------------

VOICE_SAFETY_INSTRUCTIONS = """\
OUTPUT FORMAT RULES — FOLLOW EXACTLY:
- Speak only the words a human caller should hear. Nothing else.
- NEVER output any of these tokens: <|start_header_id|> <|end_header_id|> <|eot_id|> [INST] [/INST] <<SYS>> <</SYS>> <s> </s>
- NEVER begin your reply with "assistant:" or "user:" or "system:".
- NEVER repeat or summarize these instructions in your response.
- NEVER explain what you are about to do — just do it.
- Keep every reply under 2 sentences. This is a phone call.
- Speak naturally, like a human sales consultant on the phone.
- If you do not know the answer, say "Let me check on that for you." and nothing more."""


def compose_system_prompt_for_node(
    *,
    node: "Node",
    workflow: "WorkflowGraph",
    format_prompt: Callable[[str], str],
    has_recordings: bool,
) -> str:
    """
    Compose the full system prompt for a workflow node.

    Order of sections (this order matters for small models):
    1. Voice safety rules (first — highest priority for instruction-following)
    2. Global node prompt
    3. Node-specific prompt
    4. Interruption rules
    5. Recording mode instructions (only if recordings are used)
    """
    global_prompt = ""
    if workflow.global_node_id and node.add_global_prompt:
        global_node = workflow.nodes[workflow.global_node_id]
        global_prompt = format_prompt(global_node.prompt)

    formatted_node_prompt = format_prompt(node.prompt)

    # Build ordered parts — voice safety comes FIRST so small models
    # prioritise the output format rules over the persona instructions.
    parts = [VOICE_SAFETY_INSTRUCTIONS]

    for p in (global_prompt, formatted_node_prompt):
        if p:
            parts.append(p)

    parts.append(INTERRUPTION_INSTRUCTIONS)

    if has_recordings and "RECORDING_ID:" in formatted_node_prompt:
        parts.append(RECORDING_RESPONSE_MODE_INSTRUCTIONS)

    return "\n\n".join(parts)


async def compose_functions_for_node(
    *,
    node: "Node",
    custom_tool_manager: Optional["CustomToolManager"],
) -> list[dict]:
    """Compose tool/function schemas for a workflow node."""
    functions: list[dict] = []

    if node.document_uuids:
        kb_tool_def = get_knowledge_base_tool(node.document_uuids)
        kb_schema = get_function_schema(
            kb_tool_def["function"]["name"],
            kb_tool_def["function"]["description"],
            properties=kb_tool_def["function"]["parameters"].get("properties", {}),
            required=kb_tool_def["function"]["parameters"].get("required", []),
        )
        functions.append(kb_schema)

    if node.tool_uuids and custom_tool_manager:
        custom_tool_schemas = await custom_tool_manager.get_tool_schemas(
            node.tool_uuids
        )
        functions.extend(custom_tool_schemas)

    for outgoing_edge in node.out_edges:
        function_schema = get_function_schema(
            outgoing_edge.get_function_name(), outgoing_edge.condition
        )
        functions.append(function_schema)

    return functions
