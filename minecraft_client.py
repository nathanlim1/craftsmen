import sys
import socket
import json
from typing import Tuple, Dict, Any, List

class MinecraftClient:
    """
    A client class to interact with the Minecraft world via a running listener.py script.
    """
    def __init__(self, host='127.0.0.1', port=25560):
        self.host = host
        self.port = port
        self.socket = None
        self.connect()

    def connect(self):
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((self.host, self.port))
            # Test connection
            if self._send_command("ping") != "pong":
                raise ConnectionError("Handshake failed")
            print(f"Connected to Minecraft Listener at {self.host}:{self.port}")
        except ConnectionRefusedError:
            print(f"[ERROR] Could not connect to {self.host}:{self.port}")
            print("Make sure 'listener.py' is running inside Minecraft via MineScript.")
            sys.exit(1)

    def _send_command(self, method: str, *params) -> Any:
        """Sends a JSON command to the listener and returns the result."""
        payload = {
            "method": method,
            "params": params
        }
        
        try:
            msg = json.dumps(payload) + "\n"
            self.socket.sendall(msg.encode('utf-8'))
            
            # Read response (simple blocking read for newline)
            # (uses the assumption that responses fit in one packet or come quickly)

            f = self.socket.makefile('r')
            response_line = f.readline()
            
            if not response_line:
                raise ConnectionError("Server closed connection")
                
            response = json.loads(response_line)
            
            if response.get("status") == "success":
                return response.get("result")
            else:
                raise RuntimeError(f"Remote Error: {response.get('error')}")
                
        except (BrokenPipeError, ConnectionResetError):
            print("Connection lost. Reconnecting...")
            self.connect()
            return self._send_command(method, *params)

    def get_position(self) -> Tuple[float, float, float]:
        """Returns the current (x, y, z) position of the agent."""
        pos = self._send_command("get_position")
        return tuple(pos)

    def move_to(self, x: float, y: float, z: float) -> None:
        """Teleports or moves the agent to the specified coordinates."""
        self._send_command("move_to", x, y, z)

    def place_block(self, x: int, y: int, z: int, block_type: str) -> bool:
        """
        Places a block at the specified coordinates if available in inventory.
        Returns True if successful, False otherwise.
        """
        return self._send_command("place_block", x, y, z, block_type)

    def get_block_at(self, x: int, y: int, z: int) -> str:
        """Queries the world state for the block at the specified coordinates."""
        return self._send_command("get_block_at", x, y, z)

    def get_blocks_in_bounds(
        self,
        bounds_min: Tuple[int, int, int],
        bounds_max: Tuple[int, int, int],
    ) -> List[Dict[str, Any]]:
        """Returns a list of blocks within bounds as {x,y,z,block}."""
        return self._send_command("get_blocks_in_bounds", bounds_min, bounds_max)

    def get_inventory(self) -> Dict[str, int]:
        """Returns the current inventory as a dict of item_name -> count."""
        return self._send_command("get_inventory")

    def place_ops(self, ops: List[Dict[str, Any]]) -> None:
        """Batch place blocks. Each op: {x,y,z,block}."""
        self._send_command("place_ops", ops)

    def remove_ops(self, ops: List[Dict[str, Any]]) -> None:
        """Batch remove blocks. Each op: {x,y,z}."""
        self._send_command("remove_ops", ops)

    def set_inventory(self, block_type: str, count: int) -> None:
        """Sets the inventory count for a specific block type (Helper for testing)."""
        self._send_command("set_inventory", block_type, count)

    def close(self):
        if self.socket:
            self.socket.close()
