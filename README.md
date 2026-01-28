# craftsmen
A multi-agent AI system for natural-language based construction in Minecraft.

RUNNING ON MINECRAFT 1.21.11 WITH MINESCRIPT 5.0b9


### Running the code with Minescript

To run these scripts with Minescript, you must edit your Minecraft instance’s
`minescript/config.txt`.

1. Add your repository folder to Minescript’s command search path using `command_path`.
2. On macOS, multiple paths are separated by `:` and it’s best practice to include `.` so Minescript still searches its own directory.

#### Example `config.txt`

```txt
# Lines starting with "#" are ignored.

python="/usr/bin/python3"
command_path=".:/Users/natha/Projects/craftsmen"
```

With this configuration, you can run any Python file in the repository directly from in-game chat using:

```
\script_name
```
