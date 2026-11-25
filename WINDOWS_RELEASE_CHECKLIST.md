# Windows release checklist — 0.4.2

1. Build on Windows 10/11 x64; check Build-Windows.log and packaged-self-check.json. Verify installer and compiled update ZIP exist and SHA-256 matches. Build on a clean PC as well as an existing project.
2. On a clean Windows PC use only the setup EXE. Verify destination selection, missing-Python preflight, no console flashes and successful launch. Check setup-preflight.log. Repeat on a PC with Python and an existing model environment. Check UAC cancellation and no WinGet scenarios.
3. Confirm an update from V0.3.2/0.3.3 retains models, voices, scripts and audio. On V0.4.2 select a trusted locally compiled update ZIP and EXE; reject corrupted hashes, older versions and source ZIPs. Try an offline update with network disconnected; do not expect new engines to download offline.
4. Verify startup environment-audit.json and marker invalidation with a deliberately broken managed test environment. Repair only that model, check that shared wheels and already downloaded weights are reused. Test cancellation and concurrent weight transfers. Measure clean setup and repeat setup timings before claiming a speedup.
5. Generate audio with Piper/Kokoro, then Chatterbox, Qwen variants and all remaining adapters. Test CPU and available CUDA configurations, exact reference transcript where required, short and long scripts, pause/retry, real WAV validation, and output audio. Verify no model claims readiness before synthesis. Some third-party engines may still require Windows dependency fixes.
6. Verify UI on laptop 1366x768 and larger screen; dark clone dialog, no heading in Voice Studio, direct preview, seek and save, separate batch assignment dialog, adjustable table columns, no visible section editor, no bottom task shelf, errors only. Test import of several TXT/DOCX files, different voices, one voice for all, automatically starting imported jobs and preserving pending jobs across restart.
7. Review model sources/license notes and upstream terms before redistributing. Confirm no model/reference data is accidentally included in installer. Sign the installer if distributing to customers; source project does not include an Authenticode key.

## 0.4.2 regression checks
- In the existing Windows installation, run a short Kokoro British voice sample without downloading the checkpoint again; confirm ONNX datatype mismatch is gone.
- Use a licensed voice-reference WAV and short Chatterbox script. Test CUDA and CPU separately. Confirm GPU failure triggers a new CPU worker, or reports the underlying CPU failure if both fail.
- Generate a long imported script in one-final-audio mode. Confirm all text is represented and a single WAV is assembled. Pause/resume between chunks.
- Change all resource sliders and restart. Check CPU/RAM/GPU meters, admission at low limits, and presets on both low- and high-end PCs. Limits are not hard OS utilization caps.
- Clear all queue records after stopping jobs. Verify exported audio, model files and clone profiles remain.
- Add a public standard VITS model with safetensors through AI Setup; verify correct adapter, install and actual synthesis. Reject unsupported model types, gated repos and custom remote code.

## New 0.4.2 on-device acceptance gates (not yet executed)
- Interrupt a large Qwen or F5 download, restore network, verify resumed bytes and completed model hash. Test GitHub redirects, low disk, insufficient storage, and retry after moving files.
- Generate with an already-installed Kokoro model while downloading Qwen, including queued audio and same-profile dependency repair contention.
- On a disposable installation with known reference audio, projects, models, partial downloads and installed Python profiles, move from C: to D: (both NTFS); verify old paths are junctions, new data uses D:, launch via existing shortcuts, generate offline, retry remaining download, then install an update. Test rollback on insufficient space and locked files. Verify uninstall behavior on a disposable system before production reliance.

## 0.4.4 Windows acceptance gates (not executed in Linux)
- On V0.4.1, extract source and run UPDATE_MY_SOFTWARE.vbs. Build with both prior build environment and fresh environment; verify errors do not launch installer.
- Cancel at trust prompt and at final setup confirmation; verify current install and data unchanged.
- Confirm existing active Studio is never force-closed by launcher. Close it and upgrade normally; verify old app data and version.
- In V0.4.4 test trusted source ZIP with manifest, compiled ZIP and EXE, reject malicious paths, old versions, and corrupt hashes; verify database backup and logs.
- Test a relocated installation and ensure junctions and existing installer directory still work. Retest V0.4.2 download recovery and background generation on Windows.

## 0.4.5 migration Windows acceptance gates (not executed in Linux)
- Build both the independent frozen helper and desktop application; verify --self-test and --self-check.
- Update V0.4.4 to V0.4.5 without uninstall. Check the single Move folder button, folder chooser, exact path, and cancellation.
- Move from C to a second fixed NTFS drive with spaces in paths, downloaded HF cache links, partial model weights, a working venv, project database, audio and saved voices. Confirm progress, physical disk usage, the two junction targets, and successful restart.
- Generate offline WAV with previously working models, resume a partial download, and install a subsequent offline update without relocating data back to C.
- Fail preflight for low space and external links. Interrupt copying and each junction switch on a disposable fixture; run recovery. Verify byte-for-byte sources restored and no unrelated destination deleted. Repeat move to a third path; exercise cleanup with read-only or locked files.
- Never test destructive failure injection on the only copy of user data.

## 0.4.6 process-release acceptance (not executed here)
- Upgrade from V0.4.5, close old instances, launch V0.4.6 and attempt a second launch; confirm only one GUI.
- On a disposable Windows fixture, confirm a transient process under the installation exits during the unlock wait and the move continues.
- Verify normal WM_CLOSE for a verified old GUI; an active worker or refusing window must not be forcibly terminated. Check the PID is shown and original files retained if it remains.
- Try launching during copy, inspect the active recovery gate, and verify the new pre-switch process check and no duplicate restart after a failed move.

## 0.4.7 update acceptance (not executed here)
- On V0.4.5 choose this source ZIP and cancel the optional build folder dialog, or extract and run UPDATE_MY_SOFTWARE.vbs. Verify build starts without that folder.
- On V0.4.7 choose a source ZIP with no prior source project available. Verify no folder picker appears and cached tools are reused on a second build.
- Create an invalid managed build venv; verify new online build uses an alternate cache and original environment remains intact.
- Install under a non-default folder with spaces, then upgrade both from inside the app and via the VBS launcher. Confirm the same registry path and unchanged models/database. Repeat after a successful Move folder operation to D: and confirm physical files are updated there, with logical junctions intact.
- Observe taskbar and desktop during launcher/build/installer. No Python, pip, ISCC, PowerShell or CMD console should appear in the normal update flow. Security and installer prompts may be visible. Verify failure logs and no automatic uninstall.

## V0.4.8 Windows-only acceptance checks
- Use a backup test installation. Move a mixed small-file/large-file corpus to a second local NTFS drive; measure actual end-to-end time. Test with SSD and HDD.
- Interrupt the helper in a VM during copy, during publication, during the first junction switch and after commit. Sign in again, verify recovery prompt and each choice. Confirm copied files are reused and corrupt checkpoints replaced. Verify registry startup entry removal and previous absolute paths after update.
- Test Windows startup when D: is disconnected, original source remains intact; never assume recovery from physical disk loss.
- Check the compact Sources button next to Download/Cancel across model cards and real remote downloads, including interruptions and low storage.

## V0.4.9 Windows-only acceptance checks
- Test fresh install and in-place upgrade on Windows 10/11 x64. Verify installer version 0.4.9.
- Move a fixture with many ordinary files and large weights to a fixed NTFS drive; confirm system Robocopy is used, no console opens, log shows expected flags and source stays intact before commit.
- Include link-based caches, a missing/unusable Robocopy simulation and an injected nonzero copy failure. Verify Python fallback, SHA-256, original-path junctions and database.
- Interrupt during Robocopy, verification and switching; test reboot and resume/restore, including a surviving Robocopy child. Verify old copies are retained until commit.
- Compare actual SSD/HDD copy time with V0.4.8; no performance claim until measured.

## V0.4.10 Windows acceptance checks
- Build on Windows, verify native truststore and certifi resources. Test real TLS connections through Windows-trusted network configuration without disabling verification.
- Verify size preview, range fallback, progress speed, cancel and resume on each configured source. Test local trusted ZIP/folder attachment, missing assets and invalid archives.
- Verify compatible CPU/CUDA wheel cache reuse across profiles and correct fallback for incompatible versions. Test real engine imports and synthesis before production use.
- Verify Copy today’s errors, compact AI Setup, and upgrade-in-place on a moved installation. Repeat Robocopy interruption recovery on NTFS.

## V0.4.11 acceptance checks (not yet executed on Windows)
- Clean setup on Windows 10/11, Python detection, Piper quick setup, optional LibTorch CPU ZIP download/extraction and settings/data migration.
- Import a reference MP3/WAV, crop a known interval, preview it, adjust gain, save transcript and style notes, reopen profile and regenerate with an authorized voice.
- Verify export files in a media editor: WAV, MP3, MP2 and MPEG Layer II audio.
- Generate two sections and two successive jobs using the same model; check logs for the warm-worker reuse and measure timing; test idle timeout, model switching, repair, update, move, memory limits and CPU fallback.
- Validate actual supported inference for each model and GPU. The Python engine cache is not a C++ conversion and does not guarantee exact emotion/style reproduction.

## V0.4.13 acceptance checks (Windows not yet executed)
- Start F5 or Qwen model setup. Generate “Hi” with an installed Piper and Kokoro model; verify valid WAV while the unrelated transfer continues.
- Repeat with a same-profile check; verify an explicit wait label and safe continuation when the repair finishes.
- Set preferred CUDA with high GPU usage; confirm CPU-only ONNX jobs are not GPU gated.
- Verify model names stay on one line, attachment is disabled with complete weights and during setup, S opens concise sources, generation needs no title dialog.
- Force a warm worker pipe failure and check fresh-process retry/error log. Export actual WAV/MP3/MPEG after generation.

## V0.11.2 on-device acceptance (not yet executed)
- Compare a checksum-verified large official weight download with ordinary single-stream download under the same network conditions. Check exact-range fallback and cancel/resume.
- In sequential mode, submit three distinct scripts and confirm FIFO. In parallel mode, submit two installed Kokoro jobs; confirm separate outputs and no concurrent mutation of their shared runtime. Test low-memory admission.
- Confirm the Offline software update panel is absent and use the external VBS upgrade launcher to update an existing relocated installation.
