from collections import Counter
from typing import Dict, List, Optional, Tuple

from src.agents.builder import BlockOp


class WorldState:
    """Single source of truth for all placed blocks across sub-builder calls.

    Coordinates are stored relative to the overall build origin (not world coords).
    """

    def __init__(self) -> None:
        self._blocks: Dict[Tuple[int, int, int], str] = {}

    @property
    def block_count(self) -> int:
        return len(self._blocks)

    def apply_ops(
        self,
        ops: List[BlockOp],
        zone_min: Tuple[int, int, int] = (0, 0, 0),
    ) -> List[BlockOp]:
        """Merge a sub-builder's output into the world state.

        Sub-builder ops use coordinates relative to their zone.  *zone_min*
        translates them into overall-relative coordinates.

        Air placements remove the block at that position rather than storing air.

        Returns the translated ops so the caller can forward them to the manager.
        """
        ox, oy, oz = zone_min
        translated: List[BlockOp] = []
        for op in ops:
            world_pos = (op.x + ox, op.y + oy, op.z + oz)
            if op.block == "minecraft:air":
                self._blocks.pop(world_pos, None)
            else:
                self._blocks[world_pos] = op.block
            translated.append(BlockOp(x=world_pos[0], y=world_pos[1], z=world_pos[2], block=op.block))
        return translated

    def get_blocks_in_bounds(
        self,
        bounds_min: Tuple[int, int, int],
        bounds_max: Tuple[int, int, int],
    ) -> List[BlockOp]:
        """Return every placed block whose position falls within the bounding box (inclusive)."""
        min_x, min_y, min_z = bounds_min
        max_x, max_y, max_z = bounds_max
        return [
            BlockOp(x=x, y=y, z=z, block=block)
            for (x, y, z), block in self._blocks.items()
            if min_x <= x <= max_x and min_y <= y <= max_y and min_z <= z <= max_z
        ]

    def to_block_ops(self) -> List[BlockOp]:
        """Flatten the entire state into a list of BlockOps (overall-relative coords)."""
        return [
            BlockOp(x=x, y=y, z=z, block=block)
            for (x, y, z), block in self._blocks.items()
        ]

    def bounding_box(self) -> Optional[Tuple[Tuple[int, int, int], Tuple[int, int, int]]]:
        """Return ``(min_corner, max_corner)`` encompassing all placed blocks, or *None* if empty."""
        if not self._blocks:
            return None
        xs = [p[0] for p in self._blocks]
        ys = [p[1] for p in self._blocks]
        zs = [p[2] for p in self._blocks]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def summary(self) -> str:
        """Human-readable summary of the current state."""
        if not self._blocks:
            return "World state is empty (0 blocks placed)."
        counts: Dict[str, int] = Counter(self._blocks.values())
        lines = [f"Total blocks placed: {len(self._blocks)}"]
        for block, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {block}: {count}")
        bb = self.bounding_box()
        if bb:
            lines.append(f"Bounding box: {bb[0]} to {bb[1]}")
        return "\n".join(lines)
