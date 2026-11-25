# AI Voice Studio for Mac

Works on **Apple Silicon (M1, M2, M3, M4)** and on **Intel MacBooks (i5, i7, i9)**, macOS 12 or newer.
You build the app on your own Mac; the build makes the right version for that Mac's chip.

## What works on which Mac (please read)
| Mac | What you get |
|---|---|
| **M1, M2, M3, M4** (any memory) | Everything: Piper, Kokoro, voice cloning (Chatterbox, Chatterbox Turbo, F5-TTS, Qwen3-TTS, XTTS-v2 ...), the Word checker. The Apple graphics (Metal) speed up Chatterbox, Chatterbox Turbo and F5-TTS. |
| **Intel i5 / i7 / i9** | **Piper, Kokoro and the Word checker.** The cloning and Qwen models can be downloaded but cannot run: PyTorch no longer publishes Intel-Mac builds that these voice engines need. For cloning use an Apple Silicon Mac or a Windows PC. |

## Build the app (step by step)
1. Unzip the project and open the folder.
2. You need **Python 3.10 or newer** only for the build. If you do not have it: open https://www.python.org/downloads/macos/ , download the Python 3.11 installer (the `.pkg`), open it and press Continue until it is done.
3. **Right-click `BUILD_MAC.command` and choose Open**, then Open again (macOS asks once because the file came from a download).
4. A Terminal window opens. Wait (about 5 to 15 minutes the first time). It installs the build tools, builds the app, signs it on your Mac, tests it and makes a DMG.
5. When it says **Done**, open the folder `BuildOut`, open `AI_Voice_Studio_..._arm64.dmg` (M-series) or `..._x86_64.dmg` (Intel) and drag **AI Voice Studio** to **Applications**.
6. An app you built yourself opens normally. If you copy the DMG to another Mac, that Mac may say "unidentified developer": right-click the app, choose Open, then Open (or run `xattr -dr com.apple.quarantine "/Applications/AI Voice Studio.app"` in Terminal).

## First start
- Open **Models** and download a voice model. The first download also installs **Python 3.11** for the engines (Homebrew if you have it, otherwise the official installer, which asks for your Mac password) and may open a macOS window for the **Apple developer tools**: click Install and wait.
- **Settings > Performance > Optimize for this PC** sets everything for your Mac. The Models page and the Studio list mark the models that suit your Mac with a star.
- **Run on** offers: Automatic · best for my Mac, **Apple GPU (Metal)**, Processor only. If the Apple graphics fail for any part, that part is redone on the processor at once.

## Tips by Mac
- **8 GB (M1 base):** use Gentle load strength, close other apps while a voice-over runs. Chatterbox is too big for the Apple graphics here (it uses the processor); Chatterbox Turbo and F5-TTS fit.
- **16 GB or more:** Chatterbox also runs on the Apple graphics.
- Keep the Mac plugged in and on a hard, flat surface for long voice-overs.

## How it differs from the Windows version
- No administrator start (a Mac does not need it). The owner key and owner login work the same.
- The CPU limit is enforced by the software (it pauses the helpers briefly); Windows' own hard cap does not exist on a Mac. The graphics limit does not apply to Apple graphics.
- Everything ends when you close the window (Cmd+Q too).
- **Move folder** is Windows only. **Backup for others / Complete backup** still make the data backup but their install helper files are Windows scripts; on a Mac use the owner backup and restore.
- Your data lives in `~/Library/Application Support/AVS`. The microphone asks for permission the first time you press Record.

## If something goes wrong
- *"No Python 3.10 or newer"* while building: install Python from python.org (step 2).
- *The build says it runs in Intel mode (Rosetta)*: open Terminal normally, not "Open using Rosetta", and run the build again.
- *A model will not install on an Intel Mac*: expected (see the table).
- *Anything else*: Settings > Logs (owner mode) has the text; send it with a screenshot.

## Status of this version (honest)
The Mac logic, the app recipe (checked with a Linux stand-in build and its own self-test) and the scripts (syntax) were tested in a Linux sandbox. **It has not been run on a real Mac**: the build, the signing, the DMG, Python installation, the engine installs and Metal speed are unverified.
