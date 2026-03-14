"""
Blind comparison test for single-agent vs multi-agent builds.

This script runs the same randomly selected prompt through both build modes,
places the two results side-by-side in auto mode, and labels them as LEFT /
RIGHT so they can be rated blindly before revealing which mode produced which
build.

Prompts and build sizes are loaded from a separate size-keyed JSON prompt pool.
Each size iteration picks a random prompt from that size bucket.

Iteration counts are controlled per size bucket:
- `--small N`
- `--medium N`
- `--large N`

Optional config keys:
- `comparison_iterations`: {"small": 2, "medium": 1, "large": 1}
- `comparison_prompt_pool_path`: "config/comparison_prompt_pool.json"
- `random_seed`: 12345
"""

import argparse
import contextlib
import json
import os
import random
import re
import sys
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.agents.builder import BlockOp, Builder
from src.agents.manager import Manager
from src.config_defaults import (
    DEFAULT_ORIGIN_OFFSET,
    DEFAULT_PALETTE,
    DEFAULT_SIZE_MULTI,
    DEFAULT_SIZE_SINGLE,
)
from src.core.schematic import make_schem_name, save_schem
from src.minecraft_client import MinecraftClient

DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "config", "build_config.json"
)
DEFAULT_PROMPT_POOL_PATH = os.path.join(
    os.path.dirname(__file__), "config", "comparison_prompt_pool.json"
)

SIZE_ORDER = ("small", "medium", "large")
DEFAULT_COMPARISON_ITERATIONS = {
    "small": 1,
    "medium": 1,
    "large": 1,
}
PAIR_GAP = 5
ROW_GAP = 6
SIZE_GAP = 20
MARKER_HEIGHT = 5

LEFT_MARKER_BLOCK = "minecraft:light_blue_wool"
RIGHT_MARKER_BLOCK = "minecraft:orange_wool"
PAIR_MARKER_BLOCK = "minecraft:gray_wool"
PROMPT_MARKER_BLOCK = "minecraft:white_wool"
SIZE_MARKER_BLOCKS = {
    "small": "minecraft:lime_wool",
    "medium": "minecraft:yellow_wool",
    "large": "minecraft:blue_wool",
}

ARTIFACTS_ROOT = os.path.join(
    os.path.dirname(__file__), "artifacts", "blind_comparisons"
)


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
        raise ValueError("Size entries must be JSON arrays with 3 integers")
    size = tuple(int(v) for v in raw)
    if any(v <= 0 for v in size):
        raise ValueError("Size values must be positive")
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


def _normalize_prompt_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        prompt = raw.strip()
        return [prompt] if prompt else []
    if not isinstance(raw, list):
        raise ValueError("'prompts' must be a string or a JSON array of strings")
    prompts: List[str] = []
    for idx, item in enumerate(raw, 1):
        if not isinstance(item, str):
            raise ValueError(f"Prompt #{idx} must be a string")
        prompt = item.strip()
        if prompt:
            prompts.append(prompt)
    return prompts


def _normalize_prompt_bucket(label: str, raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"Prompt pool bucket '{label}' must be a JSON object")

    size = _normalize_size(raw.get("size", list(_default_size_for_label(label))))
    prompts = _normalize_prompt_list(raw.get("prompts"))
    if not prompts:
        raise ValueError(f"Prompt pool bucket '{label}' must contain at least one prompt")

    return {
        "size": size,
        "prompts": prompts,
    }


def _default_size_for_label(label: str) -> Tuple[int, int, int]:
    if label == "small":
        return DEFAULT_SIZE_SINGLE
    if label == "large":
        return DEFAULT_SIZE_MULTI
    return (20, 10, 20)


def _load_prompt_pool(path: str) -> Dict[str, Dict[str, Any]]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Prompt pool file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    if not isinstance(payload, dict):
        raise ValueError(f"Prompt pool must be a JSON object: {path}")

    pool: Dict[str, Dict[str, Any]] = {}
    for label in SIZE_ORDER:
        if label not in payload:
            raise ValueError(f"Prompt pool is missing '{label}' bucket")
        pool[label] = _normalize_prompt_bucket(label, payload[label])
    return pool


def _normalize_iteration_count(raw: Any, label: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Iteration count for '{label}' must be an integer") from exc
    if value < 0:
        raise ValueError(f"Iteration count for '{label}' must be >= 0")
    return value


def _normalize_iteration_map(raw: Any) -> Dict[str, int]:
    counts = dict(DEFAULT_COMPARISON_ITERATIONS)
    if raw is None:
        return counts
    if not isinstance(raw, dict):
        raise ValueError("'comparison_iterations' must be a JSON object")
    for label in SIZE_ORDER:
        if label in raw:
            counts[label] = _normalize_iteration_count(raw[label], label)
    return counts


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Blind comparison runner for single-agent vs multi-agent builds."
    )
    parser.add_argument(
        "--small",
        type=int,
        default=None,
        help="Number of small comparison pairs to generate.",
    )
    parser.add_argument(
        "--medium",
        type=int,
        default=None,
        help="Number of medium comparison pairs to generate.",
    )
    parser.add_argument(
        "--large",
        type=int,
        default=None,
        help="Number of large comparison pairs to generate.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional random seed for reproducible left/right assignment.",
    )
    return parser


def _resolve_iterations(
    args: argparse.Namespace,
    config: Dict[str, Any],
) -> Dict[str, int]:
    counts = _normalize_iteration_map(config.get("comparison_iterations"))

    if args.small is not None:
        counts["small"] = _normalize_iteration_count(args.small, "small")
    if args.medium is not None:
        counts["medium"] = _normalize_iteration_count(args.medium, "medium")
    if args.large is not None:
        counts["large"] = _normalize_iteration_count(args.large, "large")

    return counts


def _resolve_seed(args: argparse.Namespace, config: Dict[str, Any]) -> int:
    if args.seed is not None:
        return int(args.seed)
    if "random_seed" in config and config["random_seed"] is not None:
        return int(config["random_seed"])
    return random.SystemRandom().randrange(1, 1_000_000_000)


def _resolve_prompt_pool_path(config: Dict[str, Any]) -> str:
    raw = config.get("comparison_prompt_pool_path", DEFAULT_PROMPT_POOL_PATH)
    return os.path.join(os.path.dirname(__file__), raw) if not os.path.isabs(raw) else raw


def _place_plan_at(
    client: MinecraftClient,
    origin: Tuple[int, int, int],
    plan: List[BlockOp],
    label: str,
) -> None:
    world_plan = Builder.to_world_coords(plan, origin)
    total = len(world_plan)
    print(f"\n  [{label}] Placing {total} blocks via /setblock ...")
    placed = 0
    started = time.time()
    for idx, op in enumerate(world_plan, 1):
        if op.block == "minecraft:air":
            continue
        ok = client.place_block(op.x, op.y, op.z, op.block)
        if ok:
            placed += 1
        if idx % 50 == 0 or idx == total:
            print(f"    -> Progress: {idx}/{total}")
    elapsed = time.time() - started
    print(f"    -> Done: {placed}/{total} blocks in {elapsed:.1f}s")


def _place_marker(
    client: MinecraftClient,
    x: int,
    y: int,
    z: int,
    block: str,
    height: int = MARKER_HEIGHT,
) -> None:
    for dy in range(height):
        client.place_block(x, y + dy, z, block)


def _place_label_sign(
    client: MinecraftClient,
    x: int,
    y: int,
    z: int,
    line1: str,
    line2: str = "",
    line3: str = "",
    line4: str = "",
) -> None:
    msg1 = json.dumps({"text": line1})
    msg2 = json.dumps({"text": line2})
    msg3 = json.dumps({"text": line3})
    msg4 = json.dumps({"text": line4})
    sign_nbt = (
        f"minecraft:oak_sign[rotation=8]"
        f"{{front_text:{{messages:['{msg1}','{msg2}','{msg3}','{msg4}']}}}}"
    )
    client.place_block(x, y, z, sign_nbt)


def _clear_area(
    client: MinecraftClient,
    origin: Tuple[int, int, int],
    size: Tuple[int, int, int],
) -> None:
    width, height, length = size
    ox, oy, oz = origin
    total = width * height * length
    print(f"  Clearing {width}x{height}x{length} area at {origin} ({total} blocks) ...")
    count = 0
    for y in range(oy, oy + height):
        for z in range(oz, oz + length):
            for x in range(ox, ox + width):
                client.place_block(x, y, z, "minecraft:air")
                count += 1
                if count % 200 == 0:
                    print(f"    -> Cleared {count}/{total}")
    print("    -> Area cleared")


def _run_single_build(
    client: MinecraftClient,
    prompt: str,
    size: Tuple[int, int, int],
    palette: List[str],
) -> List[BlockOp]:
    width, height, length = size
    volume = width * height * length
    max_blocks = min(volume, 5000)
    builder = Builder(client, max_blocks=max_blocks)
    return builder.build(
        prompt=prompt,
        bounds_min=(0, 0, 0),
        bounds_max=(width - 1, height - 1, length - 1),
        palette=palette,
    )


def _run_multi_build(
    client: MinecraftClient,
    prompt: str,
    size: Tuple[int, int, int],
) -> List[BlockOp]:
    width, height, length = size
    manager = Manager(client, use_validator=True)
    world_state = manager.build(
        prompt=prompt,
        bounds_min=(0, 0, 0),
        bounds_max=(width - 1, height - 1, length - 1),
    )
    return world_state.to_block_ops()


def _run_mode_blind(
    client: MinecraftClient,
    prompt: str,
    size: Tuple[int, int, int],
    palette: List[str],
    mode: str,
    pair_code: str,
    side_label: str,
    log_path: str,
) -> Tuple[List[BlockOp], Optional[str]]:
    try:
        with open(log_path, "w", encoding="utf-8") as log_file:
            with contextlib.redirect_stdout(log_file), contextlib.redirect_stderr(log_file):
                print(
                    f"Blind build log for {pair_code} {side_label}\n"
                    f"Prompt: {prompt}\n"
                    f"Size: {size}\n"
                    f"Mode: {mode}\n"
                )
                if mode == "single":
                    plan = _run_single_build(client, prompt, size, palette)
                elif mode == "multi":
                    plan = _run_multi_build(client, prompt, size)
                else:
                    raise ValueError(f"Unsupported mode: {mode}")
        return plan, None
    except Exception as exc:
        with open(log_path, "a", encoding="utf-8") as log_file:
            traceback.print_exc(file=log_file)
        return [], str(exc)


def _save_blind_schematic(
    plan: List[BlockOp],
    size: Tuple[int, int, int],
    pair_code: str,
    side_label: str,
) -> Optional[str]:
    if not plan:
        return None
    filename = make_schem_name(f"{pair_code.lower()}_{side_label.lower()}")
    schem_path, _ = save_schem(plan, size, filename=filename)
    return schem_path


def _place_section_header(
    client: MinecraftClient,
    x: int,
    y: int,
    z: int,
    block: str,
    line1: str,
    line2: str,
) -> None:
    _place_marker(client, x, y, z, block)
    _place_label_sign(client, x, y + MARKER_HEIGHT, z, line1, line2)


def _compact_prompt_label(prompt: str, max_lines: int = 4, max_chars: int = 15) -> List[str]:
    words = re.sub(r"\s+", " ", prompt.strip()).split(" ")
    lines: List[str] = []
    current = ""

    for word in words:
        if not word:
            continue
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= max_chars:
            current = candidate
            continue

        if current:
            lines.append(current)
            current = word
        else:
            lines.append(word[:max_chars])
            current = word[max_chars:]

        if len(lines) == max_lines:
            break

    if current and len(lines) < max_lines:
        lines.append(current[:max_chars])

    if len(lines) > max_lines:
        lines = lines[:max_lines]

    if words and len(" ".join(words)) > sum(len(line) for line in lines):
        last = lines[-1] if lines else ""
        lines[-1] = (last[: max(0, max_chars - 1)] + "...")[:max_chars]

    while len(lines) < max_lines:
        lines.append("")
    return lines


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    config_path = os.getenv("CRAFTSMEN_BUILD_CONFIG", DEFAULT_CONFIG_PATH)
    config = _load_build_config(config_path)

    iterations = _resolve_iterations(args, config)
    if sum(iterations.values()) == 0:
        print("All iteration counts are zero - nothing to generate.")
        return

    prompt_pool_path = _resolve_prompt_pool_path(config)
    prompt_pool = _load_prompt_pool(prompt_pool_path)
    origin_offset = _normalize_offset(config.get("origin_offset", list(DEFAULT_ORIGIN_OFFSET)))
    palette = _normalize_palette(config.get("palette", DEFAULT_PALETTE))

    active_sizes = [label for label in SIZE_ORDER if iterations[label] > 0]
    total_pairs = sum(iterations[label] for label in active_sizes)
    seed = _resolve_seed(args, config)
    rng = random.Random(seed)

    run_stamp = time.strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(ARTIFACTS_ROOT, f"run_{run_stamp}")
    logs_dir = os.path.join(run_dir, "logs")
    manifest_path = os.path.join(run_dir, "blind_key.json")
    os.makedirs(logs_dir, exist_ok=True)

    print("\n" + "=" * 72)
    print("  CRAFTSMEN - Blind Single-vs-Multi Comparison Test")
    print("=" * 72)
    print(f"  Prompt pool   : {prompt_pool_path}")
    print(f"  Seed          : {seed}")
    print(f"  Placement     : auto (/setblock)")
    print(f"  Pair gap      : {PAIR_GAP} blocks")
    print(f"  Row gap       : {ROW_GAP} blocks")
    print(f"  Size gap      : {SIZE_GAP} blocks")
    print(f"  Output key    : {manifest_path}")
    print("=" * 72)
    for label in active_sizes:
        print(
            f"  {label.title():<12}: {iterations[label]} pair(s), "
            f"size={prompt_pool[label]['size']}, "
            f"prompt choices={len(prompt_pool[label]['prompts'])}"
        )
    print(f"  Total pairs   : {total_pairs}")
    print("=" * 72)

    client = MinecraftClient()
    manifest: Dict[str, Any] = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "seed": seed,
        "config_path": config_path,
        "prompt_pool_path": prompt_pool_path,
        "iterations": iterations,
        "prompt_pool": {
            label: {
                "size": list(prompt_pool[label]["size"]),
                "prompts": prompt_pool[label]["prompts"],
            }
            for label in SIZE_ORDER
        },
        "pairs": [],
    }

    try:
        pos = client.get_position()
        base_origin = (
            int(pos[0]) + origin_offset[0],
            int(pos[1]) + origin_offset[1],
            int(pos[2]) + origin_offset[2],
        )
        print(f"\nBase origin: {base_origin}")

        base_x, base_y, current_z = base_origin
        pair_index = 0

        for size_label in active_sizes:
            size = prompt_pool[size_label]["size"]
            size_prompts = prompt_pool[size_label]["prompts"]
            width, _, length = size

            print("\n" + "-" * 72)
            print(f"  SIZE GROUP: {size_label.upper()} ({size})")
            print("-" * 72)

            _place_section_header(
                client,
                base_x - 3,
                base_y,
                current_z,
                SIZE_MARKER_BLOCKS[size_label],
                size_label.upper(),
                "GROUP",
            )

            for iteration in range(1, iterations[size_label] + 1):
                pair_index += 1
                pair_code = f"PAIR {pair_index:02d}"
                prompt = rng.choice(size_prompts)
                prompt_lines = _compact_prompt_label(prompt)
                left_origin = (base_x, base_y, current_z)
                right_origin = (base_x + width + PAIR_GAP, base_y, current_z)
                center_z = current_z + length // 2

                mode_order = ["single", "multi"]
                rng.shuffle(mode_order)
                left_mode, right_mode = mode_order

                print("\n" + "=" * 72)
                print(
                    f"  {pair_code} - {size_label.title()} - Iteration {iteration}"
                )
                print("=" * 72)
                print(f"  Prompt       : {prompt}")
                print(f"  Left origin  : {left_origin}")
                print(f"  Right origin : {right_origin}")

                print("\n--- Clearing build areas ---")
                _clear_area(client, left_origin, size)
                _clear_area(client, right_origin, size)

                left_log_path = os.path.join(
                    logs_dir, f"pair_{pair_index:02d}_left.log"
                )
                right_log_path = os.path.join(
                    logs_dir, f"pair_{pair_index:02d}_right.log"
                )

                print("\n--- Generating blind build plans ---")
                print(f"  [{pair_code} LEFT] Generating plan ...")
                left_plan, left_error = _run_mode_blind(
                    client=client,
                    prompt=prompt,
                    size=size,
                    palette=palette,
                    mode=left_mode,
                    pair_code=pair_code,
                    side_label="LEFT",
                    log_path=left_log_path,
                )
                print(f"  [{pair_code} RIGHT] Generating plan ...")
                right_plan, right_error = _run_mode_blind(
                    client=client,
                    prompt=prompt,
                    size=size,
                    palette=palette,
                    mode=right_mode,
                    pair_code=pair_code,
                    side_label="RIGHT",
                    log_path=right_log_path,
                )

                left_schem = _save_blind_schematic(left_plan, size, pair_code, "left")
                right_schem = _save_blind_schematic(
                    right_plan, size, pair_code, "right"
                )

                print("\n--- Placing builds ---")
                if left_plan:
                    _place_plan_at(client, left_origin, left_plan, f"{pair_code} LEFT")
                else:
                    print(f"  [{pair_code} LEFT] No blocks generated.")
                if right_plan:
                    _place_plan_at(
                        client, right_origin, right_plan, f"{pair_code} RIGHT"
                    )
                else:
                    print(f"  [{pair_code} RIGHT] No blocks generated.")

                print("\n--- Placing blind markers ---")
                left_marker_x = left_origin[0] - 1
                right_marker_x = right_origin[0] - 1
                center_marker_x = left_origin[0] + width + (PAIR_GAP // 2)
                prompt_marker_x = center_marker_x + 2

                _place_marker(
                    client, left_marker_x, base_y, center_z, LEFT_MARKER_BLOCK
                )
                _place_label_sign(
                    client,
                    left_marker_x,
                    base_y + MARKER_HEIGHT,
                    center_z,
                    pair_code,
                    "LEFT",
                )

                _place_marker(
                    client, right_marker_x, base_y, center_z, RIGHT_MARKER_BLOCK
                )
                _place_label_sign(
                    client,
                    right_marker_x,
                    base_y + MARKER_HEIGHT,
                    center_z,
                    pair_code,
                    "RIGHT",
                )

                _place_marker(
                    client, center_marker_x, base_y, center_z, PAIR_MARKER_BLOCK, 3
                )
                _place_label_sign(
                    client,
                    center_marker_x,
                    base_y + 3,
                    center_z,
                    pair_code,
                    size_label.upper(),
                    f"ITER {iteration:02d}",
                )

                _place_marker(
                    client, prompt_marker_x, base_y, center_z, PROMPT_MARKER_BLOCK, 3
                )
                _place_label_sign(
                    client,
                    prompt_marker_x,
                    base_y + 3,
                    center_z,
                    prompt_lines[0],
                    prompt_lines[1],
                    prompt_lines[2],
                    prompt_lines[3],
                )

                manifest["pairs"].append(
                    {
                        "pair_index": pair_index,
                        "pair_code": pair_code,
                        "size_label": size_label,
                        "size": list(size),
                        "prompt": prompt,
                        "iteration": iteration,
                        "left": {
                            "mode": left_mode,
                            "origin": list(left_origin),
                            "blocks": len(left_plan),
                            "schematic_path": left_schem,
                            "log_path": left_log_path,
                            "error": left_error,
                        },
                        "right": {
                            "mode": right_mode,
                            "origin": list(right_origin),
                            "blocks": len(right_plan),
                            "schematic_path": right_schem,
                            "log_path": right_log_path,
                            "error": right_error,
                        },
                    }
                )
                _write_json(manifest_path, manifest)

                current_z += length + ROW_GAP

            current_z += SIZE_GAP

        print("\n" + "=" * 72)
        print("  BLIND COMPARISON COMPLETE")
        print("=" * 72)
        print(f"  Pairs placed : {len(manifest['pairs'])}")
        print(f"  Reveal key   : {manifest_path}")
        print(f"  Logs         : {logs_dir}")
        print("=" * 72)
        print(
            "\nThe world labels only identify pair numbers and left/right placement.\n"
            "Use the saved blind key after rating to reveal which side was single-agent\n"
            "and which side was multi-agent.\n"
        )
    finally:
        _write_json(manifest_path, manifest)
        client.close()


if __name__ == "__main__":
    main()
