#!/usr/bin/env python3
"""Fetch block IDs from minecraft-data and write block_ids.py."""
import json
import urllib.request

URL = "https://raw.githubusercontent.com/PrismarineJS/minecraft-data/master/data/pc/1.20.4/blocks.json"

with urllib.request.urlopen(URL) as resp:
    data = json.load(resp)

names = sorted(set("minecraft:" + b["name"] for b in data if isinstance(b, dict) and "name" in b))

import os
out_path = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "src", "core", "block_ids.py")
)
with open(out_path, "w") as f:
    f.write('"""\nValid Minecraft block IDs (1.20.4) for palette validation.\n')
    f.write("Source: PrismarineJS/minecraft-data\n")
    f.write('"""\n\n')
    f.write("VALID_MINECRAFT_BLOCKS = frozenset({\n")
    for n in names:
        f.write(f"    {repr(n)},\n")
    f.write("})\n\n")
    f.write('''def validate_palette(palette: list[str]) -> tuple[list[str], list[str]]:
    """Validate palette block IDs. Returns (valid_blocks, invalid_blocks)."""
    valid: list[str] = []
    invalid: list[str] = []
    for block in palette:
        base = block.strip().lower().split("[")[0]
        if not base.startswith("minecraft:"):
            invalid.append(block)
        elif base in VALID_MINECRAFT_BLOCKS:
            valid.append(block.strip().lower())
        else:
            invalid.append(block)
    return valid, invalid
''')

print(f"Wrote {len(names)} block IDs to src/core/block_ids.py")
