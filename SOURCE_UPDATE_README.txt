SOURCE UPDATE FORMAT v1
The complete project ZIP must have AVS_SOURCE_UPDATE.json at root or under one
project folder. Required fields: format=avs-source-update-v1, app=AI Voice Studio,
version=major.minor.patch. Keep setup filename, APP_VERSION, build script and the
manifest consistent. Never install an untrusted source ZIP: building runs code.
The in-app updater rejects older releases, unexpected root files, archive
traversal, links, duplicate paths and oversized source entries. After a successful
Windows packaged-resource check, it stages the compiled setup and asks for final
hash and install confirmation. Build logs are in managed updates/source-*/.
Existing app data are not removed. Backup other critical files manually.

V0.4.7 builds automatically without a build-folder picker, using a persistent managed build cache if needed. The installed logical folder is passed explicitly to setup. Use UPDATE_MY_SOFTWARE.vbs to bootstrap from V0.4.5 or V0.4.6.

V0.4.8 adds resumable, parallel folder migration. Never apply an update during a move.

V0.4.9 adds automatic Robocopy acceleration with verified Python fallback.

V0.22.0 removes the in-app update panel. Use the extracted UPDATE_MY_SOFTWARE.vbs launcher for subsequent upgrades. Earlier compatible in-app updaters can stage this ZIP.
