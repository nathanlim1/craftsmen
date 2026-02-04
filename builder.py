from dataclasses import dataclass
from typing import List, Tuple, Optional
from dotenv import load_dotenv
from minecraft_client import MinecraftClient
from agent_manager import AgentManager
from agent_subbuilder import SubBuilder


load_dotenv()

@dataclass
class BlockOp:
    x: int
    y: int
    z: int
    block: str


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
        self._manager = AgentManager(model=self.model)
        self._subbuilder = SubBuilder(client=self.client, model=self.model)

    def build(
        self,
        prompt: str,
        bounds_min: Tuple[int, int, int],
        bounds_max: Tuple[int, int, int],
        palette: Optional[List[str]] = None,
    ) -> List[BlockOp]:
        bounds_min, bounds_max = self._normalize_bounds(bounds_min, bounds_max)

        ledger_ops, summaries, chosen_palette = self._manager.run(
            prompt=prompt,
            bounds_min=bounds_min,
            bounds_max=bounds_max,
            palette=palette,
            max_blocks=self.max_blocks,
            subbuilder=self._subbuilder,
        )
        if not chosen_palette:
            raise RuntimeError("Manager did not choose a palette.")

        palette = self._normalize_palette(chosen_palette)
        plan_ops = [
            BlockOp(x=op["x"], y=op["y"], z=op["z"], block=op["block"])
            for op in ledger_ops
            if op.get("block") != "minecraft:air"
        ]
        return plan_ops

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

    def _normalize_palette(self, palette: List[str]) -> List[str]:
        normalized = []
        for block in palette:
            block_id = block.strip().lower()
            if not block_id.startswith("minecraft:"):
                raise ValueError(f"Palette block must be minecraft:* id, got {block}")
            normalized.append(block_id)
        return normalized
