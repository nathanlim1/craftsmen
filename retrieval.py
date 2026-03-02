from collections import defaultdict
from typing import Dict, List, Tuple

from minecraft_client import MinecraftClient


def get_initial_terrain(
    client: MinecraftClient,
    bounds_min: Tuple[int, int, int],
    bounds_max: Tuple[int, int, int],
    padding: int = 2,
) -> List[Dict]:
    """
    One-time world scan for the entire build area plus padding.
    Returns all non-air blocks as a flat list of {x,y,z,block} dicts.

    Call this once in Builder.build() before the manager runs — subsequent
    per-chunk context lookups use this cached snapshot instead of re-querying
    the TCP bridge for every chunk dispatch.
    """
    min_x, min_y, min_z = bounds_min
    max_x, max_y, max_z = bounds_max
    padded_min = (min_x - padding, min_y - padding, min_z - padding)
    padded_max = (max_x + padding, max_y + padding, max_z + padding)

    blocks = client.get_blocks_in_bounds(padded_min, padded_max)
    return [b for b in blocks if b.get("block") not in ("air", "minecraft:air")]


def _filter_ops_in_bounds(
    ops: List[Dict],
    bounds_min: Tuple[int, int, int],
    bounds_max: Tuple[int, int, int],
) -> List[Dict]:
    min_x, min_y, min_z = bounds_min
    max_x, max_y, max_z = bounds_max
    return [
        op for op in ops
        if min_x <= op["x"] <= max_x
        and min_y <= op["y"] <= max_y
        and min_z <= op["z"] <= max_z
    ]


def get_local_context(
    initial_terrain: List[Dict],
    bounds_min: Tuple[int, int, int],
    bounds_max: Tuple[int, int, int],
    padding: int,
    ledger_ops: List[Dict],
) -> Dict:
    """
    Build local context from the cached initial terrain scan and the live ledger.
    No TCP calls — all filtering is done in-memory against the pre-scanned terrain
    and the accumulated ledger of blocks placed by the system so far.
    """
    min_x, min_y, min_z = bounds_min
    max_x, max_y, max_z = bounds_max
    padded_min = (min_x - padding, min_y - padding, min_z - padding)
    padded_max = (max_x + padding, max_y + padding, max_z + padding)

    terrain_local = _filter_ops_in_bounds(initial_terrain, padded_min, padded_max)
    ledger_local = _filter_ops_in_bounds(ledger_ops, padded_min, padded_max)

    return {
        "bounds_min": bounds_min,
        "bounds_max": bounds_max,
        "padding": padding,
        "padded_min": padded_min,
        "padded_max": padded_max,
        "terrain_blocks": terrain_local,
        "ledger_overlay": ledger_local,
    }


def format_context_as_layers(context: Dict) -> str:
    """
    Convert raw block lists into a human-readable layer summary for LLM prompts.

    Groups blocks by y-level and explicitly separates pre-existing terrain from
    blocks placed by this system (ledger), so agents can clearly distinguish
    what they built from what was already there.
    """
    bounds_min = context["bounds_min"]
    bounds_max = context["bounds_max"]
    terrain_blocks = context.get("terrain_blocks", [])
    ledger_overlay = context.get("ledger_overlay", [])

    terrain_by_y: Dict[int, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for b in terrain_blocks:
        terrain_by_y[b["y"]][b["block"]] += 1

    ledger_by_y: Dict[int, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for b in ledger_overlay:
        if b.get("block") not in ("air", "minecraft:air"):
            ledger_by_y[b["y"]][b["block"]] += 1

    lines = [f"=== Chunk bounds: {bounds_min} to {bounds_max} ===", ""]

    if terrain_by_y:
        lines.append("--- Pre-existing terrain (NOT placed by this system) ---")
        for y in sorted(terrain_by_y.keys()):
            counts = ", ".join(
                f"{count}x {block}"
                for block, count in sorted(terrain_by_y[y].items())
            )
            lines.append(f"  y={y}: {counts}")
    else:
        lines.append("--- Pre-existing terrain: none in this area ---")

    lines.append("")

    if ledger_by_y:
        lines.append("--- Agent-built blocks (placed by this system so far) ---")
        for y in sorted(ledger_by_y.keys()):
            counts = ", ".join(
                f"{count}x {block}"
                for block, count in sorted(ledger_by_y[y].items())
            )
            lines.append(f"  y={y}: {counts}")
    else:
        lines.append("--- Agent-built blocks: none yet in this area ---")

    return "\n".join(lines)
