# AI Voice Studio 0.23.1 (COMPLETE project for Windows AND Mac, includes everything from 0.12.0 to 0.23.1)

## Get the Setup file (step by step)
1. Unzip this ZIP. Open the folder.
2. Double-click BUILD_INSTALLER.bat (as before). When it finishes, BuildOut\AI_Voice_Studio_Setup_0.23.1.exe is your installer.
3. Run the Setup file. Your voices, models and settings are kept.

## Or update the installed software without a new Setup
Settings > Updates > Install update file > ZIP file > choose this ZIP. The window stays alive while it is checked, then the software restarts by itself.

## New in 0.23.1: closing is never blocked by background work, one switch for automatic samples (Windows)
- **Voice samples on/off.** The Library page has an "Auto-prepare samples" switch and a "Prepare now" button; Settings > Performance shows the same switch. Off (the default) means samples are made only when you ask.
- **Closing the software** asks "Something is still in progress" only for real work (a voice-over being made, a model download or setup, a backup). Quiet optional background work (samples, the preloaded voice engine, a sample preview) never blocks closing and is simply stopped.
- The "Failed - see logs" on Check, and "Python 3.11 - name 'sys' is not defined" in the software check, came from the missing-import mistake fixed in 0.23.0. Versions 0.20.0 to 0.22.0 have it; install this update to remove it.

## New in 0.23.0: Python and pip on a fresh Windows PC, GitHub limit fixed (Windows)
- **Fixed the crash "name 'sys' is not defined"** when preparing Python (and two more missing-import mistakes found by a full scan: one in main.py, one in the clone sample window).
- **Python without WinGet.** If Python 3.11 is missing, the software asks first ("Python is needed - install it now?"), then downloads the official installer from python.org, checks its Python Software Foundation signature, and installs it
  only for this software (no administrator password) in its own folder, with pip, which is then updated. WinGet is only a second choice. The question appears a few seconds after start when Python is missing, and when you press a model's setup button.
- **Visual C++ runtime** (needed by some voice engines) is also downloaded straight from Microsoft, signature checked, when WinGet is missing. Windows asks for permission once.
- **Check for updates no longer fails with "HTTP Error 403: rate limit exceeded".** If GitHub's program interface refuses, the release is read from GitHub's normal web pages (no limit), with the same checks.
- Still needing WinGet (or a manual install): Git and the Microsoft C++ build tools, which only a few engines use. English voices work without them.

## New in 0.22.0: online updates for everyone, safer updates (Windows)
- **Settings > Updates > Check for updates** looks at github.com/fai839261-maker/Ai-Voice-Studio-Local-run (built in, nothing to type). It says "Update available: version X" or "Already latest update installed." Anyone can use it, no owner key.
- **Installing an update** stops everything that is running (finished parts are kept), installs and restarts by itself. Nothing is installed until you press the button.
- **Automatic repair.** If a new version does not start properly twice in a row (or does not stay up for two minutes), the software goes back to the version before it by itself and tells you. The newest 3 older versions are kept on the PC; older ones are removed.
- **No owner login.** Everything is shown to everyone. The Owner login button, the Owner badge and the owner controls are gone.
- **Side panel:** Sources is gone. Logs moved to the last tab of Settings.
- **Voice samples** are made only when you ask (Settings > Performance > Prepare voice samples now). Closing the software while something is running asks you: stop and close, or keep working. A stuck "voice sample is being prepared" message can no longer happen.
- A downloaded update is checked against the SHA-256 that GitHub publishes for the file.

## New in 0.21.0: Channels rebuilt (Windows)
- **Channel logo.** Choose a picture when you create a channel, or later with Change logo / Remove logo. It shows on the channel card and in the Active channel list.
- **Channel details are short now.** No name / category / description boxes (the name is asked once, when you create the channel; Rename is a button). The master prompt box is gone from the dashboard (you have the Prompts tab).
- **Competitors.** Name, link, or both - nothing is required. Add, Add many (paste a list), Edit, Remove, Open link, Copy selected, Copy all links, Copy all.
- **Countries.** Add the countries you work for. Only then do country chips, a Country column and a Country box appear. Scripts can be sorted by country.
- **Sections inside a script.** Hidden until you press Sections > Add section. The spoken text is the sections joined in order. Sections > Join into one text puts it back.
- **Search in scripts.** Type one or more words (commas keep a phrase together). It shows how many scripts have each word; click a result to open it with the words highlighted.
- **Voice Overs tab.** Send scripts from the Scripts tab (Send to Voice Overs). Only those scripts are listed, each with its own voice (or one voice for all), then press Generate VO. The channel's voices moved here too (pick a model and voice, Set as main / Add as alternative).
- **Copy everywhere.** Copy script, Copy selected, Copy all scripts / prompts / competitor links (Copy menu on the Channel Manager), right-click menus, Ctrl+C on a list.
- **Passcodes (3).** Passcode button in Channels: 1 the whole Channels section, 2 only the channels you tick (tick none = nothing locked), 3 only the Prompts tab. Unlocked until you press Lock now (Ctrl+L) or close the software. Forgot a passcode: Forgot passcode? asks for your owner key.
- **Duplicate removed from Scripts** (Duplicate channel is still there).
- **Keyboard.** Press F1 to see all shortcuts: Ctrl+1..0 pages, Delete, Enter, Ctrl+N, Ctrl+S, Ctrl+F, Ctrl+Enter, F5, Ctrl+L, Ctrl+C on lists. Delete and Enter never act while you type in a text box.
- Nothing from 0.20.0 is removed. The Mac files are unchanged.

## New in 0.20.0: the Mac edition (see README_MAC.md)
- Runs on Apple Silicon (M1 to M4) and Intel MacBooks (i5/i7/i9), macOS 12 or newer. Build it on the Mac with BUILD_MAC.command (right-click > Open): it makes the app and a DMG for that Mac's chip.
- Apple Silicon: the Apple graphics (Metal) run Chatterbox, Chatterbox Turbo and F5-TTS (Automatic); Apple GPU under Run on also tries Qwen3-TTS and XTTS-v2. A failing Metal part is redone on the processor at once.
- Intel MacBooks: Piper, Kokoro and the Word checker work. The PyTorch engines (cloning, Qwen ...) can be downloaded but cannot run, because PyTorch no longer publishes Intel-Mac builds they need.
- Mac details: data in ~/Library/Application Support/AVS; Python 3.11 installed for the engines (Homebrew or the official signed installer); Apple developer tools requested when needed; the Mac system font;
  single-instance lock; microphone permission text; performance cores counted for threads; the software's own CPU limiter (no Windows cap); everything ends at close (Cmd+Q too).
- The Windows version works as before. Move folder and the backup install helpers stay Windows-only.

## New in 0.19.2 / 0.19.1 / 0.19.0
- **Word checker fixed.** Its engine (faster-whisper) imports "requests" without listing it, so the install ended with "No module named 'requests'". "requests" is now installed with it,
  and any other plainly missing module is installed automatically and the check repeated (up to three times). Models > Word checker > Download again (or Settings > Check everything).
- **Every model can be downloaded on every PC**, including Fish Audio S2 Pro. Fish S2 Pro downloads, but it needs Linux or WSL and about 24 GB of graphics memory to make audio, so on this PC
  it is marked "Download only" and gives a plain message if used (copy it to a PC that can run it). Sizes are now looked up for every model.
- **Recommended for this PC.** Every model card says how it will run on this PC (Fast / Good speed / Slow / Very slow, or Download only) and the best fast and good models carry a star.
  The Studio model list shows the same (★ or "slow here"). The line under Run on adds the speed word and, once a model has been timed on this PC, the real speed and an estimate for the script.
  It is a rough guide from the model's size and your hardware, not a measurement.
- **Why Qwen3-TTS 0.6B is slow on a Quadro P1000:** it is a large step-by-step (autoregressive) model and the card is weak. The software cannot change that; choose a model marked ★ (F5-TTS, Chatterbox Turbo,
  Piper, Kokoro on this PC). A new safety stops a part that never ends from running for minutes.
- **Settings > Performance > "Save and apply to this software"** at the end of the tab applies the limits, load strength, thread count and engines to the running software at once.
- **Owner key (0.19.2): both owner keys work.** The software now trusts a LIST of owner keys: your original key (fingerprint c94b-fe75-7642-7ecb) and the key made in 0.19.0 (7b1e-5b09-e952-a9d7).
  Why a key was refused: an older installed UPDATE (for example the 0.19.0 one) is loaded in front of the program, and it carried only one key. 0.19.2 fixes both sides:
  (1) the key list, (2) the start-up helper now puts aside an installed update that is OLDER than the program (this part needs the new Setup).
  If a key is still refused, the message now says which version and which key file this copy uses and which fingerprints it accepts. **Install this ZIP with Settings > Updates > Install update file first**: it replaces any old update.

## New in 0.18.0 (limits that really hold, and opening as administrator without a question)
- **The CPU / memory / graphics limits now count for the WHOLE software** (the window plus every Python helper it started). Before, the limit only chose how many
  threads a voice engine may start, so the window and the helpers could add up to far more than the limit (45% + 46% = 91% with a 75% limit).
  - Processor: Windows itself keeps the software and all its helpers together at or below the limit (a hard cap on the whole job). If Windows cannot do that,
    the software measures and pauses the helpers for a fraction of every second. 100% of the limit means the whole PC as Task Manager shows it.
  - Memory: above the limit, idle voice engines are unloaded.
  - Graphics card: when the card is busier than the limit, the graphics helper is paused for a fraction of every second (approximate).
  - Helpers left behind by an older run are found and ended at start-up.
  - A change of a limit in Settings counts within a few seconds. "Full power" (100%) never limits.
- **Opens with administrator rights without a question at every start.** The first start asks once (the normal Windows window). After that the software saves a
  Windows task and every later start opens as administrator with no question. "--no-admin" in the shortcut starts it normally. A standard user is never asked.
  Windows does not allow dragging files from Explorer into an administrator window: use the Select file buttons. This needs the NEW Setup (rebuild); the start-up file is part of it.
- If the window and the helper together still look too busy, send the Logs text: the limiter writes a line when it holds the software back.

## New in 0.17.0 (graphics card + processor share the work)
- **Why only RAM was used before:** the software sent EVERY model to the processor on an older card like your Quadro P1000, because that card is slow at HALF precision.
  But the card is fast at FULL precision, which is what these models can use. That rule was wrong for you and is replaced.
- **New hardware plan per model** (Settings > Performance > This PC shows it for your PC):
  - older-generation NVIDIA card: runs the model at FULL precision on the card when the model fits its memory (Turbo, F5, XTTS-v2, Qwen 0.6B fit 4 GB by estimate);
  - newer card: half precision when that makes the model fit, otherwise full precision;
  - a model too big for the card (for example Chatterbox or Qwen 1.7B on 4 GB) uses the processor, as before.
- **Both together:** when the card fits the model and the PC has at least 4 cores, 16 GB memory and free memory, the card and the processor each take parts of the
  same voice-over (whichever is free takes the next part). The card helper uses 2 processor threads, the processor helper the rest. The parts are joined in order.
- **Studio > Speed > Run on** now offers: Automatic · best for my PC, Graphics card + processor together, Graphics card only, Processor only
  (the graphics choices appear only when an NVIDIA card exists). The line under it explains what this PC will do for the chosen model.
- If the card runs out of memory the section is redone on the processor at once and the card is not tried again for that voice-over.
- Not measured on a real PC: the memory estimates per model are estimates, and speed on your Quadro P1000 has not been timed. Compare the "Made in" times in the Queue.
- No third-party GPU library was added (none could be tested here). Cards that are not NVIDIA (AMD, Intel) still use the processor.

## New in 0.16.0
- **Update no longer freezes the window.** The check runs in the background with a moving bar; the restart is automatic.
- **Nothing keeps running after you close the software.** The loaded voice engine and every helper process end at once (also when the program crashes
  or is ended in Task Manager). RAM, CPU and graphics card are free again immediately. Installers and the new copy started by a restart are not touched.
- **One clone for every model.** The selected cloned voice stays selected when you change the model (Chatterbox, F5, Qwen ...).
- **Clone from a long video.** The sound picture loads in the background and can be zoomed: mouse wheel = zoom, Shift + wheel or right-drag = move,
  buttons Zoom in / out / Show all / Zoom to my part, a time ruler, a whole-file strip (click to move), quiet gaps shaded.
  Drag over the picture to choose the part, drag the two edges, or use "Set start here" / "Set end here" (keys S and E, Space = play / pause).
  A click only moves the play position; your chosen part stays. Taking the sound out of a video no longer freezes the window.
- **Words spoken are added for you** (two default lines, editable). Nobody is asked for text any more. Real words still clone better with F5-type models.
- **New voices get a new name** (My Voice 001, 002 ...) that no other voice uses. The name box is editable, and the "Aa" button on a voice card renames it later.
- **Channels prompts:** the word limit is gone. Every prompt box shows only "N words | N characters" and can hold any length.

## Earlier changes (0.15.0)

## Install (step by step)
1. Open AI Voice Studio.
2. Settings > Updates > Install update file > ZIP file > choose AI_Voice_Studio_UPDATE_0_15_0.zip.
3. The software checks the file and RESTARTS BY ITSELF (no question). Work in progress is stopped; finished parts are kept.
4. The version at the bottom left shows 0.15.0.

## Why generation was so slow (Quadro P1000, 4 GB)
Your card is an older NVIDIA card (Pascal). These cards are very slow at half precision (fp16/bf16), which the voice engines
need to fit in 4 GB. The processor is usually faster. (Found from your screenshots; not measured on your PC.)
- Run on = Automatic now decides PER MODEL: a Pascal-or-older card, or a model that does not fit the card's memory, uses the processor;
  newer cards (Turing and later) are used for models that fit. "Graphics card" in Run on forces the card.
- The engine itself also skips a half-precision-slow card when Automatic is on (Qwen, Chatterbox).
- Processor threads now follow the real cores (6), not the 12 hyper-threads.
- Part size is chosen by the software for the PC and model (about 220 letters on the processor, 320 on a good card).

## Voice Studio
- Mode has 3 options only: Automatic (best for my PC), Fast (small parts), Natural (paragraph groups). Old saved modes map to these.
- After Generate the text moves to the Queue and the Studio is empty and free for the next text. A line under the button shows the Queue.
- "Check that every word was spoken" tick box: OFF by default and switched off again after every Generate.

## Queue
Columns: Project, Model · Voice, Mode, Words, Audio, Progress (bar + parts), Now (plain sentence, e.g. "Part 4 of 10 · 6 min 40 s so far"),
Made in, Created. Taller rows, larger text.

## Settings > Performance
- This PC: processor, cores, memory, Windows 10/11, graphics card (name, memory, compute level, driver), free disk, a plain explanation,
  which models use the card or the processor, and [Optimize for this PC] which sets everything and lists what it set.
- PC protection (now at the top): untick "Protect my PC" to switch it off (never waits). Slider 10 s - 5 min.

## Check words (new page, CREATE menu)
Pick an audio file and paste/load the script, press the button: the audio is turned back into text (Whisper, on the processor) and compared
word by word. Lists words NOT spoken, words spoken differently and extra words, with accuracy. Runs only when you press the button.
First time: Models > Word checker > Download (small; no PyTorch needed). The Studio tick box does the same after a voice-over
and saves a .wordcheck.txt report next to the audio and a note in the Queue.

## Library
5 to 7 voices per row (cards stretch to fill the row).

## Clones
MP4, MOV, MKV, AVI, WEBM, M4V, WMV and audio files. The sound is taken out of a video (up to 8 GB) and a "Choose the part to use" window opens:
sound picture, drag to choose the seconds, drag the edges, Play selection. The original file is never changed.

## Channels
Prompt templates no longer mention word, character or time limits. Prompts you saved earlier keep their old text; edit them if needed.

## Hidden until the owner key is added
Logs, Sources, Channels and the technical settings (Python runtime, Processing, Software check) are shown only in owner mode.

## Backup
- Asks which AI models to attach (list with sizes; Attach all / none). Only the chosen models (plus the shared wheel cache) go in.
- On the other PC the software finds the attached models, checks them and prepares their engines by itself (internet needed for the engines' packages).
- "Backup for others" and "Complete backup" carry the installed update. Every backup is noted in backup_records.json in the main backups folder.

## Also fixed
Batch import no longer lists the Word checker as a voice model. Library cards show language and gender only (the model is in the line above).

## Not for building
main.py, Build-Windows.ps1, requirements-gui.txt and the installer file in this ZIP are placeholders. Keep using your own full project to build an installer.
