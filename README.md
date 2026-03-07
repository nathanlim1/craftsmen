# 🏗️ Craftsmen

An AI-powered Minecraft builder that turns natural language prompts into real in-game structures — built and placed in **survival mode** by [Baritone](https://github.com/cabaletta/baritone).

> *"Build a small oak cabin"* → LLM generates a block plan → saved as a `.schem` schematic → Baritone pathfinds, scaffolds, and places every block from your inventory.

---

## How It Works

```
┌──────────────┐       ┌──────────────┐       ┌──────────────────┐
│  main.py     │──────▶│  builder.py  │──────▶│  schematic.py    │
│  Orchestrator│       │  LLM Planner │       │  .schem Writer   │
└──────┬───────┘       └──────────────┘       └────────┬─────────┘
       │                                               │
       │  socket (JSON-RPC)                            │  file write
       ▼                                               ▼
┌──────────────┐                              %APPDATA%/.../schematics/
│ listener.py  │                              craftsmen_<prompt>_<ts>.schem
│ (MineScript) │
│  Baritone    │
│  #build cmd  │
└──────────────┘
```

1. **`main.py`** — Takes a build prompt (CLI arg or interactive), connects to the in-game listener, and orchestrates the pipeline.
2. **`builder.py`** — Uses Azure OpenAI (via LangGraph) to generate a validated list of block placements within a bounded volume.
3. **`schematic.py`** — Converts the block plan into a **Sponge Schematic v2** (`.schem`) file with a self-contained NBT writer. No external dependencies. Blacklisted blocks (doors, beds, tall plants, etc.) are automatically stripped.
4. **`minecraft_client.py`** — Socket client that talks to the in-game listener over `localhost:25560`.
5. **`listener.py`** — Runs **inside Minecraft** via MineScript. Configures Baritone (scaffold block, permissions) and issues `#build <file> <x> <y> <z>`.

### Scaffold Block

Baritone is configured to use **`minecraft:red_wool`** as its throwaway scaffold material for bridging and pillaring. The bright colour makes it easy to spot and tear down after the build completes.

---

## Requirements

| Dependency | Version |
|---|---|
| Minecraft Java Edition | 1.21.1 |
| MineScript | 5.0b9 (Fabric) |
| Baritone | Fabric build mapped to your exact MC version |
| Python | 3.10+ |
| Modrinth App | Latest |

### Python Packages

```bash
pip install -r requirements.txt
```

Contents: `langgraph`, `openai`, `python-dotenv`, `langchain`, `langchain-openai`, `pydantic`

---

## Setup

### 1. Modrinth + Fabric Instance

1. Install the [Modrinth App](https://modrinth.com/app) and sign in.
2. **Create Instance** → Create from scratch → Game version **1.21.1**, Loader **Fabric**.

### 2. Install Mods

In the instance's **Mods** tab, install:

- **MineScript** 5.0b9
- **Fabric API** (if prompted)
- **Baritone** — download the `baritone-standalone-fabric-*.jar` from [releases](https://github.com/cabaletta/baritone/releases) and drop it into the instance's `mods/` folder.

> **Important:** not every Baritone build works on every Minecraft version. Baritone jars are version-mapped — use the build that explicitly targets your exact Minecraft + Fabric version, or commands may fail / not load.

> **Verify Baritone:** Join a test world and type `#help` in chat. If it responds, you're good.

### 3. Configure MineScript

Open the instance folder and edit `minescript/config.txt`:

```ini
python="C:/Users/<you>/AppData/Local/Python/pythoncore-3.14-64/python.exe"
command_path=".;C:/Users/<you>/OneDrive/Documents/School/craftsmen"
autorun[*]=listener
```

| Setting | Notes |
|---|---|
| `python` | Full path to your Python executable. If MineScript job `listener` fails with error `9009`, this path is wrong. |
| `command_path` | Semicolon-separated on Windows, colon-separated on macOS. `.` keeps MineScript's own scripts visible. |
| `autorun[*]=listener` | Starts `listener.py` automatically when entering any world. |

### 4. Azure OpenAI

Create a `.env` file in the project root:

```env
AZURE_OPENAI_ENDPOINT="https://<resource>.openai.azure.com/"
AZURE_OPENAI_DEPLOYMENT="<deployment-name>"
AZURE_OPENAI_API_VERSION="YYYY-MM-DD"
AZURE_OPENAI_API_KEY="<key>"
```

`AZURE_SUBSCRIPTION_KEY` is accepted as a fallback for the API key.

### 5. First Launch

Launch the Modrinth instance, enter a world, and confirm you see:

```
[Craftsmen] Listener started on port 25560
```

in the in-game chat. Open to LAN (`Pause → Open to LAN → Start`) to prevent the world from pausing when you alt-tab.

---

## Usage

```bash
# Interactive prompt
python main.py

# Or pass the prompt directly
python main.py Build a small oak watchtower
```

`main.py` now reads build settings from `config/build_config.json` (or from the path in `CRAFTSMEN_BUILD_CONFIG`).
CLI prompt still overrides `prompt` from config.

On startup, `main.py` prints a build-config summary (config path, prompt source, mode, size, origin offset, scaffold block, and palette preview) before connecting.

### Build config

Example `config/build_config.json`:

```json
{
  "prompt": "Build a small oak watchtower",
  "mode": "baritone",
  "size": [7, 7, 7],
  "origin_offset": [3, 0, 0],
  "scaffold_block": "minecraft:red_wool",
  "palette": [
    "minecraft:oak_planks",
    "minecraft:oak_log",
    "minecraft:glass"
  ]
}
```

Config fields:

- `prompt` — default natural-language build request.
- `mode` — `baritone` (write `.schem` + run `#build`) or `auto` (place blocks directly via listener `/setblock`).
- `size` — `[width, height, length]` bounding box.
- `origin_offset` — `[dx, dy, dz]` from player position.
- `scaffold_block` — used by Baritone mode.
- `palette` — allowed block IDs for plan generation.

Set a custom config path (optional):

```powershell
$env:CRAFTSMEN_BUILD_CONFIG = "C:\path\to\my_build_config.json"
```

The program will:

1. **Generate** a block plan via the LLM
2. **Either** save a uniquely-named `.schem` file (`mode=baritone`) **or** place blocks directly (`mode=auto`)
3. **Print** a materials list
4. **Run** Baritone `#build` (baritone mode) or direct placement (auto mode)

`mode=auto` uses direct `/setblock` placement through the listener (creative-like behavior) and does not use Baritone pathfinding.

### Override schematic directory (optional)

If your Modrinth profile is not named `Craftsmen`, set:

```powershell
$env:CRAFTSMEN_SCHEMATICS_DIR = "C:\Users\<you>\AppData\Roaming\ModrinthApp\profiles\<YourProfile>\schematics"
```

Then run `python main.py` in the same shell. This path is used for writing `.schem` files.

### Materials

Before Baritone starts building, the console prints the exact items and quantities needed:

```
Materials needed in inventory:
  minecraft:cobblestone: 12
  minecraft:oak_log: 8
  minecraft:oak_planks: 45
  minecraft:torch: 4
```

Stock your inventory with these items. If Baritone runs out of a material mid-build, it will pause — add more items and type `#build` in chat to resume.

### In-Game Controls

| Chat Command | Effect |
|---|---|
| `#stop` | Pause / cancel the current build |
| `#build` | Resume a paused build |
| `\jobs` | List running MineScript scripts |
| `\killjob <id>` | Stop a MineScript script |

---

## Project Structure

```
craftsmen/
├── main.py              # CLI entry point & orchestration
├── evaluate.py          # Quantitative evaluation script
├── listener.py          # MineScript in-game server (Baritone bridge)
├── requirements.txt     # Python dependencies
├── src/                 # Core package
│   ├── agents/          # LLM-based agents (builder, manager, validator)
│   ├── core/            # Schematic, world state, block IDs
│   ├── config_defaults.py
│   └── minecraft_client.py
├── config/              # Config and data files
│   ├── build_config.json
│   ├── blacklisted_blocks.json
│   └── prompts.txt
├── scripts/             # Utility scripts
│   └── fetch_blocks.py
├── docs/
│   └── overview.txt
└── .env                 # Azure OpenAI credentials (not committed)
```

Run from the project root: `python main.py` and `python evaluate.py` work directly.

---

## Blacklisted Blocks

Certain multi-part or state-dependent blocks can't be reliably placed by Baritone's `#build`. These are automatically stripped from schematics:

The blacklist now lives in `config/blacklisted_blocks.json`. Edit that file to add/remove blocked block IDs without changing Python code.

Supported JSON formats:

```json
["minecraft:oak_door", "minecraft:oak_stairs"]
```

or

```json
{"blocks": ["minecraft:oak_door", "minecraft:oak_stairs"]}
```

| Category | Examples |
|---|---|
| Doors | `oak_door`, `iron_door`, all wood types |
| Beds | All 16 colours |
| Tall plants | `tall_grass`, `sunflower`, `rose_bush`, `lilac`, `peony` |
| Banners | `white_banner`, `black_banner` |

The LLM palette in `main.py` already excludes these, but the schematic writer has a safety-net filter in case they appear.

---

## Singleplayer Pause Warning

In singleplayer, Minecraft **freezes the world** when the game menu is open. Block placement and Baritone pathing will stall until you unpause.

**Fix:** Open to LAN once per session (`Pause → Open to LAN → Start`). This keeps the world ticking while you work in your terminal.

## Quantitative Evaluation

We evaluate the system at two stages of the pipeline to measure whether the agents correctly generate and translate build plans.

### 1. Planning Validity

Measures whether the LLM generates a valid block plan.

Metrics:

* **Bounds Validity** – fraction of block operations within the allowed build region
* **Palette Validity** – fraction of operations using allowed block types
* **Duplicate Coordinate Rate** – how often multiple blocks are assigned to the same coordinate
* **Plan Size** – total number of block operations generated

These metrics are computed directly from the operation list produced by the `Builder`.

### 2. Operation List → Schematic Fidelity

Measures whether the schematic produced from the operation list accurately represents the intended block placements.

Metrics:

* **Exact Match Rate** – fraction of blocks matching exactly `(x, y, z, block)`
* **Missing Blocks** – blocks expected but not present in the schematic
* **Extra Blocks** – blocks present in the schematic but not in the plan
* **Block Type Mismatch** – coordinates where the wrong block type appears

The schematic is parsed and compared against the filtered plan used by the schematic generator.

---

## Running the Evaluation

Evaluate a single prompt:

```bash
python evaluate.py --prompt "Build a small wooden hut"
```

Evaluate multiple prompts from a file:

```bash
python evaluate.py --prompt-file config/prompts.txt
```

Each prompt generates a build plan, creates a schematic, and reports planning validity and schematic fidelity metrics.
