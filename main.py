from minecraft_client import MinecraftClient
import time

def main():
    print("Initializing Minecraft Client...")
    client = MinecraftClient()

    BLOCK_TYPE = "stone"
    BLOCK_COUNT = 5
    
    # If running in MineScript, we might want to use player's current position as start.
    start_pos = client.get_position()
    START_X = int(start_pos[0]) + 2
    START_Y = int(start_pos[1])
    START_Z = int(start_pos[2])

    print(f"Start Position: ({START_X}, {START_Y}, {START_Z})")

    # 3. Stock inventory for the test
    print(f"Setting inventory: {BLOCK_COUNT} blocks of {BLOCK_TYPE}")
    client.set_inventory(BLOCK_TYPE, BLOCK_COUNT)
    
    # Short sleep to allow game to update if needed
    time.sleep(0.1)
    
    inv = client.get_inventory()
    print(f"Initial Inventory: {inv}")
    
    # Verify inventory setup
    if inv.get(BLOCK_TYPE, 0) < BLOCK_COUNT:
        print(f"WARNING: Inventory setup might have failed. Expected {BLOCK_COUNT}, got {inv.get(BLOCK_TYPE, 0)}")

    # 4. Execute: Place N blocks in a line
    print(f"\n--- Starting Construction of {BLOCK_COUNT} blocks ---")
    
    for i in range(BLOCK_COUNT):
        # Calculate target position for the block
        x = START_X + i
        y = START_Y
        z = START_Z
        
        # Move near the target (simulating agent movement)
        # We move 1 block above where we want to place to avoid stuck inside block
        client.move_to(x, y + 2, z)
        time.sleep(0.05) # Throttle slightly
        
        # Place the block
        success = client.place_block(x, y, z, BLOCK_TYPE)
        
        if not success:
            print(f"Failed to place block at step {i} ({x}, {y}, {z})")
            break
        
        time.sleep(0.05)

    # 5. Verify: Check block presence
    print("\n--- Starting Verification ---")
    blocks_confirmed = 0
    
    for i in range(BLOCK_COUNT):
        x = START_X + i
        y = START_Y
        z = START_Z
        
        found_block = client.get_block_at(x, y, z)
        
        if found_block == BLOCK_TYPE or found_block == f"minecraft:{BLOCK_TYPE}":
            print(f"Confirmed {found_block} at ({x}, {y}, {z})")
            blocks_confirmed += 1
        else:
            print(f"Mismatch at ({x}, {y}, {z}): Expected {BLOCK_TYPE}, found {found_block}")

    # 6. Final Report
    print("\n--- Test Results ---")
    if blocks_confirmed == BLOCK_COUNT:
        print("SUCCESS: All blocks placed and verified correctly.")
    else:
        print(f"FAILURE: Only {blocks_confirmed}/{BLOCK_COUNT} blocks verified.")
        
    print(f"Final Inventory: {client.get_inventory()}")

if __name__ == "__main__":
    main()
