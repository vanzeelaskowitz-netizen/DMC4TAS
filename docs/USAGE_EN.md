# DMC4TAS user guide

A frame-by-frame TAS tool for the PC Steam version of Devil May Cry 4: write input frame by frame, advance one frame at a time, step back and rewrite, working much like Celeste Studio.
For single-player use only; it doesn't change game files or saves. 中文版: [USAGE.md](USAGE.md)

**How to read this guide**: if this is your first time, read in order from "1. Setup" through tutorials 1 and 2 of "5. Step-by-step tutorials" and you'll be ready to go;
look up the other sections when you need them. The full script syntax is in [SCRIPT.md](SCRIPT.md) (Chinese).

---

## 0. Supported versions and features

| Version | Steam app | Executable | Build |
|---|---|---|---|
| Special Edition | 329050 | `DevilMayCry4SpecialEdition.exe` | 24329835 |
| Original (DX9) | 45700 | `DevilMayCry4_DX9.exe` | 21360 |

`DevilMayCry4_DX10.exe` in the original's folder is not supported. Both versions use the same tools and the same player; the tool picks the right one from the game executable.

| Feature | Special Edition | Original (DX9) |
|---|---|---|
| Step forward, step back, edit the script, play | ✓ | ✓ |
| Record keyboard input | ✓ | ✓ |
| Controller input (recording and scripts) | — | ✓ (script playback tested; recording with a real controller not tested yet) |
| Save state / load state | ✓ (not tested much in the game yet) | ✓ (not tested much in the game yet) |
| Practice aids F1 F2 F3 F5 | ✓ | ✓ (F1's enemy HP lock and F5 One-hit kill cover only 14 kinds of regular enemies; bosses and units that can't be killed in the story are skipped) |
| F6 Enemy Step boxes | ✓ | ✓ (the colour changes haven't yet been checked frame by frame in real fights) |
| Exceed point (Nero's Exceed) marks | ✓ | ✓ |
| Game sound effects during playback | Unavailable by default (see section 9) | ✓, from the current frame, no music |
| TAS frame rate | Fixed at 60 | Follows the game's Frame Rate setting (see section 11) |
| Switching to another window while you play yourself | The game freezes in place | The game keeps running |

---

## 1. Setup (once only)

### 1. The game

- A legitimate Steam copy; Steam must be running and logged in.
- The game must be in **windowed mode**. The tool makes the game wait on a frame for you; a frozen full-screen game covers the whole screen and the computer becomes unusable,
  so the tool doesn't freeze the game in full screen.
  - Special Edition: in the game, set Options → PC Settings → Full Screen to OFF.
  - Original: open `%LOCALAPPDATA%\CAPCOM\DEVILMAYCRY4\config.ini` in Notepad (make a backup copy first),
    and change `FullScreen=ON` to `FullScreen=OFF`.
- Don't run other trainers at the same time. The tool has its own practice aids such as Lock HP and Infinite DT (section 8), and values changed by other trainers are not written into the script either,
  so replays won't match.

### 2. Python

Install Python 3.13 (64-bit) from [python.org](https://www.python.org/downloads/). Leave "py launcher" ticked during installation.

### 3. Download the tool

On the GitHub page click **Code → Download ZIP** and unzip it into any folder (if you use git, `git clone` works too).

### 4. Install the components

Double-click `setup.bat`. It creates a `.venv` folder in the tool's folder and installs the components needed (needs an internet connection, takes a few minutes).
When you see "Done." you're all set; if it fails, send the error messages in the window to the author. You only need to do this once; later, when you update the tool, if
`requirements.txt` has changed, double-click it again.

### 5. Note down your key bindings

**Scripts contain keyboard keys, not in-game actions.** First check in the game's key settings which key each action is bound to; you'll need them later for writing scripts.
The sample scripts in the repo and this guide use the author's bindings. If you want to use the samples as they are, the easiest way is to set the game's bindings the same:

| Action | Special Edition | Original |
|---|---|---|
| Lock on | `Space` | `Space` |
| Melee (sword) | `J` | `J` |
| Jump | `K` | `K` |
| Shoot | `L` | `L` |
| Devil Bringer (Nero) | `I` | `RightAlt` |
| Exceed (rev) (Nero) | `Q` | `Q` |
| Move | `W` `A` `S` `D` | `W` `A` `S` `D` |
| Turn camera left / right | `LeftShift`, `F` | `LeftShift`, `F` |

Movement is **relative to the camera**: `W` is "forward" on screen, not always towards a particular enemy.

**Better bind the camera to the keyboard too.** Recording only captures the keyboard (and on the original, the controller); camera turns made with the mouse aren't recorded, so on replay the camera faces a different way,
and the same `W` walks off in another direction.

The Special Edition's Turbo (in Game Options) changes the game speed. The same script plays out differently with Turbo on and off,
so keep the same setting when making and playing scripts. All frame counts in this repo's samples and guide were measured with Turbo on.

---

## 2. Starting up (every time)

### 1. Double-click `start_game.bat`

1. **Choose a language**: press `1` for English, `2` for Chinese. If you press nothing for 5 seconds, it keeps your last choice (stored in `language.txt`).
   The player, the script window and the messages all use this language.
2. **Choose the version** (automatic):
   - If one version of the game is already running, it uses that one; if both are running, a window asks you to choose
     (it uses one directly only if a game window happens to be in front at that moment, which is rare because you have just double-clicked the .bat).
   - If neither is running and only one is installed, it starts that one; if both are installed, a window asks you to click "Original (DX9)" or "Special Edition", and next time your last choice is the default.
   - The original starts directly in DX9, without Steam's DX9/DX10 choice box.
3. **Find the game**: found automatically through Steam. If it can't be found (for example, the game is in another folder), a window asks you to pick the game executable; you pick it once and it is remembered
   (`game_path.txt` / `game_path_dx9.txt`). The tool identifies versions by SHA-256. If you pick an unsupported executable, a window shows its SHA-256;
   if you want that version supported, send that string to the author.
4. When the black window finally shows "**The tool is attached and you have control of the game.**", it worked. Press any key to close the black window; the tool keeps running in the background.

**Always start the game with `start_game.bat`.** That way the tool can turn on "write watch" the moment the game starts, and each step back takes only about 0.02 s.
If the game is already running, it only attaches, and each step back takes about 0.2 s; everything else works the same. If startup says "write watch is not fully on", it means the same thing: it is just a bit slower.

### 2. Go to where you want to practise

Play as usual until you reach the room you want to practise in. Where you can take over:

- Special Edition: any room where you control the character (not in menus, loading screens or cutscenes).
- Original: **the title screen or a combat room**. Not in menus such as mission select.

For practising combos, the scarecrows on floor 1 of Bloody Palace are a good choice: the enemies are weak and can't kill you. With F1 Lock HP and F3 Freeze timer on (section 8),
the countdown won't kick you out either.

Switching to another window while you play yourself: the Special Edition freezes in place (no pause menu) and carries on when you switch back; the original does not freeze and keeps running,
so don't leave your character standing next to enemies taking hits.

### 3. Double-click `tas_player.bat`

Two always-on-top windows appear: the **main window** "DMC4 Frame Player" and the **script window** "DMC4 Input script".
The main window's title also shows which version is running and the frame rate the TAS runs at, for example "DMC4 Frame Player · Original (DX9) · 60 fps".

Closing the script window only hides it; the main window's "Script window" button brings it back. Closing the main window is what closes the player; closing the player doesn't close the game,
and doesn't detach the tool either. If you close the main window while the game is taken over, the game stays frozen at the frame gate (the background service keeps holding it):
double-click `tas_player.bat` again and press "⏵ Hand back".

---

## 3. A few concepts first

- **Take over**: press "⏸ Take over", the game stops on the current frame and the tool takes over the input. That moment is **frame 0**, and the stretch from there on is a **timeline**.
- **One row, one frame**: script row 1 is the input for frame 1, row 2 for frame 2, and so on. When the screen shows "Frame N", it means the first N rows have been run.
- **Step forward**: run the next row. **Step back**: restore the game's memory to how it was one frame earlier (load a snapshot); it is not playing backwards.
  If you change the input after stepping back, the frames after it are worked out again from the new script.
- **Change rows that have already played** and the game automatically goes back to the change, replays the new script up to the frame you are on, and the picture updates right away.
- **Hand back**: the timeline ends and you get the game back. The script file stays; the next take over starts again from row 1.
  If anything on the timeline has been restored, the game first goes back to an earlier frame (section 10).
- **The game is silent while taken over.** This is what makes stepping back safe; if you want sound, see section 9.
- **Loading screens**: if nothing has been restored on this timeline yet (no step back, no edit of an already-played row, no ⏮ / progress bar / double-click jump backwards, no loaded save state),
  reaching a loading screen (for example at a door) ends the timeline and hands the game back by itself: the game loads through normally, and save states are dropped.
  After any restore, a timeline can't cross a loading screen: the tool stops before the loading; step back a few frames, or hand back and walk through yourself.
- Scripts record only keys, **not positions**. The same script gives the same result only when it starts from the same position, the same camera direction and the same enemy state.

---

## 4. The interface at a glance

### Main window "DMC4 Frame Player"

From top to bottom:

1. **● Record**: start / stop recording your keyboard input (numpad `*` works too).
   **Verify by replay**: when ticked, stopping a recording replays it once from the frame where the take began (not always frame 0, see "Recording while taken over" in section 7) to check that the result matches the recording (this takes as long as the recording did). Off by default.
   **⏸ Take over / ⏵ Hand back**: the same button; after you take over it becomes Hand back.
   **Script window**: brings up the script window.
2. **⏮ Start** (back to frame 0), **◀ 10**, **◀ 1**, **▶ Play**, **1 ▶**, **10 ▶**.
3. **Practice aids**: F1 Lock HP (you + enemies), F2 Infinite DT, F3 Freeze timer, F5 One-hit kill, F6 Enemy Step boxes.
   A button that turns green when pressed is on. Whenever F1 or F5 is on, one more line appears below them, "Enemies covered n/m  skipped: …",
   saying how many enemies the enemy HP lock / One-hit kill covers and which it skipped (on both versions).
4. **Progress bar**: drag it to jump to a frame.
5. **State panel**: the detailed state of the current frame (explained below).
6. At the bottom: control hints, and a line of status messages. When something goes wrong, that line changes colour: look there first.

#### Reading the state panel

At the top in large type: "Frame N / total". When you haven't taken over, it shows "You are playing".

Special Edition:

| Line | Meaning |
|---|---|
| Keys held | The keys the script holds on this frame |
| Character read | What the game character actually read: held / pressed (just pressed) / released buttons, and the direction. If this doesn't match the line above, the game didn't read your keys |
| Action  motion @ frame | The character's current action number, animation (motion) number and frame within the animation; then grounded / airborne, Enemy Step cooldown, HP |
| Exceed gauge | Nero's Exceed gauge and whether this frame is an Exceed point (tutorial 3 in section 5) |
| Position | Coordinates and how far the character moved this frame |
| In hit stop | How much of the hit stop after hitting something is left |
| Enemy 1 … | Every enemy, sorted by distance: type, HP, distance, whether it is inside the Enemy Step sphere, whether it can be stepped on. The row of an enemy you can step on is green |
| ★ Enemy Step on this frame | An Enemy Step happened on this frame |

Original: the first line is "Keys held" too, as on the Special Edition; then the scene class name and the time units per frame; then action, animation (motion), the number of character input updates and HP;
then the Exceed line; then position; then each enemy's type, HP, action and distance. The original has no "Character read" line.

If "**The character read no input on this frame**" appears, the game's own pause screen is most likely open: write one row of `Escape` in the script and run one frame to close it.

### Script window "DMC4 Input script"

1. **New**, **Open…**, **Save as…**; **↑ Select same key**, **↓ Select same key**; on the right, the "**Sound effects**" checkbox.
2. **⏮**, **◀ 1**, **▶ Play**, **1 ▶**; the "**Go to frame** [ ]" box; on the right, the "**Fold identical rows**" checkbox.
3. **Save**, **Load ▾** (tutorial 5 in section 5).
4. Progress bar and the current frame number.
5. File name line: `script-20261125-101500.tas  120 frames  (auto-saved)`. A ⚠ means this script was recorded at a different frame rate from the current one (section 11).
6. **Editing area**: the left column is the frame number, the middle is the script, and the right column shows that frame's animation and Exceed points. The row with a blue background is the input used by the frame currently on screen.
7. At the bottom: syntax hints and error explanations. Rows with mistakes are marked red; until you fix them, the game keeps using the previous version of the script.

You can drag a `.tas` file straight from File Explorer into the script window to open it.

---

## 5. Step-by-step tutorials

### Tutorial 1: Record a clip, change one frame (5 minutes)

Goal: learn to record, play back and edit a script.

1. Start up as in section 2, go into a room with enemies, and open the player.
2. Press **● Record** (or numpad `*`). The status line shows "Recording.".
3. Click the game window once and play normally for a few seconds: lock on to an enemy, slash a few times, jump. There is no sound while recording; when you switch to another window the game freezes, and that part isn't recorded.
4. Press numpad `*` again to stop. The keys you pressed appear frame by frame in the script window, and the file is saved as `runs\take-<time>.tas`.
5. Press **⏮** (back to frame 0), then **▶ Play** (or numpad `/`): the game replays that clip from the script and stops on the last row.
6. Find a row with `J` (slash). Delete the `J` from that row, and add a `J` 5 rows further down.
   The game automatically goes back to the change and replays, and the animation number in the right column appears at the new place. Press **⏮** and play again to see the result.
7. When you're happy, press "**⏵ Hand back**" and you have control of the game again.

Tips:

- 3 or more identical rows in a row are folded into one ("Fold identical rows"); the frame-number column on the left shows the range it covers (for example 1–19) with a triangle.
  A single click on that frame number expands it (or folds it again); a double click jumps the game to that frame.
- To delete every `J` in a stretch at once: put the cursor on any `J` and press `Ctrl+Shift+↑` or `↓` (or the "Select same key" buttons);
  all the `J`s in the adjacent rows are selected. Press `Delete` to remove them, or just type to replace them.

### Tutorial 2: Write an Enemy Step jump by hand from scratch (Enemy Step / JC)

Goal: learn to take over, go frame by frame, read the Enemy Step boxes, and press jump on exactly the right frame.

1. Stand next to an enemy, fairly close. Turn on F1 Lock HP (in Bloody Palace, also F3 Freeze timer).
2. Press "**⏸ Take over**". The status line shows "Taken over: the game is stopped at the frame gate. This is frame 0…".
3. In the script window click "New" and write:

   ```
   5 Space
   Space K
   40 Space
   ```

   When the cursor leaves a row, rows with a number expand automatically: hold lock-on for 5 frames, press jump together with it on frame 6, then keep holding lock-on for 40 more frames.
4. Press numpad `+` to go one frame at a time (or press **1 ▶**). After frame 6, the character jumps.
5. Turn on **F6 Enemy Step boxes**. Boxes appear on the enemy:
   - **Red**: press jump on this frame and you will step on it.
   - **Yellow**: within reach, but you can't step yet (the first few frames of a jump, cooldown not over, standing on the ground).
   - **Green**: out of reach.
6. Keep pressing `+` to go forward. The box colour is the verdict for the frame on screen (the row with the blue background), and a `K` written on that row is used on that same frame.
   On the frame where a box first turns **red**, change `Space` on **that row** to `Space K`. The game goes back and replays up to this frame automatically, so the new `K` has already run.
7. If it stepped, the main window's state panel shows "**★ Enemy Step on this frame**" and the right column of that row shows the new animation (number 17 on the Special Edition) right away;
   you don't need to press `+` again. You've stepped on the enemy.
8. Didn't step? Press numpad `-` to go back, move the `K` one row earlier or later, and try again. Change a row, take a look: that's the basic rhythm of TAS.
9. If the boxes stay green, you can't reach even when you jump: stand a bit closer and take over again, or add a direction key towards the enemy on the jump row (for example `Space W K`).

For roughly the first 8 frames after an Enemy Step, attack presses are swallowed (measured at 60 fps), so wait a few frames before attacking; the exact number is whatever you find stepping frame by frame.

### Tutorial 3: Exceed points (Nero's Exceed)

Goal: use the marks in the right column to press Exceed on exactly the right frame.

1. Take over, write a few empty rows and then a row with `J` (slash), and step forward about 30 frames.
2. On some frames of the slash, the right column shows:
   - gold "**MAX pt**": write the Exceed key (`Q` in the samples) on this row and the Exceed gauge fills all 3 bars at once.
   - red "**Exceed pt**": write it on this row for +1 bar.
3. Add `Q` to the gold row (only this row; the row before it must not have `Q`). After the replay, this row shows "**★ MAX ok**" and the Exceed gauge in the state panel goes to 3.
4. A folded stretch shows only the best frame in it and that frame's number; click its frame number on the left to expand it and see every frame.

The length of the Exceed window changes with hit stop and game speed, so there is no fixed frame table: **go by the marks you actually step through**.
Marks appear only on frames you have stepped through; if you change earlier input, you have to step through again to update them.

### Tutorial 4: Using the sample scripts

1. In the script window click "**Open…**" and pick a sample in `runs\` (or drag it straight into the window).
2. Set up the starting position, take over, and press **▶ Play**. Only `dx9-exceed-demo.tas` states a starting position in its opening comment; the others don't,
   so stand next to an enemy (on the Special Edition, for example a Bloody Palace scarecrow) and expect the result to differ from the author's.

**Playing and stepping always use the script that is open in the script window.** To play a sample, you must open it in the script window first.

**An opened file is saved back to that same file as you edit, samples included.** Files in the old format (`sample.tas`, `demo.tas`) are rewritten in the one-row-per-frame format on the first edit.
If you want to keep the original, use "**Save as…**" first and edit the copy.

| File | Version | Contents |
|---|---|---|
| `sample.tas` | Special Edition | A small 8-frame example (the old "frame: keys" syntax) |
| `demo.tas` | Special Edition | Lock on and move forward for 200 frames, then slash and jump |
| `streak-example.tas` | Special Edition | Streak: lock on, back, forward + slash; animation 792 starts on frame 12 and dashes forward |
| `fight-probe.tas` | Special Edition | About 500 frames of real fight input in Bloody Palace, used during development to test stepping back |
| `dx9-exceed-demo.tas` | Original | Bloody Palace scarecrows, a combo of 11 × "EX Calibur, JC one frame after it hits, Exceed kept"; the opening comment gives the starting position (Nero standing about 350 away to the right of the scarecrow) |

A sample gives the same result only when the starting position, the camera and the key bindings are all the same (section 3).

### Tutorial 5: Save states and load states

In the script window, "**Save**" records the current frame under a name you give it; "**Load ▾**" lists all save states, and when you click one the game goes back to that frame.

- What is saved is "frame N of this script". After loading, the script window still has the same script open and the frame number is unchanged; you can step back all the way to frame 0, and you can go on editing the rows after that frame.
- Save states can still be loaded after you hand back, play a while yourself and take over again. In that case, on the Special Edition, loading first waits for the sound playing in the game to finish,
  which can take a few seconds; on the original it loads at once. On the Special Edition the load can also be refused when music buffers or sounds won't stop: try again a moment later.
- A timeline you get by loading a save state can't play with sound effects.
- When you hand back, the game returns to the hand-back point described in section 10 (the moment of this take over, or the frame where the last recording ended), not to the saved frame.
- A save state is a snapshot of the game's memory and is valid only **during this run of the game, while this room has not been reloaded**. After a room change, a loading screen, a restart from a checkpoint,
  closing the game or restarting the tool, all save states become invalid (the script files remain).
- At most 12; delete the ones you don't need under "Load ▾ → Delete save state". While save states exist, the game doesn't free graphics resources; if you keep save states and play on for a long time,
  the tool gives up the save states itself so the resource table doesn't fill up.

### Tutorial 6 (original): Using a controller

On the original you can write controller input in scripts, and you can also record with a controller. Syntax:

```
PadRB                 # lock on
PadRB PadLS=0/1       # lock on, left stick pushed fully up
PadRB PadLS=0/1 PadY  # lock on, stick up, slash (Y is the sword by default)
PadLT                 # left trigger pulled all the way
```

- Buttons `PadA` `PadB` `PadX` `PadY` `PadLB` `PadRB` `PadBack` `PadStart` `PadL3` `PadR3`, d-pad `PadUp` `PadDown` `PadLeft` `PadRight`.
- Sticks `PadLS=x/y`, `PadRS=x/y`: x is right, y is up, range −1 to 1. Triggers `PadLT`, `PadRT`; `PadLT=0.5` is pressed halfway.
- Keyboard and controller keys can be written on the same row. During playback, a row with no controller input counts as the controller fully released, so if a controller is plugged in, touching it by accident won't leak into playback.
- When you record with a controller, stick values keep the game's exact values.
- Playing controller input from a script has been tested; recording with a real controller hasn't been tested yet.

See the "手柄" (controller) section of [SCRIPT.md](SCRIPT.md) for details.

---

## 6. Key reference

| Key | What it does | Where it works |
|---|---|---|
| Numpad `*` | Start / stop recording | Any window (while the player is open) |
| Numpad `/` | Play / pause | Any window |
| Numpad `+` | Forward one frame | Any window |
| Numpad `-` | Back one frame | Any window |
| `←` `→` | Back / forward one frame | Main window |
| `Shift` + `←` `→` | Back / forward ten frames | Main window |
| Space | Play / pause | Main window |
| `Home` | Back to frame 0 | Main window |
| `F1` `F2` `F3` `F5` `F6` | Turn practice aids on / off | While the player is open and the game window is in front (F1–F5 are ignored while recording) |
| `Ctrl+Shift+↑/↓` | Select the same key in adjacent rows | Script window |
| Double-click a frame number | Jump to that frame | Script window |
| Type a frame number in the "Go to frame" box and press Enter | Jump to that frame | Script window |

Numpad `+ - * /` work regardless of NumLock. On a laptop without a numpad, use the buttons in the two windows and the arrow keys in the main window.

---

## 7. Script syntax at a glance

```
S W          # frame 1: hold S and W together
S W          # frame 2: keep holding them
J            # frame 3: press only J
# frame 4 is the empty row below: nothing pressed

5 K          # frames 5-9: expands to 5 rows of K
# a whole-line comment takes no frame
```

- Text after `#` is a comment, both on a line of its own and at the end of a row; a whole-line comment takes no frame.
- Separate key names with spaces or commas; case doesn't matter. To hold a key for several frames, write it on that many consecutive rows; on a row where it isn't written, it is released for that frame.
- To press a number key, write `1 5` (1 frame, press 5).
- A single click on a frame number in the left column only folds or unfolds a folded run; a double click jumps the game to that frame.
- To get to a frame below the last written row, click in the empty script area on the row of that frame: the cursor lands there, and the rows in between are filled with empty rows.
- New scripts are saved automatically in `runs\` as you edit; an opened file is saved back to where it is. A change takes effect 0.4 s after you stop editing.

For the full reference (key name list, old syntax, file format), see [SCRIPT.md](SCRIPT.md).

### How playback works

- Press "▶ Play": plays the script from the current frame at normal speed to the last row, then stops. Pressing it again during playback pauses.
- Press Play again after it has finished: goes back to frame 0 and replays from the start.
- Write more rows after it has finished: pressing Play again plays on from where it stopped through the newly written part.
- When the script is empty, Play just lets the game run.

### Recording while taken over

If you press Record after you've taken over, it depends on whether anything on the timeline has been restored:

- **Something has been restored** (a step back, a loaded save state, an edit of an already-played row, a jump backwards): recording **continues from the current frame**.
  When you stop, the script is the earlier frames plus this take. The status line says "Recording on from frame N".
- **You have only stepped forward**: Record starts a new timeline at the current frame, and the take file holds only what was recorded.

"Verify by replay" replays from the frame where the take began, which is not always frame 0.

---

## 8. Practice aids

| Toggle | What it does |
|---|---|
| F1 Lock HP | Your HP and the enemies' HP are locked at full |
| F2 Infinite DT | The Devil Trigger gauge stays full |
| F3 Freeze timer | The Bloody Palace countdown (on the original, also the mission timer) doesn't run |
| F5 One-hit kill | Enemy HP stays at 1 (when it's on together with Lock HP, this one wins) |
| F6 Enemy Step boxes | Marks the points on enemies you can Enemy Step off, on the game screen |

- You can click the buttons in the main window, or press the F keys while the game window is in front. The F keys work only while the player is open (the player polls them)
  and the game window is in front; F1–F5 are ignored while recording.
- For the colours of the Enemy Step boxes, see tutorial 2. When stepping frame by frame the colours are accurate; in live play they may lag by about a frame.
- On the original, F1's enemy HP lock and F5 One-hit kill cover 14 kinds of regular enemies; bosses and units that can't be killed in the story are skipped by both.
- Whenever F1 or F5 is on, the line under the practice-aid buttons, "Enemies covered n/m  skipped: …", shows how many enemies are covered and which were skipped (on both versions).
  On the original, an enemy that isn't covered is listed there by its class name.
- Values the practice aids change don't count as input. A script made with Lock HP on may play out differently when you replay it with Lock HP off, for example because an enemy dies earlier.

---

## 9. Sound during playback

By default the game is silent while taken over. Tick "**Sound effects**" in the script window and press Play to hear sound effects such as hits and voices, **without music**.

- **Original**: plays from the current frame at game speed; on frame 0, or at or past the script's last row, it starts from frame 1 instead. The system mutes DirectSound programs that aren't in the foreground, so when the player starts playing with sound effects it automatically
  brings the game to the front; don't click other windows during playback. When you start from the middle, sounds queued up during the earlier silent frames may all play at once.
- **Special Edition**: plays from frame 1 of the timeline. But the sound guard it needs is off by default (it is related to the crash on "hand back after a step back" and is disabled until that is fixed),
  so the checkbox is usually greyed out and reads "sound guard off".
- After playback finishes or is paused, the game goes back to silence and stays on the frame it reached.
- After you hand back, the music comes back.

When the checkbox is greyed out, the reason is in brackets:

| Reason | Meaning |
|---|---|
| sound guard off | The Special Edition default |
| timeline unguarded | This timeline came from loading a save state, or started before the sound guard was turned on. Hand back and take over again |
| a sound thread faulted: silent until the game restarts | Sound ran into an error, so the tool disabled sound to be safe; it comes back after you restart the game |

---

## 10. Finishing and handing back

Press "**⏵ Hand back**" and you have the game back; the script stays.

After any restore on the timeline (a step back, an edit to an already-played row, ⏮ Start, dragging the progress bar or double-clicking a frame number backwards, loading a save state),
handing back first returns the game to "the moment the sound threads were put to sleep" before giving it to you: the frame you took over on, or the frame where the last recording ended.
On the original, a play with sound effects also moves this point to the frame where that playback stopped.
That way the sound can come back and doors won't hang. So after handing back, your character may be back at an earlier position; this is normal.

---

## 11. Frame rate (original)

On the original, the TAS frame rate follows the **Frame Rate** in the game settings:

- `60 FPS MAX`: the TAS runs at 60 fps.
- `VARIABLE`: runs at the monitor's refresh rate; on a 120 Hz monitor, for example, that's 120 fps.

The main window's title shows the current frame rate. The first row of a recorded script states the frame rate it was recorded at (for example `# 120 fps`); when you open a script whose frame rate differs from the current one,
"⚠ made at 120 fps, the game runs 60 fps" appears after the file name. At a different frame rate, the same number of rows stands for a different length of time, so scripts aren't interchangeable.

The frame counts in this guide and the samples (for example "attacks are swallowed for about 8 frames after an Enemy Step", "JC one frame after the hit") were all measured at 60 fps; at 120 fps they roughly double,
and you should go by what you actually step through. If you want to share scripts with others, `60 FPS MAX` is recommended.

The Special Edition is currently fixed at 60 fps.

---

## 12. Where files are kept

| Location | Contents |
|---|---|
| `runs\script-<time>.tas` | New scripts, saved automatically as you edit |
| `runs\take-<time>.tas` | Recorded scripts |
| `runs\save-<name>.tas` | When you load a save state and the original script has been changed so much that it can no longer get back to the saved frame, a copy of the script as it was when you saved |
| `language.txt` | Interface language (`zh` / `en`) |
| `last_game.txt` | The version you picked last time |
| `game_path.txt`, `game_path_dx9.txt` | Game locations you picked by hand |
| `logs\` | Background service logs; `tas-service.err.txt` holds the errors |
| `logs\game-exception.log` | Where the game crashed |

Save states (tutorial 5 in section 5) live only in memory and are not written to files.

---

## 13. Known limitations

- **On the original, don't minimise the game window.** When it is minimised, D3D9 loses the graphics device; to stay safe, the tool clears all snapshots and hands control back to you,
  and the current timeline is gone (the script file remains). Being covered by other windows is fine.
- On the original, handing back after a restore (a step back, an edit of an already-played row, a jump backwards, a loaded save state) hasn't been tested much yet
  (the Special Edition has crashed handing back after a step back). If you restored a lot, the safest way to finish is to just close the game.
- Loading screens: if nothing on the timeline has been restored yet, reaching a loading screen ends the timeline and hands the game back by itself (the game loads through normally, save states are dropped).
  After any restore, a timeline can't cross a loading screen: at a door or a loading screen the tool stops before the loading; step back a few frames, or hand back and walk through yourself.
- After a restore you can't detach the tool directly; hand back first.
- Camera turns made with the mouse while recording aren't recorded, so replays may differ from the recording.
- Restarting the tool's background service loses the current timeline (the script file remains, but the starting state is gone).
- Using it together with other trainers may cause conflicts.
- Only some of Nero's moves have been verified; other characters, boss fights and non-Turbo mode have barely been tested.

---

## 14. Common problems

**The numpad does nothing.** The numpad hotkeys only work while the player is open; make sure `tas_player.bat` is running. With two players open at once,
the one opened later can't get these hotkeys, and the numpad only works for it while you click in its main window: close the extra one.

**The player can't connect to the game.** Make sure the game is running and the tool is attached (double-click `start_game.bat` again; if the game is already running, it says "The game is already running (PID …) with the tool attached, nothing to do.").

**After restarting the background service, the player doesn't respond.** Close both player windows and double-click `tas_player.bat` again.

**Starting a recording says "The game did not stop at the frame gate".** Press it again.

**Take over fails, saying the pause screen won't close.** Switch to the game, close the pause screen by hand, then take over again.

**The replay differs from the recording.** Common causes: turning the camera with the mouse; a different starting position or camera; practice aids switched on or off midway;
a changed Turbo or frame rate setting; another trainer running. Tick "Verify by replay" to check right after recording.

**I changed the script but the picture didn't change.** Look for rows marked red (a mistake; the bottom of the window explains it); make sure you edited the script that is in use in the script window.

**It stopped at a door with "Reached a scene change (loading screen)".** This happens after something on the timeline has been restored (a step back, an edit of an already-played row,
a jump backwards, a loaded save state); from then on a timeline can't cross a loading screen. Step back a few frames; to go through the door, hand back and walk through yourself.
If nothing has been restored, the tool doesn't stop there: it ends the timeline and hands the game back by itself, the game loads through normally, and save states are dropped.

**Stepping back is slow.** The game wasn't started with `start_game.bat`. Close the game and start it again with `start_game.bat`.

**"Sound effects" is greyed out.** Look at the reason in brackets and check it against the table in section 9.

**The game crashed.** The crash location is in `logs\game-exception.log`, and the background service's errors are in `logs\tas-service.err.txt`.
Send these two files to the author along with what you did. Double-click `start_game.bat` again to carry on; your script files are all still there.

---

## 15. Advanced

- `tas_console.bat`: a text console (pause / load / step / resume and other commands) for people who want to write their own automation.
- Address tables: `MAP.md` (Special Edition), `MAP_DX9.md` (original); animation number table: `MOTIONS.md`.
- Tools for AI: a separate repository, [DMC4TAS-MCP](https://github.com/vanzeelaskowitz-netizen/DMC4TAS-MCP), is an MCP server
  that lets AIs such as Claude and Codex drive this tool directly: testing moves in batches, checking frame by frame whether a JC is possible (`jc_points`), generating keys from an intent, recording video, and more.
  See that repository's README for how to install and register it.
