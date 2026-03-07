"""
Block IDs that Baritone cannot reliably place (doors, beds, tall plants, etc.).
Used by schematic writer and palette validation.
"""

import json
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
BLACKLIST_FILE = os.path.join(_PROJECT_ROOT, "config", "blacklisted_blocks.json")

_DEFAULT_BLACKLISTED_BLOCKS = frozenset({
    "minecraft:oak_door",
    "minecraft:spruce_door",
    "minecraft:birch_door",
    "minecraft:jungle_door",
    "minecraft:acacia_door",
    "minecraft:dark_oak_door",
    "minecraft:mangrove_door",
    "minecraft:cherry_door",
    "minecraft:bamboo_door",
    "minecraft:crimson_door",
    "minecraft:warped_door",
    "minecraft:iron_door",
    "minecraft:white_bed",
    "minecraft:orange_bed",
    "minecraft:magenta_bed",
    "minecraft:light_blue_bed",
    "minecraft:yellow_bed",
    "minecraft:lime_bed",
    "minecraft:pink_bed",
    "minecraft:gray_bed",
    "minecraft:light_gray_bed",
    "minecraft:cyan_bed",
    "minecraft:purple_bed",
    "minecraft:blue_bed",
    "minecraft:brown_bed",
    "minecraft:green_bed",
    "minecraft:red_bed",
    "minecraft:black_bed",
    "minecraft:tall_grass",
    "minecraft:large_fern",
    "minecraft:sunflower",
    "minecraft:lilac",
    "minecraft:rose_bush",
    "minecraft:peony",
    "minecraft:tall_seagrass",
    "minecraft:pitcher_plant",
    "minecraft:white_banner",
    "minecraft:black_banner",
})


def _load_blacklisted_blocks(path: str) -> frozenset[str]:
    """
    Load block IDs from a JSON file.

    Accepted formats:
      - ["minecraft:oak_door", ...]
      - {"blocks": ["minecraft:oak_door", ...]}
    """
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    if isinstance(payload, dict):
        raw_blocks = payload.get("blocks", [])
    elif isinstance(payload, list):
        raw_blocks = payload
    else:
        raise ValueError(
            f"Invalid blacklist format in {path}: expected list or object"
        )

    if not isinstance(raw_blocks, list):
        raise ValueError(
            f"Invalid blacklist format in {path}: 'blocks' must be a list"
        )

    normalized: set[str] = set()
    for entry in raw_blocks:
        if not isinstance(entry, str):
            raise ValueError(
                f"Invalid blacklist entry in {path}: expected string, got {type(entry).__name__}"
            )
        block_id = entry.strip().lower()
        if block_id:
            normalized.add(block_id)

    return frozenset(normalized)


def get_blacklisted_blocks() -> frozenset[str]:
    """Load blacklist from JSON, or fall back to default when file is missing."""
    if not os.path.exists(BLACKLIST_FILE):
        return _DEFAULT_BLACKLISTED_BLOCKS
    return _load_blacklisted_blocks(BLACKLIST_FILE)


BLACKLISTED_BLOCKS = get_blacklisted_blocks()
