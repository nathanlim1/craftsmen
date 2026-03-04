import sys
import socket
import json
import time
import traceback
import math

# This script runs inside Minecraft via MineScript.
# Usage in-game: \listener

try:
    import minescript
    print("MineScript imported successfully.")
except ImportError:
    print("Error: MineScript module not found. "
          "This script must be run within Minecraft using MineScript.")
    sys.exit(1)

HOST = "127.0.0.1"
PORT = 25560

# Baritone will use this block for temporary scaffolding (pillaring/bridging).
# Bright colour makes it easy to spot and tear down later.
SCAFFOLD_BLOCK = "minecraft:red_wool"


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def _player_position():
    """Return [x, y, z] of the player (minescript v4 API)."""
    return minescript.player_position()


def _distance(a, b):
    return math.sqrt(sum((ai - bi) ** 2 for ai, bi in zip(a, b)))


def _wait_for_arrival(x, y, z, timeout=45, tolerance=4.0):
    """Block until the player is within *tolerance* blocks of the target."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if _distance(_player_position(), (x, y, z)) <= tolerance:
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def _configure_baritone(scaffold_block=SCAFFOLD_BLOCK):
    """Set Baritone options for building.  Red-wool scaffold by default."""
    for cmd in [
        f"#set acceptableThrowawayItems {scaffold_block}",
        "#set allowPlace true",
        "#set allowBreak true",
    ]:
        minescript.chat(cmd)
        time.sleep(0.15)
    minescript.echo(f"[Craftsmen] Baritone scaffold: {scaffold_block}")


# ---------------------------------------------------------------------------
#  Schematic build via Baritone #build
# ---------------------------------------------------------------------------

def build_schematic(filename, x, y, z, scaffold_block=SCAFFOLD_BLOCK):
    """
    Configure Baritone for survival building, navigate near the build site,
    then start ``#build <filename> <x> <y> <z>``.

    Baritone will pathfind, scaffold with *scaffold_block* (red wool by
    default), and place blocks from the player's inventory.
    """
    _configure_baritone(scaffold_block)
    time.sleep(0.5)

    # Walk to the build origin so Baritone doesn't have to travel far first.
    try:
        dist = _distance(_player_position(), (x, y, z))
    except Exception:
        dist = 999

    if dist > 10:
        minescript.chat(f"#goto {x} {y} {z}")
        _wait_for_arrival(x, y, z, timeout=60)
        minescript.chat("#stop")
        time.sleep(0.5)

    build_cmd = f"#build {filename} {x} {y} {z}"
    minescript.chat(build_cmd)
    minescript.echo(f"[Craftsmen] {build_cmd}")
    return {
        "ok": True,
        "message": f"Started: {build_cmd}",
        "scaffold": scaffold_block,
    }


# ---------------------------------------------------------------------------
#  Command router
# ---------------------------------------------------------------------------

def handle_command(cmd_data):
    method = cmd_data.get("method")
    params = cmd_data.get("params", [])

    if method == "ping":
        return "pong"

    if method == "get_position":
        return list(_player_position())

    if method == "get_block_at":
        return minescript.getblock(int(params[0]), int(params[1]), int(params[2]))

    if method == "place_block":
        x, y, z = int(params[0]), int(params[1]), int(params[2])
        block = str(params[3])
        if not block.startswith("minecraft:"):
            block = f"minecraft:{block}"
        minescript.execute(f"/setblock {x} {y} {z} {block}")
        return True

    if method == "move_to":
        x, y, z = float(params[0]), float(params[1]), float(params[2])
        minescript.execute(f"/tp @s {x} {y} {z}")
        return True

    if method == "set_inventory":
        block = str(params[0])
        if not block.startswith("minecraft:"):
            block = f"minecraft:{block}"
        minescript.execute(f"/give @s {block} {int(params[1])}")
        return True

    if method == "get_inventory":
        return {}

    if method == "build_schematic":
        filename = str(params[0])
        x, y, z = int(params[1]), int(params[2]), int(params[3])
        scaffold = str(params[4]) if len(params) > 4 else SCAFFOLD_BLOCK
        return build_schematic(filename, x, y, z, scaffold)

    if method == "baritone":
        cmd = str(params[0]) if params else ""
        if not cmd.startswith("#"):
            cmd = f"#{cmd}"
        minescript.chat(cmd)
        return {"ok": True, "message": f"Sent: {cmd}"}

    raise ValueError(f"Unknown method: {method}")


def client_handler(conn, addr):
    print(f"Connected by {addr}")
    buffer = ""
    try:
        while True:
            data = conn.recv(1024)
            if not data:
                break

            buffer += data.decode("utf-8")

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

                conn.sendall((json.dumps(response) + "\n").encode("utf-8"))

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
            conn, addr = server.accept()
            client_handler(conn, addr)

    except Exception as e:
        print(f"Server error: {e}")
    finally:
        server.close()


if __name__ == "__main__":
    start_server()
