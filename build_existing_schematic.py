"""
Select an existing .schem file from the schematics directory and spawn it
into the Minecraft world by placing blocks directly via /setblock (auto mode).

Usage:
    python build_existing_schematic.py [schematic_name.schem]

If no argument is given, lists all .schem files in the schematics directory
and prompts the user to pick one interactively.
"""

import gzip
import io
import os
import struct
import sys
import json
import time
from typing import Dict, List, Tuple

from minecraft_client import MinecraftClient


# ── Schematics directory discovery ─────────────────────────────────────────

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "build_config.json")
DEFAULT_ORIGIN_OFFSET = (3, 0, 0)


def _get_schematics_dir() -> str:
    """Resolve the schematics directory using the same logic as schematic.py."""
    override = os.getenv("CRAFTSMEN_SCHEMATICS_DIR")
    if override:
        return os.path.expandvars(os.path.expanduser(override))

    appdata = os.getenv("APPDATA")
    if appdata:
        return os.path.join(
            appdata, "ModrinthApp", "profiles", "Craftsmen", "schematics"
        )

    return os.path.join(
        os.path.expanduser("~"),
        "AppData", "Roaming",
        "ModrinthApp", "profiles", "Craftsmen", "schematics",
    )


def _list_schematics(directory: str) -> List[str]:
    """Return sorted list of .schem filenames in *directory*."""
    if not os.path.isdir(directory):
        return []
    return sorted(
        f for f in os.listdir(directory) if f.lower().endswith(".schem")
    )


def _load_build_config() -> dict:
    """Load build_config.json if it exists, else return empty dict."""
    path = os.getenv("CRAFTSMEN_BUILD_CONFIG", DEFAULT_CONFIG_PATH)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ── Minimal NBT reader (Sponge Schematic v2) ──────────────────────────────
# Only the tag types needed for reading .schem files are implemented.

_TAG_END = 0
_TAG_BYTE = 1
_TAG_SHORT = 2
_TAG_INT = 3
_TAG_LONG = 4
_TAG_FLOAT = 5
_TAG_DOUBLE = 6
_TAG_BYTE_ARRAY = 7
_TAG_STRING = 8
_TAG_LIST = 9
_TAG_COMPOUND = 10
_TAG_INT_ARRAY = 11
_TAG_LONG_ARRAY = 12


def _read_nbt(stream: io.BufferedIOBase) -> dict:
    """Read a single named NBT compound from *stream* and return it as a dict."""

    def read_fmt(fmt: str):
        size = struct.calcsize(fmt)
        return struct.unpack(fmt, stream.read(size))

    def read_string() -> str:
        (length,) = read_fmt(">H")
        return stream.read(length).decode("utf-8")

    def read_payload(tag_type: int):
        if tag_type == _TAG_BYTE:
            return read_fmt(">b")[0]
        if tag_type == _TAG_SHORT:
            return read_fmt(">h")[0]
        if tag_type == _TAG_INT:
            return read_fmt(">i")[0]
        if tag_type == _TAG_LONG:
            return read_fmt(">q")[0]
        if tag_type == _TAG_FLOAT:
            return read_fmt(">f")[0]
        if tag_type == _TAG_DOUBLE:
            return read_fmt(">d")[0]
        if tag_type == _TAG_BYTE_ARRAY:
            (length,) = read_fmt(">i")
            return stream.read(length)
        if tag_type == _TAG_STRING:
            return read_string()
        if tag_type == _TAG_LIST:
            (elem_type,) = read_fmt(">b")
            (length,) = read_fmt(">i")
            return [read_payload(elem_type) for _ in range(length)]
        if tag_type == _TAG_COMPOUND:
            return read_compound()
        if tag_type == _TAG_INT_ARRAY:
            (length,) = read_fmt(">i")
            return list(read_fmt(f">{length}i"))
        if tag_type == _TAG_LONG_ARRAY:
            (length,) = read_fmt(">i")
            return list(read_fmt(f">{length}q"))
        raise ValueError(f"Unknown NBT tag type: {tag_type}")

    def read_compound() -> dict:
        result = {}
        while True:
            (tag_type,) = read_fmt(">b")
            if tag_type == _TAG_END:
                break
            name = read_string()
            result[name] = read_payload(tag_type)
        return result

    # Root tag: type byte + name + compound payload
    (root_type,) = read_fmt(">b")
    if root_type != _TAG_COMPOUND:
        raise ValueError(f"Expected compound root tag, got {root_type}")
    _root_name = read_string()
    return read_compound()


def _decode_varint(data: bytes, offset: int) -> Tuple[int, int]:
    """Decode a single varint from *data* at *offset*. Return (value, new_offset)."""
    value = 0
    shift = 0
    while True:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if (byte & 0x80) == 0:
            break
        shift += 7
    return value, offset


# ── Schematic parser ──────────────────────────────────────────────────────

def parse_schem(path: str) -> Tuple[List[Tuple[int, int, int, str]], Tuple[int, int, int]]:
    """
    Parse a Sponge Schematic v2 ``.schem`` file and return:
      - A list of (x, y, z, block_id) tuples for every non-air block.
      - The (width, height, length) dimensions.
    """
    with open(path, "rb") as f:
        raw = gzip.decompress(f.read())

    nbt = _read_nbt(io.BytesIO(raw))

    width = nbt["Width"]
    height = nbt["Height"]
    length = nbt["Length"]

    # Palette: block_name → index  →  invert to index → block_name
    palette_nbt: Dict[str, int] = nbt["Palette"]
    index_to_block: Dict[int, str] = {v: k for k, v in palette_nbt.items()}

    # BlockData: varint-encoded palette indices
    block_data: bytes = nbt["BlockData"]
    volume = width * height * length

    blocks: List[Tuple[int, int, int, str]] = []
    offset = 0
    for i in range(volume):
        idx, offset = _decode_varint(block_data, offset)
        block_id = index_to_block.get(idx, "minecraft:air")
        if block_id == "minecraft:air":
            continue
        # Sponge Schematic index: (y * length + z) * width + x
        x = i % width
        z = (i // width) % length
        y = i // (width * length)
        blocks.append((x, y, z, block_id))

    return blocks, (width, height, length)


# ── Interactive selection ──────────────────────────────────────────────────

def select_schematic(schematics: List[str]) -> str:
    """Display a numbered menu and return the chosen filename."""
    print("\nAvailable schematics:")
    print("-" * 50)
    for i, name in enumerate(schematics, 1):
        print(f"  [{i}] {name}")
    print("-" * 50)

    while True:
        choice = input(f"Select a schematic (1-{len(schematics)}): ").strip()
        if not choice:
            continue
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(schematics):
                return schematics[idx]
        except ValueError:
            # Allow typing the filename directly
            if choice in schematics:
                return choice
        print(f"  Invalid choice. Enter a number between 1 and {len(schematics)}.")


# ── Build execution (auto /setblock placement) ────────────────────────────

def build_existing(
    schematic_path: str,
    origin_offset: Tuple[int, int, int] = DEFAULT_ORIGIN_OFFSET,
) -> None:
    """
    Parse the .schem file, connect to Minecraft, and place every non-air
    block via /setblock (auto mode — no Baritone).
    """
    print(f"\nSchematic : {schematic_path}")
    print(f"Offset    : {origin_offset}")

    print("\nParsing schematic ...")
    blocks, (w, h, l) = parse_schem(schematic_path)
    print(f"  Dimensions : {w} x {h} x {l}")
    print(f"  Non-air blk: {len(blocks)}")

    if not blocks:
        print("  Nothing to place — schematic is empty.")
        return

    print("\nConnecting to Minecraft listener ...")
    client = MinecraftClient()

    try:
        pos = client.get_position()
        origin = (
            int(pos[0]) + origin_offset[0],
            int(pos[1]) + origin_offset[1],
            int(pos[2]) + origin_offset[2],
        )
        print(f"Player pos  : ({int(pos[0])}, {int(pos[1])}, {int(pos[2])})")
        print(f"Build origin: {origin}")

        total = len(blocks)
        placed = 0
        started = time.time()

        print(f"\nPlacing {total} blocks via /setblock ...")
        for i, (rx, ry, rz, block_id) in enumerate(blocks, 1):
            wx = origin[0] + rx
            wy = origin[1] + ry
            wz = origin[2] + rz
            ok = client.place_block(wx, wy, wz, block_id)
            if ok:
                placed += 1
            if i % 50 == 0 or i == total:
                elapsed = time.time() - started
                print(f"  Progress: {i}/{total}  ({elapsed:.1f}s)")

        elapsed = time.time() - started
        print(f"\nDone: {placed}/{total} blocks placed in {elapsed:.1f}s")
    finally:
        client.close()


# ── Main ──────────────────────────────────────────────────────────────────

def main() -> None:
    config = _load_build_config()
    origin_offset = tuple(config.get("origin_offset", list(DEFAULT_ORIGIN_OFFSET)))

    schematics_dir = _get_schematics_dir()

    # If a filename was passed on the command line, use it directly
    if len(sys.argv) > 1:
        schematic_name = sys.argv[1]
        if not schematic_name.lower().endswith(".schem"):
            schematic_name += ".schem"
        full_path = os.path.join(schematics_dir, schematic_name)
        if not os.path.isfile(full_path):
            print(f"Error: schematic not found: {full_path}")
            sys.exit(1)
    else:
        # Interactive selection
        print(f"Schematics directory: {schematics_dir}")
        schematics = _list_schematics(schematics_dir)
        if not schematics:
            print(f"\nNo .schem files found in:\n  {schematics_dir}")
            print("Generate one first with main.py, then re-run this script.")
            sys.exit(1)

        schematic_name = select_schematic(schematics)
        full_path = os.path.join(schematics_dir, schematic_name)

    build_existing(
        schematic_path=full_path,
        origin_offset=origin_offset,
    )


if __name__ == "__main__":
    main()
