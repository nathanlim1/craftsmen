import time
from dataclasses import dataclass
from typing import List, Optional, Tuple, TypedDict
from dotenv import load_dotenv
from langgraph.graph import END, StateGraph
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from minecraft_client import MinecraftClient


load_dotenv()

@dataclass
class BlockOp:
    x: int
    y: int
    z: int
    block: str


class BlockOpSchema(BaseModel):  # Structured response for LLM via langchain
    x: int = Field(description="Relative x coordinate within bounds.")
    y: int = Field(description="Relative y coordinate within bounds.")
    z: int = Field(description="Relative z coordinate within bounds.")
    block: str = Field(description="Block id, e.g. minecraft:oak_planks.")


class PlanSchema(BaseModel):  # Structured response for LLM via langchain
    ops: List[BlockOpSchema]


class BuilderState(TypedDict, total=False):
    prompt: str
    bounds_min: Tuple[int, int, int]
    bounds_max: Tuple[int, int, int]
    size: Tuple[int, int, int]
    palette: List[str]
    max_blocks: int
    attempts: int
    plan: List[BlockOp]
    error: Optional[str]
    last_error: Optional[str]


class Builder:
    def __init__(
        self,
        client: MinecraftClient,
        model: str = "gpt-5.1",
        max_blocks: int = 600,
        max_retries: int = 2,
        throttle_seconds: float = 0.05,
    ) -> None:
        self.client = client
        self.model = model
        self.max_blocks = max_blocks
        self.max_retries = max_retries
        self.throttle_seconds = throttle_seconds  # Time to wait between placing blocks for lag
        self._structured_model = ChatOpenAI(model=self.model).with_structured_output(PlanSchema)
        self._graph = self._build_graph()

    def build(
        self,
        prompt: str,
        bounds_min: Tuple[int, int, int],
        bounds_max: Tuple[int, int, int],
        palette: List[str],
        move_agent: bool = True,
        verify: bool = False,
    ) -> List[BlockOp]:
        bounds_min, bounds_max = self._normalize_bounds(bounds_min, bounds_max)
        size = self._size_from_bounds(bounds_min, bounds_max)
        palette = self._normalize_palette(palette)

        state: BuilderState = {
            "prompt": prompt,
            "bounds_min": bounds_min,
            "bounds_max": bounds_max,
            "size": size,
            "palette": palette,
            "max_blocks": self.max_blocks,
            "attempts": 0,
            "error": None,
            "last_error": None,
        }

        result = self._graph.invoke(state)
        if result.get("error"):
            raise ValueError(result["error"])
        plan = result.get("plan")
        if plan is None:
            raise RuntimeError("No plan returned from builder.")

        self._execute_plan(plan, bounds_min, move_agent=move_agent, verify=verify)
        return plan

    def _build_graph(self):
        graph = StateGraph(BuilderState)
        graph.add_node("draft_plan", self._draft_plan)
        graph.add_node("validate_plan", self._validate_plan_node)
        graph.set_entry_point("draft_plan")
        graph.add_edge("draft_plan", "validate_plan")
        graph.add_conditional_edges(
            "validate_plan",
            self._route_after_validate,
            {"retry": "draft_plan", "done": END},
        )
        return graph.compile()

    def _draft_plan(self, state: BuilderState) -> BuilderState:
        attempts = (state.get("attempts") or 0) + 1
        system_text, user_text = self._compose_prompt(
            prompt=state["prompt"],
            size=state["size"],
            palette=state["palette"],
            max_blocks=state["max_blocks"],
            last_error=state.get("last_error"),
        )

        try:
            plan = self._call_llm_for_plan(system_text, user_text)
        except Exception as exc:
            error = f"Structured output failed: {exc}"
            return {
                "attempts": attempts,
                "plan": [],
                "error": error,
                "last_error": error,
            }

        return {
            "attempts": attempts,
            "plan": plan,
            "error": None,
            "last_error": None,
        }

    def _call_llm_for_plan(self, system_text: str, user_text: str) -> List[BlockOp]:
        messages = [
            SystemMessage(content=system_text),
            HumanMessage(content=user_text),
        ]
        response: PlanSchema = self._structured_model.invoke(messages)
        return [
            BlockOp(x=op.x, y=op.y, z=op.z, block=op.block)
            for op in response.ops
        ]

    def _validate_plan_node(self, state: BuilderState) -> BuilderState:
        if state.get("error"):
            return state

        validation_error = self._validate_plan(
            state.get("plan", []),
            state["size"],
            state["palette"],
            state["max_blocks"],
        )
        if validation_error:
            return {
                "error": validation_error,
                "last_error": validation_error,
            }

        return {
            "plan": state.get("plan", []),
            "error": None,
            "last_error": None,
        }

    def _route_after_validate(self, state: BuilderState) -> str:
        if state.get("error") and (state.get("attempts") or 0) < self.max_retries:
            return "retry"
        return "done"

    def _compose_prompt(
        self,
        prompt: str,
        size: Tuple[int, int, int],
        palette: List[str],
        max_blocks: int,
        last_error: Optional[str],
    ) -> Tuple[str, str]:
        width, height, length = size
        palette_text = ", ".join(palette)
        system_text = (
            "You are a Minecraft build planner. "
            "Return only a structured plan that matches the schema: "
            "ops: list of { x:int, y:int, z:int, block:string }. "
            "Respect bounds, palette, and max block constraints."
        )
        error_hint = f"\nPrevious error: {last_error}" if last_error else ""
        user_text = (
            f"Build request: {prompt}\n"
            f"Bounds size (relative): width={width}, height={height}, length={length}\n"
            "Coordinates must satisfy: 0 <= x < width, 0 <= y < height, 0 <= z < length\n"
            f"Palette: {palette_text}\n"
            f"Max blocks: {max_blocks}\n"
            f"{error_hint}"
        )
        return system_text, user_text

    def _validate_plan(
        self,
        plan: List[BlockOp],
        size: Tuple[int, int, int],
        palette: List[str],
        max_blocks: int,
    ) -> Optional[str]:
        if len(plan) > max_blocks:
            return f"Plan has too many ops ({len(plan)} > {max_blocks})."

        width, height, length = size
        palette_set = set(palette)
        for idx, op in enumerate(plan):
            if op.x < 0 or op.x >= width or op.y < 0 or op.y >= height or op.z < 0 or op.z >= length:
                return f"Op {idx} out of bounds ({op.x},{op.y},{op.z})."
            if op.block not in palette_set:
                return f"Op {idx} uses disallowed block: {op.block}."

        return None

    def _execute_plan(
        self,
        plan: List[BlockOp],
        bounds_min: Tuple[int, int, int],
        move_agent: bool,
        verify: bool,
    ) -> None:
        base_x, base_y, base_z = bounds_min
        for op in plan:
            x = base_x + op.x
            y = base_y + op.y
            z = base_z + op.z
            if move_agent:
                self.client.move_to(x, y + 2, z)
                time.sleep(self.throttle_seconds)
            success = self.client.place_block(x, y, z, op.block)
            if not success:
                raise RuntimeError(f"Failed to place {op.block} at ({x},{y},{z}).")
            if verify:
                found = self.client.get_block_at(x, y, z)
                if found not in (op.block, f"minecraft:{op.block}"):
                    raise RuntimeError(
                        f"Verification failed at ({x},{y},{z}). Expected {op.block}, found {found}."
                    )
            time.sleep(self.throttle_seconds)

    def _normalize_bounds(
        self,
        bounds_min: Tuple[int, int, int],
        bounds_max: Tuple[int, int, int],
    ) -> Tuple[Tuple[int, int, int], Tuple[int, int, int]]:
        min_x = min(bounds_min[0], bounds_max[0])
        min_y = min(bounds_min[1], bounds_max[1])
        min_z = min(bounds_min[2], bounds_max[2])
        max_x = max(bounds_min[0], bounds_max[0])
        max_y = max(bounds_min[1], bounds_max[1])
        max_z = max(bounds_min[2], bounds_max[2])
        return (min_x, min_y, min_z), (max_x, max_y, max_z)

    def _size_from_bounds(
        self,
        bounds_min: Tuple[int, int, int],
        bounds_max: Tuple[int, int, int],
    ) -> Tuple[int, int, int]:
        return (
            bounds_max[0] - bounds_min[0] + 1,
            bounds_max[1] - bounds_min[1] + 1,
            bounds_max[2] - bounds_min[2] + 1,
        )

    def _normalize_palette(self, palette: List[str]) -> List[str]:
        normalized = []
        for block in palette:
            block_id = block.strip().lower()
            if not block_id.startswith("minecraft:"):
                raise ValueError(f"Palette block must be minecraft:* id, got {block}")
            normalized.append(block_id)
        return normalized


if __name__ == "__main__":
    # Example usage
    client = MinecraftClient()
    pos = client.get_position()
    start = (int(pos[0]) + 2, int(pos[1]), int(pos[2]))
    end = (start[0] + 6, start[1] + 4, start[2] + 6)
    palette = [
        "minecraft:oak_planks",
        "minecraft:oak_log",
        "minecraft:cobblestone",
        "minecraft:glass",
        "minecraft:oak_stairs",
        "minecraft:oak_slab",
        "minecraft:oak_door",
    ]
    builder = Builder(client)
    builder.build(
        prompt="Build a small wooden hut with a door and windows.",
        bounds_min=start,
        bounds_max=end,
        palette=palette,
        move_agent=True,
        verify=False,
    )
