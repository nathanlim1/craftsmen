import base64
import json
from typing import Annotated, Dict, List, Optional, Sequence, Tuple, TypedDict

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from openai import OpenAI

from agent_subbuilder import SubBuilder
from schemas import ChunkResultSchema, ChunkTaskSchema


_STANDARD_ROLES = ["foundation", "walls", "roof", "openings", "details"]


class AgentManager:
    def __init__(self, model: str) -> None:
        self.model = model

    class ManagerState(TypedDict):
        messages: Annotated[Sequence[BaseMessage], add_messages]
        iterations: int

    def run(
        self,
        prompt: str,
        bounds_min: Tuple[int, int, int],
        bounds_max: Tuple[int, int, int],
        palette: Optional[List[str]],
        max_blocks: int,
        subbuilder: SubBuilder,
        initial_terrain: List[Dict],
    ) -> Tuple[List[Dict], Optional[List[str]]]:
        client = subbuilder.client
        ledger_ops: List[Dict] = []
        build_state: Dict[str, Dict] = {}
        chosen_palette: Optional[List[str]] = palette

        def build_tools():
            @tool("dispatch_chunk")
            def dispatch_chunk_tool(
                chunk_id: str,
                bounds_min: List[int],
                bounds_max: List[int],
                role: str,
                target_connections: List[str],
                palette: Optional[List[str]] = None,
                max_ops: Optional[int] = None,
                padding: Optional[int] = None,
            ):
                """Dispatch a sub-agent to build a chunk and return its results.

                chunk_id: unique identifier (e.g. 'foundation', 'walls_north')
                bounds_min/max: 3-int lists defining the exclusive spatial region for this chunk
                role: structural role — one of: foundation, walls, roof, openings, details
                target_connections: list of adjacent chunk_ids this chunk must connect to
                palette: list of minecraft:* block ids; reuses current palette if omitted
                max_ops: max block operations for this chunk (default 200)
                padding: context padding in blocks (default 1)
                """
                nonlocal chosen_palette
                if palette:
                    chosen_palette = palette
                if not chosen_palette:
                    return {"ok": False, "error": "Palette not set."}

                task = ChunkTaskSchema(
                    chunk_id=chunk_id,
                    bounds_min=bounds_min,
                    bounds_max=bounds_max,
                    role=role,
                    dependencies=[],
                    target_connections=target_connections,
                )
                build_spec = {
                    "style_rules": [],
                    "structure_notes": [],
                    "palette": chosen_palette,
                    "max_ops_per_chunk": max_ops or 200,
                    "padding": padding or 1,
                }

                result: ChunkResultSchema = subbuilder.build_chunk(
                    task=task,
                    build_spec=build_spec,
                    initial_terrain=initial_terrain,
                    ledger_ops_ref=ledger_ops,
                    palette=chosen_palette,
                    max_ops=max_ops or 200,
                )
                if not result.success:
                    return {"ok": False, "error": result.error or "Chunk failed."}

                placements = [
                    {"x": op.x, "y": op.y, "z": op.z, "block": op.block}
                    for op in result.placements
                ]
                removals = [
                    {"x": op.x, "y": op.y, "z": op.z, "block": op.block}
                    for op in result.removals
                ]
                ledger_ops.extend(placements)
                ledger_ops.extend(removals)

                build_state[chunk_id] = {
                    "role": role,
                    "bounds_min": bounds_min,
                    "bounds_max": bounds_max,
                    "block_count": len(placements),
                    "summary": result.summary,
                    "status": "complete",
                }

                return {
                    "ok": True,
                    "chunk_id": chunk_id,
                    "role": role,
                    "summary": result.summary,
                    "blocks_placed": len(placements),
                    "palette": chosen_palette,
                }

            @tool("get_progress")
            def get_progress_tool():
                """Return current build progress as a structured state table.
                Use this to review what has been built and plan the next chunk."""
                return {
                    "completed_chunks": _format_build_state(build_state),
                    "remaining_roles": _get_remaining_roles(build_state),
                    "total_blocks_placed": len(
                        [op for op in ledger_ops if op.get("block") not in ("air", "minecraft:air")]
                    ),
                    "palette": chosen_palette,
                }

            @tool("capture_visual_feedback")
            def capture_visual_feedback_tool(stage: str):
                """Capture a screenshot of the current build and analyse it visually.

                Call this after completing a major structural stage (e.g. after walls,
                before starting the roof) to verify visual coherence, detect gaps,
                floating blocks, or misalignments before continuing.

                stage: short label for the current build stage (e.g. 'after_walls')
                """
                try:
                    result = client.take_screenshot(
                        label=f"craftsmen_{stage}.png",
                        bounds_min=bounds_min,
                        bounds_max=bounds_max,
                    )
                    path = result.get("path") if isinstance(result, dict) else None
                    if not path:
                        err = result.get("error", "Screenshot path not returned.") if isinstance(result, dict) else str(result)
                        print(f"[Visual Feedback] Screenshot failed at stage '{stage}': {err}")
                        return {"ok": False, "error": err}
                    with open(path, "rb") as f:
                        image_data = base64.b64encode(f.read()).decode("utf-8")

                    completed_summary = _format_build_state_for_prompt(build_state)
                    vision_client = OpenAI()
                    response = vision_client.chat.completions.create(
                        model=self.model,
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": (
                                            f"You are reviewing a Minecraft construction in progress.\n"
                                            f"Build request: {prompt}\n"
                                            f"Stage: {stage}\n"
                                            f"Completed chunks so far:\n{completed_summary}\n\n"
                                            "Analyse the screenshot:\n"
                                            "1. Does the structure look correct for the stage?\n"
                                            "2. Are there gaps, floating blocks, or misalignments?\n"
                                            "3. Does the palette look consistent?\n"
                                            "4. What (if anything) needs to be fixed before continuing?\n"
                                            "Be concise and specific."
                                        ),
                                    },
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": f"data:image/png;base64,{image_data}"
                                        },
                                    },
                                ],
                            }
                        ],
                    )
                    analysis = response.choices[0].message.content
                    print(f"\n[Visual Feedback — {stage}]\n{analysis}\n")
                    return {"ok": True, "stage": stage, "analysis": analysis}
                except Exception as e:
                    print(f"[Visual Feedback] Exception at stage '{stage}': {e}")
                    return {"ok": False, "error": str(e)}

            return [dispatch_chunk_tool, get_progress_tool, capture_visual_feedback_tool]

        tools = build_tools()
        tool_node = ToolNode(tools)
        model = ChatOpenAI(model=self.model).bind_tools(tools)

        def call_model(state: AgentManager.ManagerState):
            system_text, user_text = self._compose_prompt(
                prompt, bounds_min, bounds_max, chosen_palette, max_blocks, build_state
            )
            response = model.invoke(
                [SystemMessage(content=system_text), HumanMessage(content=user_text)]
                + list(state["messages"])
            )
            print("\n[Manager LLM Response]")
            print(f"content: {response.content}")
            if getattr(response, "tool_calls", None):
                print(f"tool_calls: {json.dumps(response.tool_calls, indent=2)}")
            print("[/Manager LLM Response]\n")
            return {"messages": [response], "iterations": state["iterations"] + 1}

        def should_continue(state: AgentManager.ManagerState) -> str:
            last_message = state["messages"][-1]
            if state["iterations"] >= 10:
                return "end"
            if getattr(last_message, "tool_calls", None):
                return "continue"
            return "end"

        workflow = StateGraph(AgentManager.ManagerState)
        workflow.add_node("agent", call_model)
        workflow.add_node("tools", tool_node)
        workflow.set_entry_point("agent")
        workflow.add_conditional_edges(
            "agent", should_continue, {"continue": "tools", "end": END}
        )
        workflow.add_edge("tools", "agent")
        graph = workflow.compile()

        graph.invoke({"messages": [], "iterations": 0})
        return ledger_ops, chosen_palette

    def _compose_prompt(
        self,
        prompt: str,
        bounds_min: Tuple[int, int, int],
        bounds_max: Tuple[int, int, int],
        palette: Optional[List[str]],
        max_blocks: int,
        build_state: Dict[str, Dict],
    ) -> Tuple[str, str]:
        palette_text = ", ".join(palette) if palette else "none (manager must choose)"
        build_progress_text = _format_build_state_for_prompt(build_state)
        remaining = _get_remaining_roles(build_state)
        remaining_text = ", ".join(remaining) if remaining else "all standard roles complete"

        system_text = (
            "You are a build manager orchestrating sequential chunk builds in Minecraft.\n"
            "Use tools to dispatch one chunk at a time.\n\n"
            "Build order: foundation → walls → roof → openings → details.\n"
            "Dispatch chunks in this order. Each chunk must spatially connect to the previous one.\n\n"
            "Bounds rules:\n"
            "- Floor/foundation: place at bounds_min.y\n"
            "- Walls: start at bounds_min.y+1, end at bounds_min.y+4 or higher\n"
            "- Roof: at or above top of walls\n"
            "- Always keep chunk bounds within the global bounds\n"
            "- Never ask a sub-agent to place blocks outside its declared chunk bounds\n\n"
            "Palette discipline: choose at most 3–4 block types total. Use 1–2 primary structural "
            "materials (e.g. oak_planks + stone_bricks) and only 1 accent (e.g. glass_pane for windows). "
            "Do NOT mix multiple wood types (e.g. oak + spruce + stripped_oak) in a single build — pick ONE. "
            "A coherent, minimal palette always looks better than a varied one for small structures.\n"
            "If no palette is provided, choose a simple 2–3 block palette and pass it to the first "
            "dispatch_chunk call. All subsequent chunks reuse it automatically.\n"
            "Always include functional blocks the build will need — for any structure with doors, "
            "include the matching door block (e.g. minecraft:oak_door for an oak build). "
            "For windows include minecraft:glass_pane. For lighting include minecraft:torch "
            "AND minecraft:wall_torch (they are separate block IDs).\n"
            "Use modern 1.13+ block IDs only: minecraft:oak_fence (not minecraft:fence), "
            "minecraft:oak_door (not minecraft:wooden_door). Never use pre-1.13 block names.\n\n"
            "Visual verification: after completing walls (and before starting roof), you may call "
            "capture_visual_feedback to take a screenshot and verify the build looks correct. "
            "Use the analysis to decide if any corrective chunks are needed.\n\n"
            "Completion: only stop when the build is structurally complete and visually coherent "
            "for the prompt. All major roles (foundation, walls, roof, openings) must be present.\n\n"
            "When fully done, respond with a JSON object: {\"summary\": \"...\"}."
        )
        user_text = (
            f"Build request: {prompt}\n"
            f"Global bounds: min={bounds_min} max={bounds_max}\n"
            f"Palette: {palette_text}\n"
            f"Max blocks total: {max_blocks}\n\n"
            f"=== Build progress ===\n"
            f"{build_progress_text}\n"
            f"Remaining roles not yet started: {remaining_text}\n\n"
            "Decide the next chunk and call dispatch_chunk, or finish if all roles are complete."
        )
        return system_text, user_text


def _format_build_state(build_state: Dict[str, Dict]) -> List[Dict]:
    """Return the build state as a list of structured dicts (for tool responses)."""
    return [
        {
            "chunk_id": chunk_id,
            "role": state["role"],
            "bounds_min": state["bounds_min"],
            "bounds_max": state["bounds_max"],
            "blocks_placed": state["block_count"],
            "summary": state["summary"],
            "status": state["status"],
        }
        for chunk_id, state in build_state.items()
    ]


def _format_build_state_for_prompt(build_state: Dict[str, Dict]) -> str:
    """Render the build state as a human-readable table for injection into the manager prompt."""
    if not build_state:
        return "  No chunks completed yet."
    lines = []
    for chunk_id, state in build_state.items():
        bmin = state["bounds_min"]
        bmax = state["bounds_max"]
        lines.append(
            f"  [{state['role'].upper()}] id={chunk_id!r}  "
            f"bounds={bmin} → {bmax}  "
            f"{state['block_count']} blocks\n"
            f"    \"{state['summary']}\""
        )
    return "\n".join(lines)


def _get_remaining_roles(build_state: Dict[str, Dict]) -> List[str]:
    """Return standard structural roles that have not yet been started."""
    completed_roles = {state["role"].lower() for state in build_state.values()}
    return [r for r in _STANDARD_ROLES if r not in completed_roles]
