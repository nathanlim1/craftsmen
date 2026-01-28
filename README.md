# craftsmen
A multi-agent AI system for natural-language based construction in Minecraft.

RUNNING ON MINECRAFT 1.21.11 WITH MINESCRIPT 5.0b9


### Running the code with Minescript

To run these scripts with Minescript, you must edit your Minecraft instance’s
`minescript/config.txt`.

1. Set the Python interpreter path.
2. Add your repository folder to Minescript’s command search path.
3. Configure the listener to start automatically when entering a world.

On macOS, multiple paths in `command_path` are separated by `:`. It’s best practice to include `.` so Minescript still searches its own directory.

#### Example `config.txt`

```txt
# Lines starting with "#" are ignored.

python="/usr/bin/python3"
command_path=".:/Users/natha/Projects/craftsmen"

# Automatically start the listener when entering any world
autorun[*]=listener
```
