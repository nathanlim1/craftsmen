import sys
import socket
import json
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

    elif method == "get_inventory":
        return get_inventory_dict()

    elif method == "place_block":
        x, y, z, block_type = params
        # Normalize block_type
        if block_type.startswith("minecraft:"):
            simple_type = block_type.split(":")[1]
        else:
            simple_type = block_type

        full_block_name = f"minecraft:{simple_type}"
        # Ensure the player has the block for this placement.
        minescript.execute(f"give @p {full_block_name} 1")
        minescript.execute(f"setblock {x} {y} {z} {full_block_name}")
        minescript.execute(f"clear @p {full_block_name} 1")
        return True

    elif method == "set_inventory":
        block_type, count = params
        minescript.execute(f"clear @p {block_type}")
        if count > 0:
            minescript.execute(f"give @p {block_type} {count}")
        return None
    
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
