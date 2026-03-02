import os
import platform
import sys
import socket
import json
import time
import traceback

# This script is meant to be run inside Minecraft via MineScript.
# Usage in-game: \listener 
# (or setup automated running in config.txt with autorun[*]=listener)

try:
    import minescript
    print("MineScript imported successfully.")
except ImportError:
    print("Error: MineScript module not found. This script must be run within Minecraft using MineScript.")
    sys.exit(1)

HOST = '127.0.0.1'
PORT = 25560  # Custom port for our listener

def get_inventory_dict():
    """Helper to get inventory as a dictionary."""
    inv_dict = {}
    try:
        for stack in minescript.player_inventory():
            item_name = stack.item
            if item_name.startswith("minecraft:"):
                item_name = item_name.split(":")[1]
            count = getattr(stack, 'count', 1)
            inv_dict[item_name] = inv_dict.get(item_name, 0) + count
    except Exception as e:
        print(f"Error reading inventory: {e}")
    return inv_dict

# Pre-1.13 or commonly hallucinated block IDs → correct modern equivalents.
_BLOCK_ID_ALIASES = {
    "minecraft:fence":            "minecraft:oak_fence",
    "minecraft:wooden_door":      "minecraft:oak_door",
    "minecraft:wooden_trapdoor":  "minecraft:oak_trapdoor",
    "minecraft:wooden_slab":      "minecraft:oak_slab",
    "minecraft:wooden_stairs":    "minecraft:oak_stairs",
    "minecraft:wooden_pressure_plate": "minecraft:oak_pressure_plate",
    "minecraft:wooden_button":    "minecraft:oak_button",
    "minecraft:log":              "minecraft:oak_log",
    "minecraft:log2":             "minecraft:dark_oak_log",
    "minecraft:planks":           "minecraft:oak_planks",
    "minecraft:leaves":           "minecraft:oak_leaves",
    "minecraft:sapling":          "minecraft:oak_sapling",
    "minecraft:stone_slab":       "minecraft:smooth_stone_slab",
    "minecraft:double_stone_slab": "minecraft:smooth_stone_slab",
    "minecraft:cobble":           "minecraft:cobblestone",
    "minecraft:brick_block":      "minecraft:bricks",
    "minecraft:lit_pumpkin":      "minecraft:jack_o_lantern",
    "minecraft:redstone_lamp_active": "minecraft:redstone_lamp",
}


def _normalize_block_id(block_type: str) -> str:
    """Map any known legacy or hallucinated block ID to its modern equivalent.
    Only the base name (before any '[' block state) is looked up."""
    if "[" in block_type:
        base, states = block_type.split("[", 1)
        normalized = _BLOCK_ID_ALIASES.get(base, base)
        return f"{normalized}[{states}"
    return _BLOCK_ID_ALIASES.get(block_type, block_type)


def _place_block(x, y, z, full_block_name):
    """Place a single block, handling block states and auto-completing door pairs.

    The give/clear commands only understand item IDs (no block state syntax), so
    we strip block states for those while keeping them for setblock.
    Doors are two blocks tall: placing either half auto-places the companion half.
    """
    full_block_name = _normalize_block_id(full_block_name)
    # Item name for give/clear has no block state bracket.
    item_name = full_block_name.split("[", 1)[0]

    minescript.execute(f"give @p {item_name} 1")
    minescript.execute(f"setblock {x} {y} {z} {full_block_name}")
    minescript.execute(f"clear @p {item_name} 1")

    # Auto-place companion half for two-block-tall door blocks.
    base = item_name  # e.g. "minecraft:oak_door"
    if base.endswith("_door") and "trapdoor" not in base:
        if "[" in full_block_name:
            states_str = full_block_name.split("[", 1)[1].rstrip("]")
            states = dict(
                s.split("=") for s in states_str.split(",") if "=" in s
            )
        else:
            states = {}

        half = states.get("half", "lower")
        companion_states = dict(states)
        companion_states["half"] = "upper" if half == "lower" else "lower"
        companion_y = y + 1 if half == "lower" else y - 1

        states_part = ",".join(f"{k}={v}" for k, v in companion_states.items())
        companion_block = f"{base}[{states_part}]"

        minescript.execute(f"give @p {item_name} 1")
        minescript.execute(f"setblock {x} {companion_y} {z} {companion_block}")
        minescript.execute(f"clear @p {item_name} 1")


def handle_command(cmd_data):
    """Executes the command and returns the result."""
    method = cmd_data.get("method")
    params = cmd_data.get("params", [])
    
    if method == "get_position":
        pos = minescript.player_position()
        if hasattr(pos, 'x'):
            return [float(pos.x), float(pos.y), float(pos.z)]
        return [float(pos[0]), float(pos[1]), float(pos[2])]

    elif method == "move_to":
        x, y, z = params
        minescript.execute(f"tp {x} {y} {z}")
        return None

    elif method == "get_block_at":
        x, y, z = params
        block_id = minescript.getblock(x, y, z)
        if block_id.startswith("minecraft:"):
            return block_id.split(":")[1]
        return block_id

    elif method == "get_blocks_in_bounds":
        bounds_min, bounds_max = params
        min_x, min_y, min_z = bounds_min
        max_x, max_y, max_z = bounds_max
        blocks = []
        for x in range(int(min_x), int(max_x) + 1):
            for y in range(int(min_y), int(max_y) + 1):
                for z in range(int(min_z), int(max_z) + 1):
                    block_id = minescript.getblock(x, y, z)
                    if block_id.startswith("minecraft:"):
                        block_id = block_id.split(":")[1]
                    blocks.append({"x": x, "y": y, "z": z, "block": block_id})
        return blocks

    elif method == "get_inventory":
        return get_inventory_dict()

    elif method == "place_block":
        x, y, z, block_type = params
        if not block_type.startswith("minecraft:"):
            block_type = f"minecraft:{block_type}"
        _place_block(x, y, z, block_type)
        return True

    elif method == "place_ops":
        ops = params[0]
        for op in ops:
            x, y, z = op["x"], op["y"], op["z"]
            block_type = op["block"]
            if not block_type.startswith("minecraft:"):
                block_type = f"minecraft:{block_type}"
            _place_block(x, y, z, block_type)
        return True

    elif method == "remove_ops":
        ops = params[0]
        for op in ops:
            x, y, z = op["x"], op["y"], op["z"]
            minescript.execute(f"setblock {x} {y} {z} minecraft:air")
        return True

    elif method == "place_ops":
        ops = params[0]
        for op in ops:
            x, y, z = op["x"], op["y"], op["z"]
            block_type = op["block"]
            if block_type.startswith("minecraft:"):
                simple_type = block_type.split(":")[1]
            else:
                simple_type = block_type
            full_block_name = f"minecraft:{simple_type}"
            minescript.execute(f"give @p {full_block_name} 1")
            minescript.execute(f"setblock {x} {y} {z} {full_block_name}")
            minescript.execute(f"clear @p {full_block_name} 1")
        return True

    elif method == "remove_ops":
        ops = params[0]
        for op in ops:
            x, y, z = op["x"], op["y"], op["z"]
            minescript.execute(f"setblock {x} {y} {z} minecraft:air")
        return True

    elif method == "set_inventory":
        block_type, count = params
        minescript.execute(f"clear @p {block_type}")
        if count > 0:
            minescript.execute(f"give @p {block_type} {count}")
        return None
    
    elif method == "take_screenshot":
        # Take a screenshot via MineScript and return the saved file path.
        # params: [label, bounds_min, bounds_max]
        # If bounds are supplied the player is teleported to a fixed vantage point
        # outside and above the build before capturing, so the LLM always sees the
        # full exterior regardless of where the player happened to be standing.
        label = params[0] if len(params) > 0 else None
        bounds_min_sc = params[1] if len(params) > 1 else None
        bounds_max_sc = params[2] if len(params) > 2 else None
        try:
            if not hasattr(minescript, "screenshot"):
                return {"path": None, "error": "minescript.screenshot() not available in this MineScript version."}

            # --- Position player at a good vantage point if bounds are provided ---
            if bounds_min_sc and bounds_max_sc:
                min_x, min_y, min_z = bounds_min_sc
                max_x, max_y, max_z = bounds_max_sc
                cx = (min_x + max_x) / 2
                cz = (min_z + max_z) / 2
                width  = max(max_x - min_x, max_z - min_z)
                # Stand ~1.5× the build width away on the +X/+Z diagonal, elevated
                dist = max(width * 1.5, 15)
                vx = cx + dist
                vy = max_y + max(width * 0.6, 10)
                vz = cz + dist
                # yaw=-135 faces back toward -X/-Z (toward the build centre)
                minescript.execute(f"tp @p {vx:.1f} {vy:.1f} {vz:.1f} -135 -25")
                time.sleep(0.5)  # Wait for the world to render from the new position.

            filename = label or f"craftsmen_{int(time.time())}.png"

            # Resolve the Minecraft screenshots directory.
            # listener.py lives in <mc_dir>/minescript/, so going up one level gives mc_dir.
            script_dir = os.path.dirname(os.path.abspath(__file__))
            mc_dir_from_script = os.path.dirname(script_dir)

            system = platform.system()
            if system == "Darwin":
                mc_dir_standard = os.path.expanduser(
                    "~/Library/Application Support/minecraft"
                )
            elif system == "Windows":
                mc_dir_standard = os.path.join(
                    os.environ.get("APPDATA", os.path.expanduser("~")), ".minecraft"
                )
            else:
                mc_dir_standard = os.path.expanduser("~/.minecraft")

            screenshots_from_script = os.path.join(mc_dir_from_script, "screenshots")
            screenshots_standard = os.path.join(mc_dir_standard, "screenshots")
            screenshots_dir = (
                screenshots_from_script
                if os.path.isdir(screenshots_from_script)
                else screenshots_standard
            )

            # Snapshot existing files so we can identify the newly saved one.
            before = set(os.listdir(screenshots_dir)) if os.path.isdir(screenshots_dir) else set()

            minescript.screenshot(filename)
            time.sleep(0.8)  # Give Minecraft a moment to flush the file.

            if os.path.isdir(screenshots_dir):
                after = set(os.listdir(screenshots_dir))
                new_files = sorted(after - before)
                if new_files:
                    path = os.path.join(screenshots_dir, new_files[-1])
                elif os.path.isfile(os.path.join(screenshots_dir, filename)):
                    path = os.path.join(screenshots_dir, filename)
                else:
                    path = None
            else:
                path = None

            return {"path": path}
        except Exception as e:
            return {"path": None, "error": str(e)}

    elif method == "ping":
        return "pong"

    else:
        raise ValueError(f"Unknown method: {method}")

def client_handler(conn, addr):
    print(f"Connected by {addr}")
    buffer = ""
    try:
        while True:
            data = conn.recv(1024)
            if not data:
                break
            
            buffer += data.decode('utf-8')
            
            # Process complete messages (newline delimited)
            while "\n" in buffer:
                message, buffer = buffer.split("\n", 1)
                if not message.strip():
                    continue
                    
                try:
                    cmd_data = json.loads(message)
                    print(f"Executing: {cmd_data.get('method')}")
                    result = handle_command(cmd_data)
                    response = {"status": "success", "result": result}
                except Exception as e:
                    traceback.print_exc()
                    response = {"status": "error", "error": str(e)}
                
                conn.sendall((json.dumps(response) + "\n").encode('utf-8'))
                
    except Exception as e:
        print(f"Connection error: {e}")
    finally:
        conn.close()
        print(f"Disconnected {addr}")

def start_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind((HOST, PORT))
        server.listen(1)
        print(f"Listening on {HOST}:{PORT}...")
        minescript.echo(f"Listener started on port {PORT}")
        
        while True:
            # Check for stop signal? For now just run forever until script killed
            conn, addr = server.accept()
            # Only handle one client at a time for simplicity (prevents race conditions in game state)
            client_handler(conn, addr)
            
    except Exception as e:
        print(f"Server error: {e}")
    finally:
        server.close()

if __name__ == "__main__":
    start_server()
