AI VOICE STUDIO 0.22.0 - SOURCE PROJECT / LOCAL UPGRADE

CURRENT V0.4.5 / V0.4.6: use the one-time bootstrap below, or select the
new source ZIP through Settings and CANCEL the optional previous build folder
picker in the old updater. The picker was removed in V0.4.7.

1. Extract every file to a writable folder.
2. Double-click UPDATE_MY_SOFTWARE.vbs (trusted source only).
3. Approve the build. Existing tools are detected; a persistent build environment
   is reused or created automatically. Missing tools may require internet.
4. Close old Studio when asked and approve installation into the detected folder.
5. Reopen Studio and verify version 0.22.0. No uninstall or manual directory choice.

The GUI launcher hides build consoles; Windows security prompts and prerequisite
installer UI may appear. Logs remain available. Do not launch the legacy BAT files
if you want a console-free workflow. Source ZIP has no compiled EXE included.

BuildOut:
AI_Voice_Studio_Setup_0.22.0.exe
AI_Voice_Studio_Setup_0.22.0.exe.sha256.txt
AI_Voice_Studio_Update_0.22.0.zip

V0.4.7 through V0.4.13: select a trusted manifest-bearing source ZIP directly in
Settings > Install update from file; no build folder picker. Confirm trust and
installer integrity. Keep the same Windows account and do not delete AVS data or
migration junctions. Windows build and live installation require validation.

Complete or safely resolve any current folder migration before upgrading.

V0.22.0 removes the in-app offline update panel. For future releases, always use
UPDATE_MY_SOFTWARE.vbs from the extracted full source project, or a compiled EXE.
