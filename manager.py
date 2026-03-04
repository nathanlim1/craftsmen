import os
from typing import List, Tuple

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langchain_openai import AzureChatOpenAI, ChatOpenAI
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from builder import BlockOp, Builder
from minecraft_client import MinecraftClient
from world_state import WorldState

load_dotenv()


MANAGER_SYSTEM_PROMPT = """\
You are a Minecraft construction manager. Your job is to decompose a high-level
build request into sub-tasks and delegate each one to a sub-builder agent.

You operate within an overall build volume of size {width} x {height} x {length} blocks.
All coordinates are relative to the build origin: x ranges from 0 to {max_x},
y (vertical) from 0 to {max_y}, z from 0 to {max_z}.

Available palette for the entire build: {palette}

For each sub-task, you call the delegate_build tool with:
- query: a clear, detailed description of what to build
- zone_min, zone_max: the construction zone where the sub-builder can place blocks
  (inclusive, relative to overall origin)
- context_min, context_max: a bounding box of existing blocks the sub-builder can see
  for reference (should be >= the construction zone)
- palette: a subset (or all) of the available palette appropriate for this sub-task

Guidelines:
- Prefer FEWER, LARGER delegations. Bundle entire structural shells in single calls:
  e.g. "foundation + walls + roof" for one building should be ONE sub-builder call, not
  separate calls for foundation, walls, and roof. The sub-builder can plan the full
  structure and place all blocks in one pass.
- Only split into separate calls when components are truly distinct: different buildings
  (e.g. house vs barn in a village), or interior details vs exterior shell, or
  add-ons that depend on an existing structure.
- Each delegate_build call should cover a substantial, coherent chunk of the build.
- Zones may overlap if you want a later sub-builder to refine or replace earlier work
- Context bounds should typically be equal to or larger than construction bounds so the
  sub-builder can see neighboring structures for alignment
- Air (minecraft:air) is always available to every sub-builder for clearing blocks
- You have a maximum of {max_delegations} delegate_build calls
- Review the placed blocks returned after each delegation before planning the next one
- When you are satisfied with the build, stop calling tools and respond with a brief
  summary of what was constructed
"""


class DelegateBuildInput(BaseModel):
    """Input schema for the delegate_build tool."""

    query: str = Field(
        description="Clear description of what the sub-builder should construct."
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
    context_min: List[int] = Field(
        description=(
            "[x, y, z] minimum corner of the visible context window "
            "(inclusive, relative to overall origin)."
        )
    )
    context_max: List[int] = Field(
        description=(
            "[x, y, z] maximum corner of the visible context window "
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
        model: str = "gpt-5.1",
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
        overall_palette: List[str],
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

        max_delegations = self.max_delegations

        # ── delegate_build closure (captured by the tool) ──────────────

        def _delegate_build(
            query: str,
            zone_min: List[int],
            zone_max: List[int],
            context_min: List[int],
            context_max: List[int],
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
            context_min_t: Tuple[int, int, int] = tuple(context_min)  # type: ignore[assignment]
            context_max_t: Tuple[int, int, int] = tuple(context_max)  # type: ignore[assignment]

            for i, (lo, hi, dim) in enumerate(
                zip(zone_min_t, zone_max_t, overall_size)
            ):
                if lo < 0 or hi >= dim:
                    axis = "xyz"[i]
                    return (
                        f"Error: zone {axis} range [{lo}, {hi}] is outside "
                        f"overall bounds [0, {dim - 1}]. Adjust and retry."
                    )

            # Context blocks in overall-relative coords, translated to zone-relative
            context_blocks_global = world_state.get_blocks_in_bounds(
                context_min_t, context_max_t
            )
            zmin_x, zmin_y, zmin_z = zone_min_t
            context_blocks_relative = [
                BlockOp(
                    x=op.x - zmin_x,
                    y=op.y - zmin_y,
                    z=op.z - zmin_z,
                    block=op.block,
                )
                for op in context_blocks_global
            ]

            sub_bounds_min = (0, 0, 0)
            sub_bounds_max = (
                zone_max_t[0] - zone_min_t[0],
                zone_max_t[1] - zone_min_t[1],
                zone_max_t[2] - zone_min_t[2],
            )

            print(
                f"  [Manager] Delegation #{delegation_count}: "
                f"query={query!r}, zone={zone_min_t}-{zone_max_t}, "
                f"palette={len(palette)} blocks"
            )

            try:
                plan = builder.build(
                    prompt=query,
                    bounds_min=sub_bounds_min,
                    bounds_max=sub_bounds_max,
                    palette=palette,
                    context_blocks=context_blocks_relative,
                )
            except (ValueError, RuntimeError) as exc:
                return f"Sub-builder error: {exc}"

            translated_ops = world_state.apply_ops(plan, zone_min=zone_min_t)

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
            palette=", ".join(overall_palette),
            max_delegations=max_delegations,
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
