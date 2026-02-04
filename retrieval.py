from typing import Dict, List, Tuple

from minecraft_client import MinecraftClient


def _filter_ops_in_bounds(
    ops: List[Dict[str, int]],
    bounds_min: Tuple[int, int, int],
    bounds_max: Tuple[int, int, int],
) -> List[Dict[str, int]]:
    min_x, min_y, min_z = bounds_min
    max_x, max_y, max_z = bounds_max
    filtered = []
    for op in ops:
        x, y, z = op["x"], op["y"], op["z"]
        if min_x <= x <= max_x and min_y <= y <= max_y and min_z <= z <= max_z:
            filtered.append(op)
    return filtered


def get_local_context(
    client: MinecraftClient,
    bounds_min: Tuple[int, int, int],
    bounds_max: Tuple[int, int, int],
    padding: int,
    ledger_ops: List[Dict[str, int]],
) -> Dict:
    min_x, min_y, min_z = bounds_min
    max_x, max_y, max_z = bounds_max
    padded_min = (min_x - padding, min_y - padding, min_z - padding)
    padded_max = (max_x + padding, max_y + padding, max_z + padding)

    blocks = client.get_blocks_in_bounds(padded_min, padded_max)
    non_air_blocks = []
    for block in blocks:
        block_id = block.get("block")
        if block_id in ("air", "minecraft:air"):
            continue
        non_air_blocks.append(block)
    ledger_local = _filter_ops_in_bounds(ledger_ops, padded_min, padded_max)

    return {
        "bounds_min": bounds_min,
        "bounds_max": bounds_max,
        "padding": padding,
        "padded_min": padded_min,
        "padded_max": padded_max,
        "blocks": non_air_blocks,
        "ledger_overlay": ledger_local,
    }
