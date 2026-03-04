import os
from dataclasses import dataclass
from typing import List, Optional, Tuple, TypedDict
from dotenv import load_dotenv
from langgraph.graph import END, StateGraph
from langchain_openai import AzureChatOpenAI, ChatOpenAI
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
    ) -> None:
        self.client = client
        self.model = model
        self.max_blocks = max_blocks
        self.max_retries = max_retries
        self._structured_model = self._create_llm().with_structured_output(PlanSchema)
        self._graph = self._build_graph()

    def _create_llm(self):
        azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        azure_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        azure_api_key = os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("AZURE_SUBSCRIPTION_KEY")

        if azure_endpoint and azure_deployment and azure_api_key:
            return AzureChatOpenAI(
                azure_endpoint=azure_endpoint,
                azure_deployment=azure_deployment,
                api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
                api_key=azure_api_key,
            )

        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            return ChatOpenAI(model=self.model, api_key=openai_key)

        raise ValueError(
            "No LLM configured. Set either Azure OpenAI env vars "
            "(AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT, AZURE_OPENAI_API_KEY) "
            "or OPENAI_API_KEY."
        )

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

        _ = move_agent
        _ = verify
        return self._order_plan(plan)

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
            error = self._format_llm_error(exc)
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

    def _format_llm_error(self, exc: Exception) -> str:
        message = str(exc)
        lowered = message.lower()

        if "404" in lowered and "resource not found" in lowered:
            endpoint = os.getenv("AZURE_OPENAI_ENDPOINT") or "<missing>"
            deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT") or "<missing>"
            api_version = os.getenv("AZURE_OPENAI_API_VERSION") or "<missing>"
            return (
                "Structured output failed: Azure returned 404 Resource not found. "
                "Check that your deployment exists and matches the exact deployment name, "
                "and that endpoint/api version are from the same Azure OpenAI resource. "
                f"Current config: endpoint={endpoint}, deployment={deployment}, api_version={api_version}. "
                "For Azure, endpoint should look like https://<resource>.openai.azure.com/"
            )

        return f"Structured output failed: {exc}"

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

    def _order_plan(self, plan: List[BlockOp]) -> List[BlockOp]:
        return sorted(plan, key=self._plan_order_key)

    def _plan_order_key(self, op: BlockOp):
        return (
            op.y,
            op.z,
            op.x,
        )

    @staticmethod
    def to_world_coords(
        plan: List[BlockOp], origin: Tuple[int, int, int]
    ) -> List[BlockOp]:
        """Translate relative plan coordinates to absolute world coordinates."""
        ox, oy, oz = origin
        return [
            BlockOp(x=op.x + ox, y=op.y + oy, z=op.z + oz, block=op.block)
            for op in plan
        ]

    @staticmethod
    def plan_to_dicts(plan: List[BlockOp]) -> list:
        """Serialize a list of BlockOps to plain dicts for the wire protocol."""
        return [
            {"x": op.x, "y": op.y, "z": op.z, "block": op.block}
            for op in plan
        ]

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
    from schematic import save_schem, material_list

    client = MinecraftClient()
    pos = client.get_position()
    origin = (int(pos[0]) + 3, int(pos[1]), int(pos[2]))
    end = (origin[0] + 4, origin[1] + 4, origin[2] + 4)
    palette = [
        "minecraft:oak_planks",
        "minecraft:oak_log",
        "minecraft:glass",
        "minecraft:oak_stairs",
        "minecraft:oak_slab",
        "minecraft:oak_door",
    ]
    builder = Builder(client)
    plan = builder.build(
        prompt="Build a small and simple garden",
        bounds_min=origin,
        bounds_max=end,
        palette=palette,
    )
    print(f"Plan: {len(plan)} block operations")

    # Materials check
    for block, count in sorted(material_list(plan).items()):
        print(f"  {block}: {count}")

    # Save schematic and start Baritone #build
    size = (5, 5, 5)
    schem_path, schem_name = save_schem(plan, size)
    print(f"Schematic saved to {schem_path}")
    result = client.build_schematic(schem_name, *origin)
    print(f"Result: {result}")
    client.close()
