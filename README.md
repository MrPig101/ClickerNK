# ClickerNK – Cell to Singularity Auto Clicker

A fast auto-clicker for **Cell to Singularity** (Steam) with a simple UI.

## Features

- Pick any point on screen as the click target (saved between sessions)
- F4 (or any key) to start/stop — hotkey is configurable
- Sends mouse clicks + configurable key presses each iteration
  - Special keys: Space, Ctrl, Alt
  - Number keys 0–9 with All/None toggle
  - Numpad 0–9 with All/None toggle
  - Letters A–Z (excluding WASD) with a single toggle
- **Golden sphere avoidance** — pauses clicking when the golden sphere is detected at the target, with configurable cooldown
- Configurable CPS limit to avoid tanking game FPS
- All settings saved automatically between sessions

## Requirements

```
pip install pywin32 keyboard
```

## Run from source

```
python clicker.py
```

## Build exe

```
pip install pyinstaller
pyinstaller --onefile --noconsole --name ClickerNK clicker.py
```

The exe will appear in `dist/ClickerNK.exe`.

## Usage

1. Launch the game
2. Open ClickerNK and click **Pick Point**, then click the organism you want to click
3. Configure which keys to press and the CPS limit
4. Press **F4** (or your configured hotkey) to start — the game window is brought to the foreground automatically
5. Press **F4** again to stop — the mouse returns to its original position
