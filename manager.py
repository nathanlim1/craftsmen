import os
from typing import List, Tuple

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langchain_openai import AzureChatOpenAI, ChatOpenAI
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from block_ids import validate_palette
from builder import BlockOp, Builder
from minecraft_client import MinecraftClient
from validator import ValidationAgent
from world_state import WorldState

load_dotenv()

MAX_ZONE_VOLUME = 650


def _merge_ops(plan: List[BlockOp], fix_ops: List[BlockOp]) -> List[BlockOp]:
    """Merge plan with fix_ops; fix_ops overwrite overlapping positions."""
    by_pos: dict = {}
    for op in plan:
        by_pos[(op.x, op.y, op.z)] = op
    for op in fix_ops:
        by_pos[(op.x, op.y, op.z)] = op
    return list(by_pos.values())


MANAGER_SYSTEM_PROMPT = """\
You are a Minecraft construction manager. Your job is to decompose a high-level
build request into sub-tasks and delegate each one to a sub-builder agent.

You operate within an overall build volume of size {width} x {height} x {length} blocks.
All coordinates are relative to the build origin: x ranges from 0 to {max_x},
y (vertical) from 0 to {max_y}, z from 0 to {max_z}.

For each sub-task, you call the delegate_build tool with:
- query: a concise description of what to build (prefer brevity; short phrases over long prose)
- zone_min, zone_max: the construction zone where the sub-builder can place blocks
  (inclusive, relative to overall origin). Zone volume (width×height×length) must not
  exceed {max_zone_volume} blocks.
- palette: a list of minecraft block IDs appropriate for this sub-task (e.g.
  minecraft:oak_planks, minecraft:glass). Choose blocks entirely based on your own
  judgment for what fits the build. Air (minecraft:air) is always available.

Guidelines:
- One delegation per structure: each distinct structure (house, barn, well, garden,
  wall, tower, etc.) gets exactly one delegate_build call.
- Within a single structure, bundle everything in one call: foundation + walls + roof
  (and interior if part of the same structure) should be ONE sub-builder call. The
  sub-builder can plan the full structure and place all blocks in one pass.
- Do not bundle multiple structures in one call: a house and a barn are two delegations.
- Each delegate_build call should cover one complete structure.
- Sub-builders work in isolation. Prefer very concise queries: short, direct phrases
  rather than long descriptions. Include only essential detail.
- Each zone must be at most {max_zone_volume} blocks in volume (width×height×length).
- Zones may overlap if you want a later sub-builder to refine or replace earlier work
- Air (minecraft:air) is always available to every sub-builder for clearing blocks
- You have a maximum of {max_delegations} delegate_build calls
- Review the placed blocks returned after each delegation before planning the next one
- When you are satisfied with the build, stop calling tools and respond with a brief
  summary of what was constructed
"""


class DelegateBuildInput(BaseModel):
    """Input schema for the delegate_build tool."""

    query: str = Field(
        description="Concise description of what the sub-builder should construct."
    )
    zone_min: List[int] = Field(
        description=(
            "[x, y, z] minimum corner of the construction zone "
            "(inclusive, relative to overall origin)."
        )
    )
    zone_max: List[int] = Field(
        description=(
            "[x, y, z] maximum corner of the construction zone "
            "(inclusive, relative to overall origin)."
        )
    )
    palette: List[str] = Field(
        description=(
            "List of minecraft: block IDs the sub-builder may use "
            "(e.g. ['minecraft:oak_planks', 'minecraft:glass'])."
        )
    )


class Manager:
    """High-level manager agent that decomposes build requests and dispatches sub-builders."""

    def __init__(
        self,
        client: MinecraftClient,
        model: str = "gpt-5.4",
        max_delegations: int = 10,
        max_blocks_per_sub: int = 600,
        max_retries_per_sub: int = 2,
    ) -> None:
        self.client = client
        self.model = model
        self.max_delegations = max_delegations
        self.max_blocks_per_sub = max_blocks_per_sub
        self.max_retries_per_sub = max_retries_per_sub
        self._llm = self._create_llm()

    def _create_llm(self):
        azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        azure_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        azure_api_key = os.getenv("AZURE_OPENAI_API_KEY") or os.getenv(
            "AZURE_SUBSCRIPTION_KEY"
        )

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
    ) -> WorldState:
        """Decompose *prompt* into sub-tasks and build via delegated sub-builders.

        Returns the final :class:`WorldState` containing all placed blocks.
        """
        min_x = min(bounds_min[0], bounds_max[0])
        min_y = min(bounds_min[1], bounds_max[1])
        min_z = min(bounds_min[2], bounds_max[2])
        max_x = max(bounds_min[0], bounds_max[0])
        max_y = max(bounds_min[1], bounds_max[1])
        max_z = max(bounds_min[2], bounds_max[2])

        width = max_x - min_x + 1
        height = max_y - min_y + 1
        length = max_z - min_z + 1
        overall_size = (width, height, length)

        world_state = WorldState()
        delegation_count = 0

        builder = Builder(
            self.client,
            model=self.model,
            max_blocks=self.max_blocks_per_sub,
            max_retries=self.max_retries_per_sub,
        )
        validator = ValidationAgent(model=self.model)

        max_delegations = self.max_delegations

        # ── delegate_build closure (captured by the tool) ──────────────

        def _delegate_build(
            query: str,
            zone_min: List[int],
            zone_max: List[int],
            palette: List[str],
        ) -> str:
            nonlocal delegation_count
            delegation_count += 1

            if delegation_count > max_delegations:
                return (
                    f"Maximum delegation limit ({max_delegations}) reached. "
                    "No more sub-builders can be dispatched. Please provide "
                    "your final summary."
                )

            zone_min_t: Tuple[int, int, int] = tuple(zone_min)  # type: ignore[assignment]
            zone_max_t: Tuple[int, int, int] = tuple(zone_max)  # type: ignore[assignment]

            for i, (lo, hi, dim) in enumerate(
                zip(zone_min_t, zone_max_t, overall_size)
            ):
                if lo < 0 or hi >= dim:
                    axis = "xyz"[i]
                    return (
                        f"Error: zone {axis} range [{lo}, {hi}] is outside "
                        f"overall bounds [0, {dim - 1}]. Adjust and retry."
                    )

            zone_volume = (
                (zone_max_t[0] - zone_min_t[0] + 1)
                * (zone_max_t[1] - zone_min_t[1] + 1)
                * (zone_max_t[2] - zone_min_t[2] + 1)
            )
            if zone_volume > MAX_ZONE_VOLUME:
                msg = (
                    f"Zone volume {zone_volume} exceeds maximum ({MAX_ZONE_VOLUME} blocks). "
                    "Use a smaller zone."
                )
                print(f"  [Manager] {msg}")
                return f"Error: {msg}"

            sub_bounds_min = (0, 0, 0)
            sub_bounds_max = (
                zone_max_t[0] - zone_min_t[0],
                zone_max_t[1] - zone_min_t[1],
                zone_max_t[2] - zone_min_t[2],
            )

            valid_palette, invalid_blocks = validate_palette(palette)
            if invalid_blocks:
                msg = (
                    f"Invalid palette blocks (not valid Minecraft block IDs): "
                    f"{', '.join(invalid_blocks)}. Use only minecraft:* block IDs "
                    f"from the game (e.g. minecraft:oak_planks, minecraft:stone)."
                )
                print(f"  [Manager] {msg}")
                return f"Error: {msg}"

            print(
                f"  [Manager] Delegation #{delegation_count}: "
                f"query={query!r}, zone={zone_min_t}-{zone_max_t}, "
                f"palette={len(valid_palette)} blocks"
            )

            try:
                plan = builder.build(
                    prompt=query,
                    bounds_min=sub_bounds_min,
                    bounds_max=sub_bounds_max,
                    palette=valid_palette,
                )
            except (ValueError, RuntimeError) as exc:
                print(f"  [Manager] Sub-builder error: {exc}")
                return f"Sub-builder error: {exc}"

            zone_size = (
                zone_max_t[0] - zone_min_t[0] + 1,
                zone_max_t[1] - zone_min_t[1] + 1,
                zone_max_t[2] - zone_min_t[2] + 1,
            )
            fix_ops = validator.validate(
                query=query,
                size=zone_size,
                placed_blocks=plan,
                palette=valid_palette,
            )
            if fix_ops:
                print(f"  [Manager] Validator added {len(fix_ops)} fix ops")
                merged = _merge_ops(plan, fix_ops)
            else:
                merged = plan

            translated_ops = world_state.apply_ops(merged, zone_min=zone_min_t)

            print(
                f"  [Manager] Sub-builder placed {len(translated_ops)} blocks"
            )

            lines = [
                f"Sub-builder placed {len(translated_ops)} blocks in zone "
                f"{list(zone_min_t)} to {list(zone_max_t)}:",
            ]
            for op in translated_ops:
                lines.append(f"  ({op.x},{op.y},{op.z}) {op.block}")
            lines.append(f"\n{world_state.summary()}")

            return "\n".join(lines)

        # ── build the tool and agent ───────────────────────────────────

        delegate_tool = StructuredTool.from_function(
            func=_delegate_build,
            name="delegate_build",
            description=(
                "Dispatch a sub-builder agent to construct something within a "
                "specific zone. The sub-builder can only place blocks inside "
                "the zone using the given palette. Air (minecraft:air) is "
                "always available for clearing blocks."
            ),
            args_schema=DelegateBuildInput,
        )

        system_prompt = MANAGER_SYSTEM_PROMPT.format(
            width=width,
            height=height,
            length=length,
            max_x=width - 1,
            max_y=height - 1,
            max_z=length - 1,
            max_delegations=max_delegations,
            max_zone_volume=MAX_ZONE_VOLUME,
        )

        agent = create_react_agent(self._llm, tools=[delegate_tool])

        result = agent.invoke(
            {
                "messages": [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=f"Build request: {prompt}"),
                ]
            },
            config={"recursion_limit": max_delegations * 2 + 10},
        )

        # Print the final assistant summary
        for msg in reversed(result.get("messages", [])):
            content = getattr(msg, "content", None)
            tool_calls = getattr(msg, "tool_calls", None)
            if content and not tool_calls:
                print(f"\n[Manager] Final summary: {content}")
                break

        return world_state
