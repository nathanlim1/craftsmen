# craftsmen
A multi-agent AI system for natural-language based construction in Minecraft.

RUNNING ON MINECRAFT 1.21.11 WITH MINESCRIPT 5.0b9




# Setup Guide
---

## Requirements

* macOS
* Minecraft **Java Edition**
* Python **3.8+**
* Modrinth App
* Gemini API key (set as `GOOGLE_API_KEY`)

---

## Setup Guide

### 1. Install Minecraft Java Edition

Download and install Minecraft from:
[https://www.minecraft.net](https://www.minecraft.net)

Launch it once to confirm it works, then quit.

---

### 2. Install Modrinth

Download and install the Modrinth App:
[https://modrinth.com/app](https://modrinth.com/app)

Sign in with your Minecraft account if prompted.

---

### 3. Create a Fabric Instance

1. In Modrinth, click **Create Instance**
2. Choose **Create from scratch**
3. Configure:

   * **Game Version**: 1.21.11
   * **Mod Loader**: **Fabric**
4. Create the instance

---

### 4. Install Minescript

1. Open the instance
2. Go to the **Mods** tab
3. Install **Minescript**, version 5.0b9 w/ Fabric
4. Install **Fabric API** if prompted (required)

---

### 5. First Launch

Launch the instance once and reach the main menu, then quit.
This initializes the `minescript/` directory and config files.

---

### 6. Install Python

Verify Python is installed:

```bash
which python3
python3 --version
```

Python 3.8+ is required.
If needed:

```bash
brew install python
```

---

### 7. Configure Gemini API Key

Set your Gemini API key in the environment before running scripts:

```bash
export GOOGLE_API_KEY="your_key_here"
```

---

### 8. Configure Minescript

Open the instance folder in Modrinth and edit:

```
minescript/config.txt
```

Example configuration:

```txt
# Lines starting with "#" are ignored.

python="/usr/bin/python3"
command_path=".:/Users/natha/Projects/craftsmen"

# Automatically start the listener when entering any world
autorun[*]=listener
```

#### Notes

* On macOS, `command_path` entries are separated by `:`
* `.` ensures Minescript still searches the instance’s own directory
* `listener` refers to `listener.py` in this repository

---

## Running Scripts

From inside a Minecraft world, run any script in this project using chat:

```
\script_name
```

Examples:

```
\listener
\hello
```

With `autorun[*]=listener`, the listener starts automatically when entering a world.

---

## Long-Running Scripts

Minescript manages scripts as jobs:

```
\jobs        # list running scripts
\killjob ID  # stop a script
```

* Leaving the world or closing Minecraft automatically terminates all running scripts.
* Re-entering the world restarts any `autorun` scripts.

---

## World Pausing (Important)

In singleplayer, Minecraft **pauses the world when the game menu is open**.

When paused:

* Minescript commands may execute
* Block placement and world updates will not register until unpaused

### Recommendation

Open the world to LAN:

```
Pause Menu → Open to LAN → Start LAN World
```

This keeps the world ticking and prevents delayed block placement during development.
