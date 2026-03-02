from minecraft_client import MinecraftClient
from builder import Builder

def main():
    print("Initializing Minecraft Client...")
    client = MinecraftClient()
    builder = Builder(client)

    pos = client.get_position()
    start = (int(pos[0]) + 2, int(pos[1]), int(pos[2]))
    end = (start[0] + 10, start[1] + 10, start[2] + 10)

    print("Starting manager/subagent build...")
    builder.build(
        prompt="Build a small wooden house (5x5 foundation)and make it look very nice.",
        bounds_min=start,
        bounds_max=end,
    )

if __name__ == "__main__":
    main()
