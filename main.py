import sys
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


def main():
    prompt = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else None
    if not prompt:
        prompt = input("What should I build? > ").strip()
    if not prompt:
        print("No prompt provided — exiting.")
        return

    print("Connecting to Minecraft listener ...")
    client = MinecraftClient()

    # Build area starts a few blocks in front of the player
    pos = client.get_position()
    origin = (int(pos[0]) + 3, int(pos[1]), int(pos[2]))
    w, h, l = DEFAULT_SIZE
    end = (origin[0] + w - 1, origin[1] + h - 1, origin[2] + l - 1)

    print(f"Prompt  : {prompt}")
    print(f"Origin  : {origin}")
    print(f"Size    : {DEFAULT_SIZE}")
    print(f"Palette : {len(DEFAULT_PALETTE)} block types")
    print(f"Scaffold: {SCAFFOLD_BLOCK}")

    # --- 1. Plan the build via Azure OpenAI ---------------------------------
    print("\n[1/3] Generating build plan ...")
    builder = Builder(client)
    plan = builder.build(
        prompt=prompt,
        bounds_min=origin,
        bounds_max=end,
        palette=DEFAULT_PALETTE,
    )
    print(f"  -> {len(plan)} block operations generated")

    # --- 2. Save as .schem for Baritone -------------------------------------
    print("\n[2/3] Writing schematic ...")
    size = (w, h, l)
    schem_name = make_schem_name(prompt[:30])
    schem_path, schem_name = save_schem(plan, size, filename=schem_name)
    print(f"  -> Saved to {schem_path}")

    # --- Materials needed ----------------------------------------------------
    materials = material_list(plan)
    print("\n  Materials needed in inventory:")
    for block, count in sorted(materials.items()):
        print(f"    {block}: {count}")

    # --- Creative mode: auto-give materials ----------------------------------
    result = client.ensure_materials_if_creative(materials, SCAFFOLD_BLOCK)
    if result.get("gave"):
        print("  -> Creative mode: materials + scaffold added to inventory")

    # --- 3. Tell Baritone to #build -----------------------------------------

        print("\n[3/3] Starting Baritone #build (creative mode) ...")
    else:
        print("\n[3/3] Starting Baritone #build (survival mode) ...")
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
