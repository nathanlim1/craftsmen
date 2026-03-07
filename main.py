import sys
import json
import os
import time
from typing import Any, Dict, List, Tuple

from minecraft_client import MinecraftClient
from builder import Builder
from schematic import save_schem, material_list, make_schem_name


DEFAULT_PALETTE = [
    "minecraft:oak_planks",
    "minecraft:oak_log",
    "minecraft:glass",
    "minecraft:cobblestone",
    "minecraft:oak_stairs",
    "minecraft:oak_slab",
    "minecraft:torch",
]

# Build volume (width, height, length) in blocks
DEFAULT_SIZE = (7, 7, 7)

SCAFFOLD_BLOCK = "minecraft:red_wool"
DEFAULT_MODE = "baritone"
DEFAULT_ORIGIN_OFFSET = (3, 0, 0)
DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "build_config.json")


def _load_build_config(path: str = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError(f"Build config must be a JSON object: {path}")
    return payload


def _normalize_size(raw: Any) -> Tuple[int, int, int]:
    if not isinstance(raw, list) or len(raw) != 3:
        raise ValueError("'size' must be a JSON array with 3 integers")
    size = tuple(int(v) for v in raw)
    if any(v <= 0 for v in size):
        raise ValueError("'size' values must be positive")
    return size


def _normalize_offset(raw: Any) -> Tuple[int, int, int]:
    if not isinstance(raw, list) or len(raw) != 3:
        raise ValueError("'origin_offset' must be a JSON array with 3 integers")
    return tuple(int(v) for v in raw)


def _normalize_palette(raw: Any) -> List[str]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("'palette' must be a non-empty JSON array of block IDs")
    normalized: List[str] = []
    for block in raw:
        if not isinstance(block, str):
            raise ValueError("'palette' entries must be strings")
        block_id = block.strip().lower()
        if not block_id.startswith("minecraft:"):
            raise ValueError(f"Palette block must be minecraft:* id, got {block}")
        normalized.append(block_id)
    return normalized


def _place_plan_automatically(client: MinecraftClient, origin: Tuple[int, int, int], plan) -> None:
    world_plan = Builder.to_world_coords(plan, origin)
    total = len(world_plan)
    print("\n[3/3] Auto placing blocks via listener (/setblock) ...")
    placed = 0
    started = time.time()
    for idx, op in enumerate(world_plan, 1):
        ok = client.place_block(op.x, op.y, op.z, op.block)
        if ok:
            placed += 1
        if idx % 25 == 0 or idx == total:
            print(f"  -> Progress: {idx}/{total}")
    elapsed = time.time() - started
    print(f"  -> Auto placement complete: {placed}/{total} blocks in {elapsed:.1f}s")


def _print_startup_config(
    config_path: str,
    config_loaded: bool,
    prompt_source: str,
    prompt: str,
    mode: str,
    size: Tuple[int, int, int],
    origin_offset: Tuple[int, int, int],
    scaffold_block: str,
    palette: List[str],
) -> None:
    print("\nBuild config:")
    print(f"  Path         : {config_path}")
    print(f"  Loaded       : {'yes' if config_loaded else 'no (using defaults)'}")
    print(f"  Prompt source: {prompt_source}")
    print(f"  Prompt       : {prompt}")
    print(f"  Mode         : {mode}")
    print(f"  Size         : {size}")
    print(f"  Origin offset: {origin_offset}")
    print(f"  Scaffold     : {scaffold_block}")
    preview = ", ".join(palette[:5])
    suffix = " ..." if len(palette) > 5 else ""
    print(f"  Palette      : {len(palette)} blocks ({preview}{suffix})")


def main():
    config_path = os.getenv("CRAFTSMEN_BUILD_CONFIG", DEFAULT_CONFIG_PATH)
    config = _load_build_config(config_path)

    prompt = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else config.get("prompt")
    prompt_source = "cli" if len(sys.argv) > 1 else "config"
    if not prompt:
        prompt = input("What should I build? > ").strip()
        prompt_source = "interactive"
    if not prompt:
        print("No prompt provided — exiting.")
        return

    mode = str(config.get("mode", DEFAULT_MODE)).strip().lower()
    if mode not in {"baritone", "auto"}:
        raise ValueError("Config 'mode' must be either 'baritone' or 'auto'")

    size = _normalize_size(config.get("size", list(DEFAULT_SIZE)))
    palette = _normalize_palette(config.get("palette", DEFAULT_PALETTE))
    origin_offset = _normalize_offset(config.get("origin_offset", list(DEFAULT_ORIGIN_OFFSET)))
    scaffold_block = str(config.get("scaffold_block", SCAFFOLD_BLOCK)).strip().lower()
    if not scaffold_block.startswith("minecraft:"):
        raise ValueError("'scaffold_block' must be a minecraft:* block id")

    _print_startup_config(
        config_path=config_path,
        config_loaded=bool(config),
        prompt_source=prompt_source,
        prompt=prompt,
        mode=mode,
        size=size,
        origin_offset=origin_offset,
        scaffold_block=scaffold_block,
        palette=palette,
    )

    print("Connecting to Minecraft listener ...")
    client = MinecraftClient()

    # Build area starts near the player, configurable via origin_offset
    pos = client.get_position()
    origin = (
        int(pos[0]) + origin_offset[0],
        int(pos[1]) + origin_offset[1],
        int(pos[2]) + origin_offset[2],
    )
    w, h, l = size
    end = (origin[0] + w - 1, origin[1] + h - 1, origin[2] + l - 1)

    print(f"Origin  : {origin}")

    # --- 1. Plan the build via Azure OpenAI ---------------------------------
    print("\n[1/3] Generating build plan ...")
    builder = Builder(client)
    plan = builder.build(
        prompt=prompt,
        bounds_min=origin,
        bounds_max=end,
        palette=palette,
    )
    print(f"  -> {len(plan)} block operations generated")

    schem_name = None

    # --- 2. Save as .schem for Baritone (if needed) -------------------------
    if mode == "baritone":
        print("\n[2/3] Writing schematic ...")
        schem_name = make_schem_name(prompt[:30])
        schem_path, schem_name = save_schem(plan, size, filename=schem_name)
        print(f"  -> Saved to {schem_path}")
    else:
        print("\n[2/3] Skipping schematic write in auto mode ...")

    # --- Materials needed ----------------------------------------------------
    materials = material_list(plan)
    print("\n  Materials needed in inventory:")
    for block, count in sorted(materials.items()):
        print(f"    {block}: {count}")

    # --- 3. Build execution --------------------------------------------------
    if mode == "baritone":
        print("\n[3/3] Starting Baritone #build (survival mode) ...")
        result = client.build_schematic(
            filename=schem_name,
            x=origin[0],
            y=origin[1],
            z=origin[2],
            scaffold_block=scaffold_block,
        )
        print(f"  -> {result.get('message', result)}")
        print(
            f"\nBaritone is now building in-game using items from your inventory."
            f"\nScaffold block: {scaffold_block} (will be left in place for debugging)."
            f"\nUse '#stop' in chat to cancel, or '#build' to resume."
        )
    else:
        _place_plan_automatically(client, origin, plan)
        print("\nAuto mode finished. Blocks were placed directly via listener.")

    client.close()


if __name__ == "__main__":
    main()
