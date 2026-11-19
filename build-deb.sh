#!/bin/bash
# Build cz-beam_<version>_all.deb into ./build. Needs dpkg-deb (any Debian/Ubuntu).
set -euo pipefail
cd "$(dirname "$0")"
VERSION=$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' app-builder.json | head -1)
PKG=cz-beam
grep -q "^VERSION = '$VERSION'" app/app.py || { echo "app.py VERSION differs from app-builder.json ($VERSION)"; exit 1; }
python3 -m py_compile app/app.py
root=$(mktemp -d); trap 'rm -rf "$root"' EXIT
chmod 0755 "$root"
install -D -m 0644 app/app.py             "$root/usr/share/APPLaunch/apps/$PKG/app.py"
install -D -m 0644 app/$PKG.desktop       "$root/usr/share/APPLaunch/applications/$PKG.desktop"
install -D -m 0644 app/$PKG.png           "$root/usr/share/APPLaunch/share/images/$PKG.png"
install -D -m 0644 LICENSE                "$root/usr/share/doc/$PKG/copyright"
install -d -m 0755 "$root/DEBIAN"
sed "s/@VERSION@/$VERSION/" debian/control.in > "$root/DEBIAN/control"
echo "Installed-Size: $(du -sk --exclude=DEBIAN "$root" | cut -f1)" >> "$root/DEBIAN/control"
mkdir -p build
dpkg-deb --root-owner-group -Zxz --build "$root" "build/${PKG}_${VERSION}_all.deb"
