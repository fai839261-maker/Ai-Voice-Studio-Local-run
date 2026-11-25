#!/bin/bash
# Double-click this file on a Mac to build AI Voice Studio for that Mac.
cd "$(dirname "$0")"
chmod +x Build-Mac.sh
./Build-Mac.sh
status=$?
echo
read -n 1 -s -r -p "Press any key to close this window."
exit $status
