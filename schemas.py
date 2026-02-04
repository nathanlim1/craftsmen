from typing import List, Optional
from pydantic import BaseModel, Field, conlist


class BlockOpSchema(BaseModel):
    x: int = Field(description="Absolute x coordinate within chunk bounds.")
    y: int = Field(description="Absolute y coordinate within chunk bounds.")
    z: int = Field(description="Absolute z coordinate within chunk bounds.")
    block: str = Field(description="Block id, e.g. minecraft:oak_planks.")


class BuildSpecSchema(BaseModel):
    style_rules: List[str] = Field(description="High-level build rules and style guidance.")
    structure_notes: List[str] = Field(description="Structural guidance and global constraints.")
    palette: List[str] = Field(description="Palette chosen for this build (minecraft:* ids).")
    max_ops_per_chunk: int = Field(description="Max operations per chunk task.")
    padding: int = Field(description="Context padding around chunk bounds.")


class ChunkTaskSchema(BaseModel):
    chunk_id: str = Field(description="Unique id for this chunk.")
    bounds_min: conlist(int, min_length=3, max_length=3) = Field(
        description="Absolute min bounds (x,y,z)."
    )
    bounds_max: conlist(int, min_length=3, max_length=3) = Field(
        description="Absolute max bounds (x,y,z)."
    )
    role: str = Field(description="Role of the chunk, e.g. foundation, walls, roof.")
    dependencies: List[str] = Field(description="Chunk ids that must complete first.")
    target_connections: List[str] = Field(description="Edges or neighbors to connect to.")


class ManagerPlanSchema(BaseModel):
    build_spec: BuildSpecSchema
    chunk_plan: List[ChunkTaskSchema]


class ChunkOpsSchema(BaseModel):
    placements: List[BlockOpSchema] = Field(description="Blocks to place within chunk bounds.")
    removals: List[BlockOpSchema] = Field(description="Blocks to remove (block can be 'air').")
    summary: str = Field(description="Short summary of work completed.")


class ChunkResultSchema(BaseModel):
    chunk_id: str
    placements: List[BlockOpSchema]
    removals: List[BlockOpSchema]
    summary: str
    success: bool
    error: Optional[str] = None
