import json
import time
from typing import Annotated, Dict, List, Optional, Sequence, Tuple, TypedDict

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from minecraft_client import MinecraftClient
from retrieval import format_context_as_layers, get_local_context
from schemas import BlockOpSchema, ChunkResultSchema, ChunkTaskSchema


class SubBuilder:
    def __init__(self, client: MinecraftClient, model: str) -> None:
        self.client = client
        self.model = model

    class AgentState(TypedDict):
        messages: Annotated[Sequence[BaseMessage], add_messages]
        iterations: int

    def build_chunk(
        self,
        task: ChunkTaskSchema,
        build_spec: Dict,
        initial_terrain: List[Dict],
        ledger_ops_ref: List[Dict],
        palette: List[str],
        max_ops: int,
        max_steps: int = 30,
    ) -> ChunkResultSchema:
        tools = self._build_tools(
            task, build_spec, initial_terrain, ledger_ops_ref, palette, max_ops
        )
        tool_node = ToolNode(tools)
        model = ChatOpenAI(model=self.model).bind_tools(tools)

        initial_context = get_local_context(
            initial_terrain=initial_terrain,
            bounds_min=tuple(task.bounds_min),
            bounds_max=tuple(task.bounds_max),
            padding=build_spec.get("padding", 1),
            ledger_ops=ledger_ops_ref,
        )

        def call_model(state: SubBuilder.AgentState):
            system_text, user_text = self._compose_prompt(
                task, build_spec, initial_context, palette, max_ops
            )
            response = model.invoke(
                [SystemMessage(content=system_text), HumanMessage(content=user_text)]
                + list(state["messages"])
            )
            print("\n[Subbuilder LLM Response]")
            print(f"content: {response.content}")
            if getattr(response, "tool_calls", None):
                print(f"tool_calls: {json.dumps(response.tool_calls, indent=2)}")
            print("[/Subbuilder LLM Response]\n")
            return {"messages": [response], "iterations": state["iterations"] + 1}

        def should_continue(state: SubBuilder.AgentState) -> str:
            last_message = state["messages"][-1]
            if state["iterations"] >= max_steps:
                return "end"
            if getattr(last_message, "tool_calls", None):
                return "continue"
            return "end"

        workflow = StateGraph(SubBuilder.AgentState)
        workflow.add_node("agent", call_model)
        workflow.add_node("tools", tool_node)
        workflow.set_entry_point("agent")
        workflow.add_conditional_edges(
            "agent", should_continue, {"continue": "tools", "end": END}
        )
        workflow.add_edge("tools", "agent")
        graph = workflow.compile()

        result = graph.invoke({"messages": [], "iterations": 0})
        messages = list(result["messages"])
        placements, removals = self._collect_executed_ops(messages)
        summary = self._extract_summary(messages)

        if not placements and not removals:
            return ChunkResultSchema(
                chunk_id=task.chunk_id,
                placements=[],
                removals=[],
                summary=summary or "No ops applied.",
                success=True,
                error=None,
            )

        return ChunkResultSchema(
            chunk_id=task.chunk_id,
            placements=[BlockOpSchema(**op) for op in placements],
            removals=[BlockOpSchema(**op) for op in removals],
            summary=summary or "Chunk completed.",
            success=True,
            error=None,
        )

    def _compose_prompt(
        self,
        task: ChunkTaskSchema,
        build_spec: Dict,
        initial_context: Dict,
        palette: List[str],
        max_ops: int,
    ) -> Tuple[str, str]:
        palette_text = ", ".join(palette)
        context_summary = format_context_as_layers(initial_context)

        role = task.role.lower()

        if role == "details":
            role_rules = (
                "DETAILS ROLE — decoration only:\n"
                "- NEVER remove or overwrite blocks that are already present in 'Agent-built blocks'. "
                "Those are structural and must not be disturbed.\n"
                "- Only place blocks at positions that are currently AIR (not listed in agent-built blocks).\n"
                "- Suitable decorations: wall torches on wall faces, lanterns on ceilings/floors, "
                "flower pots, carpets, banners, signs, fences as railings in open areas.\n"
                "- Do not tile trapdoors across entire wall surfaces — that destroys the walls.\n"
                "- Keep decoration sparse: a few well-placed items look better than filling every surface.\n"
            )
        elif role == "walls":
            role_rules = (
                "WALLS ROLE:\n"
                "- Place solid walls only on the perimeter. Leave the interior hollow.\n"
                "- Use a single primary wall material. You may use a secondary accent material sparingly "
                "(e.g. corner pillars), but do NOT mix 3+ materials in the same wall.\n"
                "- Build walls to a consistent height (typically 4–5 blocks tall from bounds_min.y).\n"
            )
        elif role == "roof":
            role_rules = (
                "ROOF ROLE:\n"
                "- Place the roof at exactly walls_top_y + 1 (check context for wall height).\n"
                "- A solid flat roof (full fill of bounds x/z at the roof y) or a simple gabled "
                "roof using stairs are both acceptable. Keep it clean.\n"
            )
        elif role == "foundation":
            role_rules = (
                "FOUNDATION ROLE:\n"
                "- Place a solid floor covering the full x/z footprint at bounds_min.y.\n"
                "- Use a single foundation material.\n"
            )
        elif role == "openings":
            role_rules = (
                "OPENINGS ROLE:\n"
                "- Remove wall blocks and replace with doors/windows. "
                "Do this by placing the new block (door/glass) directly — setblock overwrites the wall.\n"
                "- Doors: place only the lower half with the correct facing state. "
                "The upper half is placed automatically.\n"
                "- Windows: use glass_pane (1–2 blocks wide, 1–2 blocks tall).\n"
                "- One door on one wall, one window on one other wall is sufficient for a small house.\n"
            )
        else:
            role_rules = ""

        system_text = (
            "You are a sub-agent building a specific chunk of a Minecraft structure.\n"
            "Use the tools to place and remove blocks.\n\n"
            f"{role_rules}\n"
            "General spatial rules:\n"
            "- Work bottom-up: place lower y-layers before upper ones.\n"
            "- 'Pre-existing terrain' in the context was NOT placed by this system — do not re-place it.\n"
            "- 'Agent-built blocks' were placed by prior chunks — treat them as immovable structure.\n"
            "- Your chunk bounds define your exclusive region. Do not place blocks outside them.\n"
            "- Use get_local_context to refresh your view mid-task if needed.\n\n"
            "Block ID rules (modern 1.13+ names only):\n"
            "- Fences: minecraft:oak_fence (NOT minecraft:fence)\n"
            "- Doors: minecraft:oak_door (NOT minecraft:wooden_door)\n"
            "- Doors are two blocks tall. Place ONLY the lower half with facing state, e.g. "
            "minecraft:oak_door[facing=north,half=lower,hinge=left,open=false]. Upper half is auto-placed.\n"
            "- Torches on walls: minecraft:wall_torch[facing=north|south|east|west]\n"
            "- Torches on floor/ceiling: minecraft:torch (no facing state)\n\n"
            "When finished, respond with a JSON object: {\"summary\": \"...\"}."
        )
        user_text = (
            f"Chunk task: {task.model_dump()}\n"
            f"Build spec: {build_spec}\n"
            f"Palette: {palette_text}\n"
            f"Max ops this chunk: {max_ops}\n\n"
            f"Current world context:\n{context_summary}\n"
        )
        return system_text, user_text

    def _build_tools(
        self,
        task: ChunkTaskSchema,
        build_spec: Dict,
        initial_terrain: List[Dict],
        ledger_ops_ref: List[Dict],
        palette: List[str],
        max_ops: int,
    ):
        bounds_min = tuple(task.bounds_min)
        bounds_max = tuple(task.bounds_max)

        @tool("get_local_context")
        def get_local_context_tool(padding: Optional[int] = None):
            """Fetch the latest world context for this chunk's bounds.
            Returns a human-readable layer summary of terrain and agent-built blocks.
            Call this to refresh your view of what has been placed since your task started."""
            ctx = get_local_context(
                initial_terrain=initial_terrain,
                bounds_min=bounds_min,
                bounds_max=bounds_max,
                padding=padding or build_spec.get("padding", 1),
                ledger_ops=ledger_ops_ref,
            )
            return format_context_as_layers(ctx)

        @tool("place_ops")
        def place_ops_tool(placements: List[BlockOpSchema]):
            """Place blocks in batch and auto-validate."""
            ops = [
                {"x": op.x, "y": op.y, "z": op.z, "block": op.block}
                for op in placements
            ]
            remove_ops = [op for op in ops if op["block"] in ("air", "minecraft:air")]
            ops = [op for op in ops if op["block"] not in ("air", "minecraft:air")]
            if len(ops) > max_ops:
                print(f"[Subbuilder Validation] Reject place_ops: too many ops ({len(ops)} > {max_ops})")
                return {"ok": False, "error": "Too many ops for chunk.", "applied": []}
            ok, error = self._validate_ops(ops, [], bounds_min, bounds_max, palette)
            if not ok:
                print(f"[Subbuilder Validation] Reject place_ops: {error}")
                return {"ok": False, "error": error, "applied": []}
            if ops:
                self.client.place_ops(ops)
            if remove_ops:
                self.client.remove_ops(remove_ops)
            ok, error = self._validate_ops(ops, remove_ops, bounds_min, bounds_max, palette, verify_world=True)
            if not ok:
                print(f"[Subbuilder Validation] World verify failed place_ops: {error}")
            applied = []
            if ok:
                applied = ops + remove_ops
            return {"ok": ok, "error": error, "applied": applied}

        @tool("remove_ops")
        def remove_ops_tool(removals: List[BlockOpSchema]):
            """Remove blocks in batch and auto-validate."""
            ops = [
                {"x": op.x, "y": op.y, "z": op.z, "block": op.block}
                for op in removals
            ]
            if len(ops) > max_ops:
                print(f"[Subbuilder Validation] Reject remove_ops: too many ops ({len(ops)} > {max_ops})")
                return {"ok": False, "error": "Too many ops for chunk.", "applied": []}
            ok, error = self._validate_ops([], ops, bounds_min, bounds_max, palette)
            if not ok:
                print(f"[Subbuilder Validation] Reject remove_ops: {error}")
                return {"ok": False, "error": error, "applied": []}
            self.client.remove_ops(ops)
            ok, error = self._validate_ops([], ops, bounds_min, bounds_max, palette, verify_world=True)
            if not ok:
                print(f"[Subbuilder Validation] World verify failed remove_ops: {error}")
            return {"ok": ok, "error": error, "applied": ops if ok else []}

        return [get_local_context_tool, place_ops_tool, remove_ops_tool]

    def _collect_executed_ops(self, messages: List[BaseMessage]):
        placements: List[Dict] = []
        removals: List[Dict] = []
        for message in messages:
            if isinstance(message, ToolMessage):
                try:
                    payload = json.loads(message.content)
                except Exception:
                    continue
                applied = payload.get("applied", [])
                if message.name == "place_ops":
                    placements.extend(applied)
                elif message.name == "remove_ops":
                    removals.extend(applied)
        return placements, removals

    def _extract_summary(self, messages: List[BaseMessage]) -> Optional[str]:
        for message in reversed(messages):
            if isinstance(message, ToolMessage):
                continue
            content = getattr(message, "content", "")
            if not content:
                continue
            try:
                payload = json.loads(content)
                if isinstance(payload, dict) and "summary" in payload:
                    return payload["summary"]
            except Exception:
                continue
        return None

    def _validate_ops(
        self,
        placements: List[Dict],
        removals: List[Dict],
        bounds_min: Tuple[int, int, int],
        bounds_max: Tuple[int, int, int],
        palette: List[str],
        verify_world: bool = False,
        verify_retries: int = 3,
        verify_delay_seconds: float = 0.1,
    ) -> Tuple[bool, Optional[str]]:
        palette_set = set(palette)
        min_x, min_y, min_z = bounds_min
        max_x, max_y, max_z = bounds_max

        for op in placements:
            x, y, z = op["x"], op["y"], op["z"]
            if not (min_x <= x <= max_x and min_y <= y <= max_y and min_z <= z <= max_z):
                return False, f"Placement out of bounds: ({x},{y},{z})"
            if op["block"] in ("air", "minecraft:air"):
                removals.append({"x": x, "y": y, "z": z, "block": "minecraft:air"})
                continue
            # Strip block state properties (e.g. "[facing=north,half=lower]") before
            # comparing against the palette, which only stores base block IDs.
            block_base = op["block"].split("[", 1)[0]
            if block_base not in palette_set:
                return False, f"Disallowed block: {op['block']}"

        for op in removals:
            x, y, z = op["x"], op["y"], op["z"]
            if not (min_x <= x <= max_x and min_y <= y <= max_y and min_z <= z <= max_z):
                return False, f"Removal out of bounds: ({x},{y},{z})"

        if verify_world:
            for op in placements:
                if op["block"] in ("air", "minecraft:air"):
                    continue
                # Strip block states before comparing — the world query returns the
                # live block state which may differ from what was requested (e.g. doors
                # store additional state), so we only compare the base block ID.
                expected_full = op["block"].split("[", 1)[0]
                expected_base = expected_full.split("minecraft:")[-1]
                found = None
                for attempt in range(verify_retries):
                    found = self.client.get_block_at(op["x"], op["y"], op["z"])
                    found_base = found.split("[", 1)[0]
                    if found_base in (expected_full, expected_base):
                        break
                    if attempt < verify_retries - 1:
                        time.sleep(verify_delay_seconds)
                if found is None:
                    found = "unknown"
                found_base = found.split("[", 1)[0]
                if found_base not in (expected_full, expected_base):
                    print(
                        "[Subbuilder Validation] Expected "
                        f"{op['block']} at ({op['x']},{op['y']},{op['z']}), found {found}"
                    )
                    return False, f"Placement mismatch at ({op['x']},{op['y']},{op['z']})"
            for op in removals:
                found = None
                for attempt in range(verify_retries):
                    found = self.client.get_block_at(op["x"], op["y"], op["z"])
                    found_base = found.split("[", 1)[0]
                    if found_base in ("air", "minecraft:air"):
                        break
                    if attempt < verify_retries - 1:
                        time.sleep(verify_delay_seconds)
                if found is None:
                    found = "unknown"
                found_base = found.split("[", 1)[0]
                if found_base not in ("air", "minecraft:air"):
                    print(
                        "[Subbuilder Validation] Expected air at "
                        f"({op['x']},{op['y']},{op['z']}), found {found}"
                    )
                    return False, f"Removal mismatch at ({op['x']},{op['y']},{op['z']})"

        return True, None
