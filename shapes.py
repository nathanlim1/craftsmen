"""High-level shape representation for LLM building instructions.

Shapes (fill, wall, floor, line) are expanded to BlockOps. Openings are created
by placing an air shape after a solid one — later ops overwrite earlier ones.
"""

from collections import defaultdict
from typing import Dict, List, Literal, Tuple

from pydantic import BaseModel, Field

from blocks import BlockOp


class Coord(BaseModel):
    x: int = Field(description="X coordinate.")
    y: int = Field(description="Y coordinate.")
    z: int = Field(description="Z coordinate.")


class ShapeOp(BaseModel):
    type: Literal["fill", "wall", "floor", "line"] = Field(
        description="Shape type: fill=3D box, wall=vertical plane, floor=horizontal plane, line=1D run."
    )
    corner1: Coord = Field(description="First corner.")
    corner2: Coord = Field(description="Second corner (opposite corner of the shape).")
    block: str = Field(description="Block id, e.g. minecraft:oak_planks or minecraft:air.")


class HighLevelPlan(BaseModel):
    ops: List[ShapeOp] = Field(description="List of shape operations, applied in order (later overwrites earlier).")


def _clip_bounds(
    x1: int, x2: int, y1: int, y2: int, z1: int, z2: int,
    clip_min: Tuple[int, int, int], clip_max: Tuple[int, int, int],
) -> Tuple[int, int, int, int, int, int]:
    """Clip shape bounds to clip region. Returns (x1, x2, y1, y2, z1, z2) or empty range."""
    cmx, cmy, cmz = clip_min
    cMx, cMy, cMz = clip_max
    nx1 = max(x1, cmx)
    nx2 = min(x2, cMx)
    ny1 = max(y1, cmy)
    ny2 = min(y2, cMy)
    nz1 = max(z1, cmz)
    nz2 = min(z2, cMz)
    if nx1 > nx2 or ny1 > ny2 or nz1 > nz2:
        return (0, -1, 0, -1, 0, -1)  # empty
    return (nx1, nx2, ny1, ny2, nz1, nz2)


def _expand_fill(
    op: ShapeOp, clip_min: Tuple[int, int, int], clip_max: Tuple[int, int, int]
) -> List[BlockOp]:
    x1, x2 = min(op.corner1.x, op.corner2.x), max(op.corner1.x, op.corner2.x)
    y1, y2 = min(op.corner1.y, op.corner2.y), max(op.corner1.y, op.corner2.y)
    z1, z2 = min(op.corner1.z, op.corner2.z), max(op.corner1.z, op.corner2.z)
    x1, x2, y1, y2, z1, z2 = _clip_bounds(x1, x2, y1, y2, z1, z2, clip_min, clip_max)
    if x1 > x2:
        return []
    result = []
    for x in range(x1, x2 + 1):
        for y in range(y1, y2 + 1):
            for z in range(z1, z2 + 1):
                result.append(BlockOp(x=x, y=y, z=z, block=op.block))
    return result


def _expand_wall(
    op: ShapeOp, clip_min: Tuple[int, int, int], clip_max: Tuple[int, int, int]
) -> List[BlockOp]:
    x1, x2 = min(op.corner1.x, op.corner2.x), max(op.corner1.x, op.corner2.x)
    y1, y2 = min(op.corner1.y, op.corner2.y), max(op.corner1.y, op.corner2.y)
    z1, z2 = min(op.corner1.z, op.corner2.z), max(op.corner1.z, op.corner2.z)
    if op.corner1.x == op.corner2.x:
        # Wall in YZ plane at constant x
        x1, x2, y1, y2, z1, z2 = _clip_bounds(x1, x2, y1, y2, z1, z2, clip_min, clip_max)
        if x1 > x2:
            return []
        result = []
        for y in range(y1, y2 + 1):
            for z in range(z1, z2 + 1):
                result.append(BlockOp(x=x1, y=y, z=z, block=op.block))
        return result
    if op.corner1.z == op.corner2.z:
        # Wall in XY plane at constant z
        x1, x2, y1, y2, z1, z2 = _clip_bounds(x1, x2, y1, y2, z1, z2, clip_min, clip_max)
        if x1 > x2:
            return []
        result = []
        for x in range(x1, x2 + 1):
            for y in range(y1, y2 + 1):
                result.append(BlockOp(x=x, y=y, z=z1, block=op.block))
        return result
    # Invalid wall (both x and z differ) - treat as fill of the 2D plane? Plan says "one horizontal axis must be equal"
    # Fallback: treat as thin fill (1-block thick in one dimension) - use the smaller dimension
    dx, dz = abs(x2 - x1), abs(z2 - z1)
    if dx == 0 and dz == 0:
        return _expand_fill(op, clip_min, clip_max)
    if dx <= dz:
        # Constant z
        z1 = op.corner1.z
        z2 = op.corner1.z
        x1, x2, y1, y2, z1, z2 = _clip_bounds(x1, x2, y1, y2, z1, z2, clip_min, clip_max)
        if x1 > x2:
            return []
        return [BlockOp(x=x, y=y, z=z1, block=op.block) for x in range(x1, x2 + 1) for y in range(y1, y2 + 1)]
    else:
        x1 = op.corner1.x
        x2 = op.corner1.x
        x1, x2, y1, y2, z1, z2 = _clip_bounds(x1, x2, y1, y2, z1, z2, clip_min, clip_max)
        if x1 > x2:
            return []
        return [BlockOp(x=x1, y=y, z=z, block=op.block) for y in range(y1, y2 + 1) for z in range(z1, z2 + 1)]


def _expand_floor(
    op: ShapeOp, clip_min: Tuple[int, int, int], clip_max: Tuple[int, int, int]
) -> List[BlockOp]:
    x1, x2 = min(op.corner1.x, op.corner2.x), max(op.corner1.x, op.corner2.x)
    y1, y2 = min(op.corner1.y, op.corner2.y), max(op.corner1.y, op.corner2.y)
    z1, z2 = min(op.corner1.z, op.corner2.z), max(op.corner1.z, op.corner2.z)
    if op.corner1.y != op.corner2.y:
        # Not a valid floor - treat as fill
        return _expand_fill(op, clip_min, clip_max)
    x1, x2, y1, y2, z1, z2 = _clip_bounds(x1, x2, y1, y2, z1, z2, clip_min, clip_max)
    if x1 > x2:
        return []
    return [
        BlockOp(x=x, y=y1, z=z, block=op.block)
        for x in range(x1, x2 + 1)
        for z in range(z1, z2 + 1)
    ]


def _expand_line(
    op: ShapeOp, clip_min: Tuple[int, int, int], clip_max: Tuple[int, int, int]
) -> List[BlockOp]:
    x1, x2 = min(op.corner1.x, op.corner2.x), max(op.corner1.x, op.corner2.x)
    y1, y2 = min(op.corner1.y, op.corner2.y), max(op.corner1.y, op.corner2.y)
    z1, z2 = min(op.corner1.z, op.corner2.z), max(op.corner1.z, op.corner2.z)
    dx, dy, dz = x2 - x1, y2 - y1, z2 - z1
    dims_diff = sum(1 for d in [dx, dy, dz] if d > 0)
    if dims_diff == 0:
        # Single point
        x1, x2, y1, y2, z1, z2 = _clip_bounds(x1, x2, y1, y2, z1, z2, clip_min, clip_max)
        if x1 <= x2:
            return [BlockOp(x=x1, y=y1, z=z1, block=op.block)]
        return []
    if dims_diff > 1:
        # Invalid line - treat as fill
        return _expand_fill(op, clip_min, clip_max)
    result = []
    if dx > 0:
        for x in range(x1, x2 + 1):
            if clip_min[0] <= x <= clip_max[0] and clip_min[1] <= y1 <= clip_max[1] and clip_min[2] <= z1 <= clip_max[2]:
                result.append(BlockOp(x=x, y=y1, z=z1, block=op.block))
    elif dy > 0:
        for y in range(y1, y2 + 1):
            if clip_min[0] <= x1 <= clip_max[0] and clip_min[1] <= y <= clip_max[1] and clip_min[2] <= z1 <= clip_max[2]:
                result.append(BlockOp(x=x1, y=y, z=z1, block=op.block))
    else:
        for z in range(z1, z2 + 1):
            if clip_min[0] <= x1 <= clip_max[0] and clip_min[1] <= y1 <= clip_max[1] and clip_min[2] <= z <= clip_max[2]:
                result.append(BlockOp(x=x1, y=y1, z=z, block=op.block))
    return result


def expand_plan(
    plan: HighLevelPlan,
    clip_min: Tuple[int, int, int],
    clip_max: Tuple[int, int, int],
) -> List[BlockOp]:
    """Expand high-level shape ops to BlockOps. Processes in list order; later ops overwrite earlier.

    Shapes outside clip bounds are clipped (the overlapping portion is placed).
    """
    acc: Dict[Tuple[int, int, int], str] = {}
    expanders = {
        "fill": _expand_fill,
        "wall": _expand_wall,
        "floor": _expand_floor,
        "line": _expand_line,
    }
    for op in plan.ops:
        expander = expanders.get(op.type, _expand_fill)
        blocks = expander(op, clip_min, clip_max)
        for b in blocks:
            acc[(b.x, b.y, b.z)] = b.block
    return [BlockOp(x=x, y=y, z=z, block=block) for (x, y, z), block in acc.items()]


def compress_to_shapes(blocks: Dict[Tuple[int, int, int], str]) -> List[ShapeOp]:
    """Convert a block dict into compact ShapeOp descriptions.

    Groups by block type and greedily merges into axis-aligned rectangles
    (floors, walls, lines, fills).
    """
    if not blocks:
        return []
    by_type: Dict[str, List[Tuple[int, int, int]]] = defaultdict(list)
    for (x, y, z), block in blocks.items():
        by_type[block].append((x, y, z))

    result: List[ShapeOp] = []
    for block, positions in by_type.items():
        pos_set = set(positions)
        used: set = set()

        def _try_floor(x: int, y: int, z: int) -> bool:
            """Grow largest floor from seed. Return True if added."""
            added: set = set()
            row = [(x, y, z)]
            added.add((x, y, z))
            used.add((x, y, z))
            min_x, max_x = x, x
            cx = x
            while (cx + 1, y, z) in pos_set and (cx + 1, y, z) not in used:
                cx += 1
                row.append((cx, y, z))
                added.add((cx, y, z))
                used.add((cx, y, z))
                max_x = cx
            cx = x
            while (cx - 1, y, z) in pos_set and (cx - 1, y, z) not in used:
                cx -= 1
                row.insert(0, (cx, y, z))
                added.add((cx, y, z))
                used.add((cx, y, z))
                min_x = cx
            cz_lo, cz_hi = z, z
            while all((px, y, cz_hi + 1) in pos_set and (px, y, cz_hi + 1) not in used for px in range(min_x, max_x + 1)):
                for px in range(min_x, max_x + 1):
                    t = (px, y, cz_hi + 1)
                    added.add(t)
                    used.add(t)
                cz_hi += 1
            while all((px, y, cz_lo - 1) in pos_set and (px, y, cz_lo - 1) not in used for px in range(min_x, max_x + 1)):
                for px in range(min_x, max_x + 1):
                    t = (px, y, cz_lo - 1)
                    added.add(t)
                    used.add(t)
                cz_lo -= 1
            if (max_x - min_x + 1) * (cz_hi - cz_lo + 1) >= 2:
                result.append(ShapeOp(type="floor", corner1=Coord(x=min_x, y=y, z=cz_lo), corner2=Coord(x=max_x, y=y, z=cz_hi), block=block))
                return True
            for p in added:
                used.discard(p)
            return False

        def _try_wall_x(x: int, y: int, z: int) -> bool:
            """Grow wall at constant x. Return True if added."""
            added: set = set()
            col = [(x, y, z)]
            added.add((x, y, z))
            used.add((x, y, z))
            for dy in [1, -1]:
                cy = y
                while (x, cy + dy, z) in pos_set and (x, cy + dy, z) not in used:
                    cy += dy
                    t = (x, cy, z)
                    col.append(t)
                    added.add(t)
                    used.add(t)
            min_y, max_y = min(p[1] for p in col), max(p[1] for p in col)
            cz_lo, cz_hi = z, z
            while all((x, py, cz_hi + 1) in pos_set and (x, py, cz_hi + 1) not in used for py in range(min_y, max_y + 1)):
                for py in range(min_y, max_y + 1):
                    t = (x, py, cz_hi + 1)
                    added.add(t)
                    used.add(t)
                cz_hi += 1
            while all((x, py, cz_lo - 1) in pos_set and (x, py, cz_lo - 1) not in used for py in range(min_y, max_y + 1)):
                for py in range(min_y, max_y + 1):
                    t = (x, py, cz_lo - 1)
                    added.add(t)
                    used.add(t)
                cz_lo -= 1
            if (max_y - min_y + 1) * (cz_hi - cz_lo + 1) >= 2:
                result.append(ShapeOp(type="wall", corner1=Coord(x=x, y=min_y, z=cz_lo), corner2=Coord(x=x, y=max_y, z=cz_hi), block=block))
                return True
            for p in added:
                used.discard(p)
            return False

        def _try_wall_z(x: int, y: int, z: int) -> bool:
            """Grow wall at constant z. Return True if added."""
            added: set = set()
            col = [(x, y, z)]
            added.add((x, y, z))
            used.add((x, y, z))
            for dy in [1, -1]:
                cy = y
                while (x, cy + dy, z) in pos_set and (x, cy + dy, z) not in used:
                    cy += dy
                    t = (x, cy, z)
                    col.append(t)
                    added.add(t)
                    used.add(t)
            min_y, max_y = min(p[1] for p in col), max(p[1] for p in col)
            cx_lo, cx_hi = x, x
            while all((cx_hi + 1, py, z) in pos_set and (cx_hi + 1, py, z) not in used for py in range(min_y, max_y + 1)):
                for py in range(min_y, max_y + 1):
                    t = (cx_hi + 1, py, z)
                    added.add(t)
                    used.add(t)
                cx_hi += 1
            while all((cx_lo - 1, py, z) in pos_set and (cx_lo - 1, py, z) not in used for py in range(min_y, max_y + 1)):
                for py in range(min_y, max_y + 1):
                    t = (cx_lo - 1, py, z)
                    added.add(t)
                    used.add(t)
                cx_lo -= 1
            if (max_y - min_y + 1) * (cx_hi - cx_lo + 1) >= 2:
                result.append(ShapeOp(type="wall", corner1=Coord(x=cx_lo, y=min_y, z=z), corner2=Coord(x=cx_hi, y=max_y, z=z), block=block))
                return True
            for p in added:
                used.discard(p)
            return False

        for (x, y, z) in sorted(positions):
            if (x, y, z) in used:
                continue
            if _try_floor(x, y, z):
                continue
            if (x, y, z) in used:
                continue
            if _try_wall_x(x, y, z):
                continue
            if (x, y, z) in used:
                continue
            if _try_wall_z(x, y, z):
                continue
            if (x, y, z) in used:
                continue
            result.append(ShapeOp(type="fill", corner1=Coord(x=x, y=y, z=z), corner2=Coord(x=x, y=y, z=z), block=block))

    return result
