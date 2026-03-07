"""
Validation agent that inspects sub-builder output and returns fix ops for
missing or malformed elements (walls, roofs, etc.).
"""

import os
from collections import defaultdict
from typing import List, Tuple

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI, ChatOpenAI
from pydantic import BaseModel, Field

from src.agents.builder import BlockOp, BlockOpSchema

load_dotenv()


class FixOpsSchema(BaseModel):
    """Structured response for the validation LLM."""

    ops: List[BlockOpSchema] = Field(
        default_factory=list,
        description="List of {x, y, z, block} to fix missing or malformed elements. Empty if build is OK.",
    )


class ValidationAgent:
    """Validates sub-builder output and returns fix ops for issues."""

    def __init__(self, model: str = "gpt-4o-mini") -> None:
        self.model = model
        self._llm = self._create_llm().with_structured_output(FixOpsSchema)

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
            "or OPENAI_API_KEY."
        )

    def validate(
        self,
        query: str,
        size: Tuple[int, int, int],
        placed_blocks: List[BlockOp],
        palette: List[str],
    ) -> List[BlockOp]:
        """
        Analyze placed blocks against the build request. Return fix ops for
        missing or malformed elements (walls, roofs, etc.), or empty if OK.

        All coordinates are zone-relative (0-indexed within the zone).
        """
        width, height, length = size
        palette_set = set(palette)
        palette_set.add("minecraft:air")

        blocks_text = self._format_blocks(placed_blocks)
        palette_text = ", ".join(palette)

        system_text = (
            "You are a Minecraft build validator. You receive a build request, "
            "the zone dimensions, and the blocks that were placed by a sub-builder. "
            "Your job is to detect missing or malformed elements: incomplete walls, "
            "missing roofs, gaps, structural issues, etc. "
            "If the build looks complete and correct, return an empty ops list. "
            "If you find issues, return block ops to fix them. "
            "Use zone-relative coordinates (0 <= x < width, 0 <= y < height, 0 <= z < length). "
            "Only use blocks from the palette. You may use minecraft:air to clear blocks."
        )

        user_text = (
            f"Build request: {query}\n"
            f"Zone size: width={width}, height={height}, length={length}\n"
            f"Palette: {palette_text}\n\n"
            f"Blocks placed by sub-builder:\n{blocks_text}\n\n"
            "Are there missing walls, roofs, gaps, or other issues? "
            "If yes, return ops to fix them. If no, return empty ops."
        )

        messages = [
            SystemMessage(content=system_text),
            HumanMessage(content=user_text),
        ]

        try:
            response: FixOpsSchema = self._llm.invoke(messages)
        except Exception as exc:
            print(f"  [Validator] LLM error: {exc}")
            return []

        fix_ops: List[BlockOp] = []
        for item in response.ops or []:
            x, y, z = item.x, item.y, item.z
            block = (item.block or "").strip().lower()
            if x is None or y is None or z is None or not block:
                continue
            if not block.startswith("minecraft:"):
                block = f"minecraft:{block}" if block else "minecraft:air"
            if block not in palette_set:
                continue
            if 0 <= x < width and 0 <= y < height and 0 <= z < length:
                fix_ops.append(BlockOp(x=int(x), y=int(y), z=int(z), block=block))

        return fix_ops

    @staticmethod
    def _format_blocks(blocks: List[BlockOp]) -> str:
        """Compact representation of blocks grouped by type."""
        grouped: dict = defaultdict(list)
        for op in blocks:
            if op.block != "minecraft:air":
                grouped[op.block].append(f"({op.x},{op.y},{op.z})")
        lines = []
        for block, coords in sorted(grouped.items()):
            lines.append(f"  {block}: {', '.join(coords)}")
        return "\n".join(lines) if lines else "  (none)"
