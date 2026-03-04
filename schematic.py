"""
Convert a Builder plan (list of BlockOps) into a Sponge Schematic v2 (.schem)
file that Baritone's ``#build`` command can read.

No external dependencies — contains a self-contained minimal NBT writer.
"""

import gzip
import io
import os
import struct
import sys
import time
from collections import Counter
from typing import Dict, List, Tuple

from builder import BlockOp


# ── Minimal NBT binary writer ──────────────────────────────────────────────
# Only the tag types needed for Sponge Schematic v2 are implemented.

_TAG_END = 0
_TAG_SHORT = 2
_TAG_INT = 3
_TAG_BYTE_ARRAY = 7
_TAG_STRING = 8
_TAG_COMPOUND = 10
_TAG_INT_ARRAY = 11


class _NBT:
    """Tiny, dependency-free NBT serializer."""

    def __init__(self) -> None:
        self._buf = io.BytesIO()

    # -- low-level helpers ---------------------------------------------------

    def _pack(self, fmt: str, *values) -> None:
        self._buf.write(struct.pack(fmt, *values))

    def _utf(self, text: str) -> None:
        encoded = text.encode("utf-8")
        self._pack(">H", len(encoded))
        self._buf.write(encoded)

    def _header(self, tag_type: int, name: str) -> None:
        self._pack("B", tag_type)
        self._utf(name)

    # -- public tag writers --------------------------------------------------

    def short(self, name: str, value: int) -> None:
        self._header(_TAG_SHORT, name)
        self._pack(">h", value)

    def int_(self, name: str, value: int) -> None:
        self._header(_TAG_INT, name)
        self._pack(">i", value)

    def string(self, name: str, value: str) -> None:
        self._header(_TAG_STRING, name)
        self._utf(value)

    def byte_array(self, name: str, data: bytes) -> None:
        self._header(_TAG_BYTE_ARRAY, name)
        self._pack(">i", len(data))
        self._buf.write(data)

    def int_array(self, name: str, values: List[int]) -> None:
        self._header(_TAG_INT_ARRAY, name)
        self._pack(">i", len(values))
        for v in values:
            self._pack(">i", v)

    def compound_open(self, name: str) -> None:
        self._header(_TAG_COMPOUND, name)

    def compound_close(self) -> None:
        self._pack("B", _TAG_END)

    # -- output --------------------------------------------------------------

    def getvalue(self) -> bytes:
        return self._buf.getvalue()

    def gzipped(self) -> bytes:
        return gzip.compress(self._buf.getvalue())


# ── Varint encoding (used by Sponge Schematic BlockData) ───────────────────

def _encode_varint(value: int) -> bytes:
    buf = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value != 0:
            byte |= 0x80
        buf.append(byte)
        if value == 0:
            break
    return bytes(buf)


# ── Public API ─────────────────────────────────────────────────────────────

# Multi-part or state-dependent blocks that Baritone can't reliably place.
# These are stripped from the plan before writing the schematic.
BLACKLISTED_BLOCKS = {
    # Doors (two-tall, orientation-dependent)
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
    # Beds (two-wide, directional)
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
    # Tall plants / double blocks
    "minecraft:tall_grass",
    "minecraft:large_fern",
    "minecraft:sunflower",
    "minecraft:lilac",
    "minecraft:rose_bush",
    "minecraft:peony",
    "minecraft:tall_seagrass",
    "minecraft:pitcher_plant",
    # Banners (entity-heavy, orientation)
    "minecraft:white_banner",
    "minecraft:black_banner",
}


def _filter_blacklisted(plan: List[BlockOp]) -> Tuple[List[BlockOp], List[str]]:
    """Remove blacklisted blocks from *plan*.  Return (filtered, removed_set)."""
    kept: List[BlockOp] = []
    removed_names: set = set()
    for op in plan:
        base = op.block.split("[")[0]
        if base in BLACKLISTED_BLOCKS:
            removed_names.add(base)
        else:
            kept.append(op)
    return kept, sorted(removed_names)


def material_list(plan: List[BlockOp]) -> Dict[str, int]:
    """
    Return a mapping of *item* name → count the player needs in their
    inventory before Baritone can ``#build`` the schematic.

    Air and blacklisted blocks are excluded.
    """
    counts: Dict[str, int] = Counter()
    for op in plan:
        base = op.block.split("[")[0]
        if base == "minecraft:air":
            continue
        if base in BLACKLISTED_BLOCKS:
            continue
        counts[base] += 1
    return dict(counts)


def plan_to_schem(
    plan: List[BlockOp],
    size: Tuple[int, int, int],
    data_version: int = 3700,
) -> bytes:
    """
    Build gzipped Sponge Schematic v2 NBT bytes from a plan.

    Args:
        plan:  BlockOps with *relative* coordinates (0-indexed).
        size:  (width, height, length) of the bounding box.
        data_version:  Minecraft data version (default 3700 = 1.20.4).

    Returns:
        Gzipped NBT bytes ready to write to a ``.schem`` file.
    """
    width, height, length = size

    # Strip blocks Baritone can't place
    plan, removed = _filter_blacklisted(plan)
    if removed:
        import sys
        print(f"  [schematic] Stripped blacklisted blocks: {', '.join(removed)}",
              file=sys.stderr)

    # ── palette ────────────────────────────────────────────────────────
    palette: Dict[str, int] = {"minecraft:air": 0}
    for op in plan:
        if op.block not in palette:
            palette[op.block] = len(palette)

    # ── block data (varint-encoded palette indices) ────────────────────
    volume = width * height * length
    grid = [0] * volume  # default: air (index 0)
    for op in plan:
        if 0 <= op.x < width and 0 <= op.y < height and 0 <= op.z < length:
            idx = (op.y * length + op.z) * width + op.x
            grid[idx] = palette[op.block]

    block_data = bytearray()
    for index in grid:
        block_data.extend(_encode_varint(index))

    # ── write NBT ──────────────────────────────────────────────────────
    nbt = _NBT()
    nbt.compound_open("Schematic")

    nbt.int_("Version", 2)
    nbt.int_("DataVersion", data_version)
    nbt.short("Width", width)
    nbt.short("Height", height)
    nbt.short("Length", length)

    # Palette compound — maps block-state string → palette index
    nbt.compound_open("Palette")
    for block_name, idx in palette.items():
        nbt.int_(block_name, idx)
    nbt.compound_close()

    nbt.int_("PaletteMax", len(palette))
    nbt.byte_array("BlockData", bytes(block_data))
    nbt.int_array("Offset", [0, 0, 0])

    nbt.compound_close()  # close root Schematic compound

    return nbt.gzipped()


def make_schem_name(label: str = "build") -> str:
    """Generate a unique schematic filename with a timestamp."""
    ts = time.strftime("%Y%m%d_%H%M%S")
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in label)[:30]
    return f"craftsmen_{safe}_{ts}.schem"


def _default_schematics_dir() -> str:
    """Return the platform-specific Baritone schematics directory."""
    if sys.platform == "win32":
        base = os.getenv("APPDATA", "")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.path.expanduser("~/.local/share")
    return os.path.join(base, "ModrinthApp", "profiles", "Craftsmen", "schematics")


def save_schem(
    plan: List[BlockOp],
    size: Tuple[int, int, int],
    filename: str = None,
    schematics_dir: str = None,
    data_version: int = 3700,
) -> Tuple[str, str]:
    """
    Write the plan as a ``.schem`` file into the Baritone schematics directory.

    Args:
        plan:  BlockOps with *relative* coordinates.
        size:  (width, height, length).
        filename:  Name of the schematic file.  If *None*, a unique
                   timestamped name is generated.
        schematics_dir:  Override path.  Defaults to platform-specific:
                         Windows ``%APPDATA%``, macOS ``~/Library/Application Support``,
                         Linux ``~/.local/share``, then ``.../ModrinthApp/profiles/Craftsmen/schematics``.
        data_version:  Minecraft data version.

    Returns:
        ``(absolute_path, filename)`` tuple.
    """
    if filename is None:
        filename = make_schem_name()

    if schematics_dir is None:
        schematics_dir = _default_schematics_dir()

    os.makedirs(schematics_dir, exist_ok=True)
    path = os.path.join(schematics_dir, filename)

    data = plan_to_schem(plan, size, data_version)
    with open(path, "wb") as f:
        f.write(data)

    return path, filename


def save_world_state(
    world_state,
    size: Tuple[int, int, int],
    filename: str = None,
    schematics_dir: str = None,
    data_version: int = 3700,
) -> Tuple[str, str]:
    """Write a :class:`WorldState` as a ``.schem`` file.

    Converts the world state's block dict to a plan and delegates to
    :func:`save_schem`.  Coordinates in the world state are expected to be
    relative to the overall build origin (0-indexed).

    Args:
        world_state:  A :class:`world_state.WorldState` instance.
        size:  (width, height, length) of the overall build volume.
        filename:  Optional schematic filename.
        schematics_dir:  Optional override for the schematics directory.
        data_version:  Minecraft data version.

    Returns:
        ``(absolute_path, filename)`` tuple.
    """
    plan = world_state.to_block_ops()
    return save_schem(plan, size, filename, schematics_dir, data_version)
