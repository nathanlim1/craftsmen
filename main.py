import argparse
from minecraft_client import MinecraftClient
from manager import Manager
from schematic import save_world_state, material_list, make_schem_name


DEFAULT_PALETTE = [
    "minecraft:oak_planks",
    "minecraft:oak_log",
    "minecraft:glass",
    "minecraft:cobblestone",
    "minecraft:oak_stairs",
    "minecraft:oak_slab",
    "minecraft:torch",
]

DEFAULT_SIZE = (21, 15, 21)

SCAFFOLD_BLOCK = "minecraft:red_wool"


def main():
    parser = argparse.ArgumentParser(
        description="Multi-agent Minecraft builder from natural language prompts."
    )
    parser.add_argument(
        "--instant",
        action="store_true",
        help="Spawn blocks instantly with /setblock instead of Baritone pathfinding.",
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Build prompt (e.g. 'a small house with a garden').",
    )
    args = parser.parse_args()

    prompt = " ".join(args.prompt).strip() if args.prompt else None
    if not prompt:
        prompt = input("What should I build? > ").strip()
    if not prompt:
        print("No prompt provided — exiting.")
        return

    print("Connecting to Minecraft listener ...")
    client = MinecraftClient()

    pos = client.get_position()
    origin = (int(pos[0]) + 3, int(pos[1]), int(pos[2]))
    w, h, ln = DEFAULT_SIZE
    end = (origin[0] + w - 1, origin[1] + h - 1, origin[2] + ln - 1)

    print(f"Prompt  : {prompt}")
    print(f"Origin  : {origin}")
    print(f"Size    : {DEFAULT_SIZE}")
    print(f"Palette : {len(DEFAULT_PALETTE)} block types")
    print(f"Scaffold: {SCAFFOLD_BLOCK}")

    # --- 1. Manager decomposes and delegates to sub-builders ----------------
    print("\n[1/3] Manager is decomposing build and delegating to sub-builders ...")
    manager = Manager(client)
    world_state = manager.build(
        prompt=prompt,
        bounds_min=origin,
        bounds_max=end,
        overall_palette=DEFAULT_PALETTE,
    )
    print(f"  -> {world_state.block_count} total blocks placed across all sub-builders")

    if world_state.block_count == 0:
        print("No blocks were placed — nothing to build.")
        client.close()
        return

    # --- 2. Save as .schem for Baritone ------------------------------------
    print("\n[2/3] Writing schematic ...")
    size = (w, h, ln)
    schem_name = make_schem_name(prompt[:30])
    schem_path, schem_name = save_world_state(
        world_state, size, filename=schem_name
    )
    print(f"  -> Saved to {schem_path}")

    plan = world_state.to_block_ops()
    ox, oy, oz = origin

    if args.instant:
        # --- Instant: spawn blocks directly with /setblock -----------------
        print("\n[3/3] Placing blocks instantly (--instant) ...")
        placed = 0
        for op in plan:
            if op.block == "minecraft:air":
                continue
            client.place_block(ox + op.x, oy + op.y, oz + op.z, op.block)
            placed += 1
            if placed % 50 == 0:
                print(f"  -> {placed} blocks placed ...")
        print(f"  -> {placed} blocks placed in world")
    else:
        # --- Materials needed -----------------------------------------------
        materials = material_list(plan)
        print("\n  Materials needed in inventory:")
        for block, count in sorted(materials.items()):
            print(f"    {block}: {count}")

        # --- Creative mode: auto-give materials ----------------------------
        result = client.ensure_materials_if_creative(materials, SCAFFOLD_BLOCK)
        if result.get("gave"):
            print("  -> Creative mode: materials + scaffold added to inventory")
            print("\n[3/3] Starting Baritone #build (creative mode) ...")
        else:
            print("\n[3/3] Starting Baritone #build (survival mode) ...")

        # --- Tell Baritone to #build ----------------------------------------
        result = client.build_schematic(
            filename=schem_name,
            x=origin[0],
            y=origin[1],
            z=origin[2],
            scaffold_block=SCAFFOLD_BLOCK,
        )
        print(f"  -> {result.get('message', result)}")

        print(
            f"\nBaritone is now building in-game using items from your inventory."
            f"\nScaffold block: {SCAFFOLD_BLOCK} (will be left in place for debugging)."
            f"\nUse '#stop' in chat to cancel, or '#build' to resume."
        )

    client.close()


if __name__ == "__main__":
    main()
