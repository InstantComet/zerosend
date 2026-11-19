#!/bin/bash
# Build the .deb from this checkout and install it. Run: sudo bash install.sh
set -euo pipefail
(( EUID == 0 )) || { echo 'Run with sudo'; exit 1; }
cd "$(dirname "$0")"
bash build-deb.sh
deb="$(ls -t build | grep '^cz-beam_.*_all\.deb$' | head -1)"
# apt's download user cannot read a home directory; install from a copy.
cp "build/$deb" /tmp/ && apt-get install -y "/tmp/$deb" && rm -f "/tmp/$deb"
chown -R 1000:1000 build
echo 'INSTALLED. Open "Beam" in the launcher. Uninstall: sudo apt remove cz-beam'
