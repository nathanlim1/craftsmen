"""Block operation type used across the construction pipeline."""

from dataclasses import dataclass


@dataclass
class BlockOp:
    x: int
    y: int
    z: int
    block: str
