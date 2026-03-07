import argparse
import gzip
import io
import os
import struct
from collections import Counter
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from builder import BlockOp, Builder
from schematic import save_schem, filter_plan_for_schematic


try:
    # Reuse the defaults from the main pipeline if available.
    from main import DEFAULT_PALETTE, DEFAULT_SIZE
except Exception:  # pragma: no cover - defensive fallback
    DEFAULT_PALETTE = [
        "minecraft:oak_planks",
        "minecraft:oak_log",
        "minecraft:glass",
        "minecraft:cobblestone",
        "minecraft:oak_stairs",
        "minecraft:oak_slab",
        "minecraft:torch",
    ]
    DEFAULT_SIZE = (7, 7, 7)


# ============================================================================
# Part 1: Planning validity metrics
# ============================================================================

def _op_fields(op: Any) -> Tuple[int, int, int, str]:
    """
    Extract (x, y, z, block) from either a BlockOp or a dict-like object.
    """
    # Attribute-based objects (e.g. BlockOp or similar) are preferred.
    try:
        return int(op.x), int(op.y), int(op.z), str(op.block)
    except AttributeError:
        pass

    # Fallback for plain dicts / TypedDicts
    try:
        return int(op["x"]), int(op["y"]), int(op["z"]), str(op["block"])
    except Exception as exc:  # pragma: no cover - defensive
        raise TypeError(f"Unsupported operation type: {type(op)!r}") from exc


def compute_plan_validity_metrics(
    operations: Sequence[Any],
    width: int,
    height: int,
    length: int,
    allowed_blocks: Iterable[str],
) -> Dict[str, Any]:
    """
    Compute quantitative validity metrics for a list of block operations.

    Metrics:
      - bounds_validity: fraction of ops whose coordinates lie within
        0 <= x < width, 0 <= y < height, 0 <= z < length
      - palette_validity: fraction of ops whose block is in allowed_blocks
      - duplicate_coordinate_count: number of repeated coordinates
      - duplicate_coordinate_rate: duplicate_coordinate_count / total_operations
      - total_operations: total number of operations
      - unique_coordinate_count: number of unique coordinates
    """
    total = len(operations)
    if total == 0:
        return {
            "bounds_validity": 1.0,
            "palette_validity": 1.0,
            "duplicate_coordinate_count": 0,
            "duplicate_coordinate_rate": 0.0,
            "total_operations": 0,
            "unique_coordinate_count": 0,
        }

    allowed_set = {b.strip().lower() for b in allowed_blocks}

    in_bounds = 0
    in_palette = 0
    coord_counts: Counter = Counter()

    for op in operations:
        x, y, z, block = _op_fields(op)
        coord_counts[(x, y, z)] += 1

        if 0 <= x < width and 0 <= y < height and 0 <= z < length:
            in_bounds += 1

        if block.strip().lower() in allowed_set:
            in_palette += 1

    duplicate_coordinate_count = sum(c - 1 for c in coord_counts.values() if c > 1)
    duplicate_coordinate_rate = duplicate_coordinate_count / float(total)

    return {
        "bounds_validity": in_bounds / float(total),
        "palette_validity": in_palette / float(total),
        "duplicate_coordinate_count": duplicate_coordinate_count,
        "duplicate_coordinate_rate": duplicate_coordinate_rate,
        "total_operations": total,
        "unique_coordinate_count": len(coord_counts),
    }


# ============================================================================
# Part 2: Schematic parsing and fidelity metrics
# ============================================================================

class _NBTReader:
    """
    Minimal NBT reader for Sponge Schematic v2 files.

    Supports just enough of the spec to read:
      - Width, Height, Length
      - Palette (compound of block_name -> int)
      - BlockData (byte array of varint palette indices)
      - Offset (int array) – currently ignored
    """

    def __init__(self, data: bytes) -> None:
        self._buf = io.BytesIO(data)

    def _read(self, fmt: str) -> Any:
        size = struct.calcsize(fmt)
        raw = self._buf.read(size)
        if len(raw) != size:
            raise EOFError("Unexpected end of NBT data")
        return struct.unpack(fmt, raw)[0]

    def _read_string(self) -> str:
        length = self._read(">H")
        raw = self._buf.read(length)
        if len(raw) != length:
            raise EOFError("Unexpected end of NBT string")
        return raw.decode("utf-8")

    def _read_tag_payload(self, tag_type: int) -> Any:
        # Only handle the tag types used by schematic.py
        if tag_type == 2:  # TAG_Short
            return self._read(">h")
        if tag_type == 3:  # TAG_Int
            return self._read(">i")
        if tag_type == 7:  # TAG_Byte_Array
            length = self._read(">i")
            raw = self._buf.read(length)
            if len(raw) != length:
                raise EOFError("Unexpected end of byte array")
            return raw
        if tag_type == 8:  # TAG_String
            return self._read_string()
        if tag_type == 10:  # TAG_Compound
            return self._read_compound()
        if tag_type == 11:  # TAG_Int_Array
            length = self._read(">i")
            return [self._read(">i") for _ in range(length)]
        raise ValueError(f"Unsupported NBT tag type: {tag_type}")

    def _read_compound(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        while True:
            tag_type_raw = self._buf.read(1)
            if not tag_type_raw:
                raise EOFError("Unexpected EOF while reading compound")
            tag_type = tag_type_raw[0]
            if tag_type == 0:  # TAG_End
                break
            name = self._read_string()
            value = self._read_tag_payload(tag_type)
            result[name] = value
        return result

    def read_root(self) -> Tuple[str, Mapping[str, Any]]:
        tag_type_raw = self._buf.read(1)
        if not tag_type_raw:
            raise EOFError("Empty NBT data")
        tag_type = tag_type_raw[0]
        if tag_type != 10:  # TAG_Compound
            raise ValueError(f"Expected root TAG_Compound, got type {tag_type}")
        name = self._read_string()
        payload = self._read_compound()
        return name, payload


def _decode_varints(data: bytes, expected_count: int) -> List[int]:
    """
    Decode a sequence of little-endian base-128 varints from *data*.

    Returns exactly expected_count integers, or fewer if the data ends
    early (in which case the caller can decide how to handle it).
    """
    values: List[int] = []
    value = 0
    shift = 0

    for byte in data:
        byte_val = byte & 0x7F
        value |= byte_val << shift
        if (byte & 0x80) == 0:
            values.append(value)
            if len(values) == expected_count:
                break
            value = 0
            shift = 0
        else:
            shift += 7

    return values


def parse_schematic_blocks(
    schem_path: str,
) -> Tuple[Dict[Tuple[int, int, int], str], Tuple[int, int, int]]:
    """
    Parse a Sponge Schematic v2 file and reconstruct block placements.

    Returns:
      - mapping from (x, y, z) -> block_name (non-air only)
      - (width, height, length) tuple

    The coordinate mapping and palette usage match ``plan_to_schem`` in
    ``schematic.py`` so that comparisons are deterministic.
    """
    with open(schem_path, "rb") as f:
        gz = f.read()

    data = gzip.decompress(gz)
    reader = _NBTReader(data)
    _, root = reader.read_root()

    width = int(root["Width"])
    height = int(root["Height"])
    length = int(root["Length"])
    volume = width * height * length

    palette: Mapping[str, int] = root["Palette"]
    block_data_bytes: bytes = root["BlockData"]

    # Build inverse palette: index -> block_name
    index_to_block: Dict[int, str] = {}
    for name, idx in palette.items():
        index_to_block[int(idx)] = name

    indices = _decode_varints(block_data_bytes, expected_count=volume)
    if len(indices) < volume:
        # Defensive: pad with air if truncated
        indices.extend([0] * (volume - len(indices)))

    blocks: Dict[Tuple[int, int, int], str] = {}
    for idx, palette_index in enumerate(indices[:volume]):
        # Inverse of index computation in plan_to_schem:
        # idx = (y * length + z) * width + x
        y = idx // (length * width)
        rem = idx % (length * width)
        z = rem // width
        x = rem % width

        block_name = index_to_block.get(palette_index, "minecraft:air")
        if block_name == "minecraft:air":
            continue
        blocks[(x, y, z)] = block_name

    return blocks, (width, height, length)


def _expected_filtered_blocks(
    operations: Sequence[Any],
    width: int,
    height: int,
    length: int,
) -> Dict[Tuple[int, int, int], str]:
    """
    Apply the same filtering and bounds handling as ``plan_to_schem`` and
    produce a mapping (x, y, z) -> block_name for comparison with a
    parsed schematic.
    """
    # Apply blacklist filtering using the same helper as plan_to_schem.
    filtered_plan, _removed = filter_plan_for_schematic(list(operations))

    blocks: Dict[Tuple[int, int, int], str] = {}
    for op in filtered_plan:
        x, y, z, block = _op_fields(op)
        if 0 <= x < width and 0 <= y < height and 0 <= z < length:
            # Same "last write wins" semantics as plan_to_schem's grid.
            blocks[(x, y, z)] = block
    return blocks


def compute_schematic_fidelity_metrics(
    operations: Sequence[Any],
    schem_path: str,
    width: int,
    height: int,
    length: int,
    allowed_blocks: Iterable[str],
) -> Dict[str, Any]:
    """
    Compare an operation list against the blocks recovered from a
    generated schematic.

    The comparison is made against the *filtered* expected plan, using
    the same blacklist and bounds rules as the schematic writer.
    """
    _ = list(allowed_blocks)  # Reserved for future palette-aware checks

    expected_blocks = _expected_filtered_blocks(operations, width, height, length)
    recovered_blocks, parsed_size = parse_schematic_blocks(schem_path)

    # Sizes should normally match; differences are not fatal but worth
    # surfacing in the metrics.
    size_mismatch = (
        parsed_size[0] != width
        or parsed_size[1] != height
        or parsed_size[2] != length
    )

    expected_coords = set(expected_blocks.keys())
    recovered_coords = set(recovered_blocks.keys())

    intersection = expected_coords & recovered_coords

    exact_match_count = sum(
        1 for coord in intersection
        if recovered_blocks[coord] == expected_blocks[coord]
    )
    block_type_mismatch_count = len(intersection) - exact_match_count

    missing_block_count = len(expected_coords - recovered_coords)
    extra_block_count = len(recovered_coords - expected_coords)

    expected_block_count = len(expected_coords)
    recovered_block_count = len(recovered_coords)

    exact_match_rate = (
        float(exact_match_count) / expected_block_count
        if expected_block_count > 0
        else 1.0
    )

    return {
        "exact_match_count": exact_match_count,
        "exact_match_rate": exact_match_rate,
        "missing_block_count": missing_block_count,
        "extra_block_count": extra_block_count,
        "block_type_mismatch_count": block_type_mismatch_count,
        "expected_block_count": expected_block_count,
        "recovered_block_count": recovered_block_count,
        "size_mismatch": size_mismatch,
        "expected_size": (width, height, length),
        "parsed_size": parsed_size,
    }


# ============================================================================
# CLI utilities
# ============================================================================

def _generate_plan_and_schematic(
    prompt: str,
    size: Tuple[int, int, int],
    palette: Sequence[str],
) -> Tuple[List[BlockOp], str]:
    """
    Use the existing Builder and schematic writer to create an operation
    list and matching schematic file for a single prompt.

    This uses a synthetic origin (0, 0, 0) with relative bounds only,
    so it does not require a running Minecraft instance.
    """
    width, height, length = size

    # Builder currently only needs a client for type purposes; the
    # planning logic is independent of Minecraft state, so we safely
    # pass None here to avoid requiring a live listener.
    builder = Builder(client=None)  # type: ignore[arg-type]

    bounds_min = (0, 0, 0)
    bounds_max = (width - 1, height - 1, length - 1)

    plan = builder.build(
        prompt=prompt,
        bounds_min=bounds_min,
        bounds_max=bounds_max,
        palette=list(palette),
        move_agent=False,
        verify=False,
    )

    schem_path, _schem_name = save_schem(plan, size, schematics_dir="eval_schematics")
    return plan, schem_path


def _print_plan_metrics(metrics: Mapping[str, Any]) -> None:
    print("  Planning validity:")
    print(f"    total_operations        : {metrics['total_operations']}")
    print(f"    unique_coordinate_count : {metrics['unique_coordinate_count']}")
    print(f"    duplicate_coordinate_count: {metrics['duplicate_coordinate_count']}")
    print(f"    duplicate_coordinate_rate : {metrics['duplicate_coordinate_rate']:.3f}")
    print(f"    bounds_validity         : {metrics['bounds_validity']:.3f}")
    print(f"    palette_validity        : {metrics['palette_validity']:.3f}")


def _print_fidelity_metrics(metrics: Mapping[str, Any]) -> None:
    print("  Schematic fidelity:")
    print(f"    expected_block_count    : {metrics['expected_block_count']}")
    print(f"    recovered_block_count   : {metrics['recovered_block_count']}")
    print(f"    exact_match_count       : {metrics['exact_match_count']}")
    print(f"    exact_match_rate        : {metrics['exact_match_rate']:.3f}")
    print(f"    missing_block_count     : {metrics['missing_block_count']}")
    print(f"    extra_block_count       : {metrics['extra_block_count']}")
    print(f"    block_type_mismatch_count: {metrics['block_type_mismatch_count']}")
    if metrics.get("size_mismatch"):
        print(
            f"    size_mismatch           : expected={metrics['expected_size']}, "
            f"parsed={metrics['parsed_size']}"
        )


def run_for_prompt(prompt: str) -> None:
    prompt = prompt.strip()
    if not prompt:
        return

    print(f"\n=== Prompt: {prompt!r} ===")
    width, height, length = DEFAULT_SIZE

    plan, schem_path = _generate_plan_and_schematic(
        prompt=prompt,
        size=DEFAULT_SIZE,
        palette=DEFAULT_PALETTE,
    )

    plan_metrics = compute_plan_validity_metrics(
        operations=plan,
        width=width,
        height=height,
        length=length,
        allowed_blocks=DEFAULT_PALETTE,
    )

    fidelity_metrics = compute_schematic_fidelity_metrics(
        operations=plan,
        schem_path=schem_path,
        width=width,
        height=height,
        length=length,
        allowed_blocks=DEFAULT_PALETTE,
    )

    _print_plan_metrics(plan_metrics)
    _print_fidelity_metrics(fidelity_metrics)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate planning validity and schematic fidelity for Craftsmen builds.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--prompt",
        type=str,
        help="Single natural-language build prompt to evaluate.",
    )
    group.add_argument(
        "--prompt-file",
        type=str,
        help="Path to a file containing one prompt per line. "
             "Lines starting with '#' and empty lines are ignored.",
    )
    args = parser.parse_args()

    if args.prompt:
        run_for_prompt(args.prompt)
        return

    # Prompt-file mode
    path = os.path.abspath(args.prompt_file)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Prompt file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            run_for_prompt(stripped)


if __name__ == "__main__":
    main()

