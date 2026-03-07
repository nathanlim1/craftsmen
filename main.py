import sys
import json
import os
import time
from typing import Any, Dict, List, Tuple

from minecraft_client import MinecraftClient
from builder import Builder
from manager import Manager
from schematic import save_schem, save_world_state, material_list, make_schem_name


DEFAULT_PALETTE = [
    "minecraft:oak_planks",
    "minecraft:oak_log",
    "minecraft:glass",
    "minecraft:cobblestone",
    "minecraft:oak_stairs",
    "minecraft:oak_slab",
    "minecraft:torch",
]

DEFAULT_SIZE_MULTI = (50, 15, 50)
DEFAULT_SIZE_SINGLE = (7, 7, 7)
SCAFFOLD_BLOCK = "minecraft:red_wool"
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


def _place_plan_directly(
    client: MinecraftClient,
    origin: Tuple[int, int, int],
    plan: List,
) -> None:
    """Place blocks directly via /setblock (instant placement)."""
    world_plan = Builder.to_world_coords(plan, origin)
    total = len(world_plan)
    print("\n[3/3] Placing blocks directly via listener (/setblock) ...")
    placed = 0
    started = time.time()
    for idx, op in enumerate(world_plan, 1):
        if op.block == "minecraft:air":
            continue
        ok = client.place_block(op.x, op.y, op.z, op.block)
        if ok:
            placed += 1
        if idx % 50 == 0 or idx == total:
            print(f"  -> Progress: {idx}/{total}")
    elapsed = time.time() - started
    print(f"  -> Placement complete: {placed}/{total} blocks in {elapsed:.1f}s")


def _print_startup_config(
    config_path: str,
    config_loaded: bool,
    prompt_source: str,
    prompt: str,
    agent_mode: str,
    placement_mode: str,
    size: Tuple[int, int, int],
    origin_offset: Tuple[int, int, int],
    scaffold_block: str,
    palette: List[str],
) -> None:
    print("\nBuild config:")
    print(f"  Path            : {config_path}")
    print(f"  Loaded          : {'yes' if config_loaded else 'no (using defaults)'}")
    print(f"  Prompt source   : {prompt_source}")
    print(f"  Prompt          : {prompt}")
    print(f"  Agent mode      : {agent_mode}")
    print(f"  Placement mode  : {placement_mode}")
    print(f"  Size            : {size}")
    print(f"  Origin offset   : {origin_offset}")
    print(f"  Scaffold        : {scaffold_block}")
    if agent_mode == "single":
        preview = ", ".join(palette[:5])
        suffix = " ..." if len(palette) > 5 else ""
        print(f"  Palette         : {len(palette)} blocks ({preview}{suffix})")
    else:
        print("  Palette         : (ignored — multi-agent uses sub-builder palettes)")


def main():
    config_path = os.getenv("CRAFTSMEN_BUILD_CONFIG", DEFAULT_CONFIG_PATH)
    config = _load_build_config(config_path)

    prompt = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else config.get("prompt")
    prompt_source = "cli" if len(sys.argv) > 1 else "config"
    if not prompt:
        prompt = input("What should I build? > ").strip()
        prompt_source = "interactive"
    if not prompt:
        print("No prompt provided — exiting.")
        return

    agent_mode = str(config.get("agent_mode", "multi")).strip().lower()
    if agent_mode not in {"single", "multi"}:
        raise ValueError("Config 'agent_mode' must be either 'single' or 'multi'")

    raw_placement = str(config.get("placement_mode", config.get("mode", "baritone"))).strip().lower()
    if raw_placement in {"auto", "instant"}:
        placement_mode = "instant"
    elif raw_placement == "baritone":
        placement_mode = "baritone"
    else:
        raise ValueError("Config 'placement_mode' must be 'baritone', 'instant', or 'auto'")

    default_size = DEFAULT_SIZE_SINGLE if agent_mode == "single" else DEFAULT_SIZE_MULTI
    size = _normalize_size(config.get("size", list(default_size)))
    origin_offset = _normalize_offset(config.get("origin_offset", list(DEFAULT_ORIGIN_OFFSET)))
    scaffold_block = str(config.get("scaffold_block", SCAFFOLD_BLOCK)).strip().lower()
    if not scaffold_block.startswith("minecraft:"):
        raise ValueError("'scaffold_block' must be a minecraft:* block id")

    palette = _normalize_palette(config.get("palette", DEFAULT_PALETTE))

    _print_startup_config(
        config_path=config_path,
        config_loaded=bool(config),
        prompt_source=prompt_source,
        prompt=prompt,
        agent_mode=agent_mode,
        placement_mode=placement_mode,
        size=size,
        origin_offset=origin_offset,
        scaffold_block=scaffold_block,
        palette=palette,
    )

    print("Connecting to Minecraft listener ...")
    client = MinecraftClient()

    pos = client.get_position()
    origin = (
        int(pos[0]) + origin_offset[0],
        int(pos[1]) + origin_offset[1],
        int(pos[2]) + origin_offset[2],
    )
    w, h, ln = size
    end = (origin[0] + w - 1, origin[1] + h - 1, origin[2] + ln - 1)

    print(f"Origin  : {origin}")

    if agent_mode == "single":
        # --- Single-agent: Builder directly ---
        print("\n[1/3] Generating build plan (single-agent) ...")
        builder = Builder(client)
        plan = builder.build(
            prompt=prompt,
            bounds_min=origin,
            bounds_max=end,
            palette=palette,
        )
        print(f"  -> {len(plan)} block operations generated")

        if len(plan) == 0:
            print("No blocks to place — nothing to build.")
            client.close()
            return

        schem_name = make_schem_name(prompt[:30])
        if placement_mode == "baritone":
            print("\n[2/3] Writing schematic ...")
            schem_path, schem_name = save_schem(plan, size, filename=schem_name)
            print(f"  -> Saved to {schem_path}")
        else:
            print("\n[2/3] Skipping schematic write (instant placement) ...")

        materials = material_list(plan)
        print("\n  Materials needed in inventory:")
        for block, count in sorted(materials.items()):
            print(f"    {block}: {count}")

        result = client.ensure_materials_if_creative(materials, scaffold_block)
        if result.get("gave"):
            print("  -> Creative mode: materials + scaffold added to inventory")

        if placement_mode == "baritone":
            print("\n[3/3] Starting Baritone #build ...")
            result = client.build_schematic(
                filename=schem_name,
                x=origin[0],
                y=origin[1],
                z=origin[2],
                scaffold_block=scaffold_block,
            )
            print(f"  -> {result.get('message', result)}")
            print(
                "\nBaritone is now building in-game using items from your inventory.\n"
                f"Scaffold block: {scaffold_block} (will be left in place for debugging).\n"
                "Use '#stop' in chat to cancel, or '#build' to resume."
            )
        else:
            _place_plan_directly(client, origin, plan)
            print("\nInstant placement finished.")

    else:
        # --- Multi-agent: Manager + sub-builders ---
        print("\n[1/3] Manager is decomposing build and delegating to sub-builders ...")
        manager = Manager(client)
        world_state = manager.build(
            prompt=prompt,
            bounds_min=origin,
            bounds_max=end,
        )
        print(f"  -> {world_state.block_count} total blocks placed across all sub-builders")

        if world_state.block_count == 0:
            print("No blocks were placed — nothing to build.")
            client.close()
            return

        print("\n[2/3] Writing schematic ...")
        schem_name = make_schem_name(prompt[:30])
        schem_path, schem_name = save_world_state(
            world_state, size, filename=schem_name
        )
        print(f"  -> Saved to {schem_path}")

        plan = world_state.to_block_ops()
        materials = material_list(plan)
        print("\n  Materials needed in inventory:")
        for block, count in sorted(materials.items()):
            print(f"    {block}: {count}")

        result = client.ensure_materials_if_creative(materials, scaffold_block)
        if result.get("gave"):
            print("  -> Creative mode: materials + scaffold added to inventory")

        if placement_mode == "baritone":
            print("\n[3/3] Starting Baritone #build ...")
            result = client.build_schematic(
                filename=schem_name,
                x=origin[0],
                y=origin[1],
                z=origin[2],
                scaffold_block=scaffold_block,
            )
            print(f"  -> {result.get('message', result)}")
            print(
                "\nBaritone is now building in-game using items from your inventory.\n"
                f"Scaffold block: {scaffold_block} (will be left in place for debugging).\n"
                "Use '#stop' in chat to cancel, or '#build' to resume."
            )
        else:
            _place_plan_directly(client, origin, plan)
            print("\nInstant placement finished.")

    client.close()


if __name__ == "__main__":
    main()
