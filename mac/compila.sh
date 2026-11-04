#!/bin/bash
# Compila Ozu.app da mac/Ozu.swift, per Apple Silicon e Intel, e la firma (firma locale, senza account Apple).
# Bastano gli strumenti da riga di comando di Xcode (xcode-select --install). Da lanciare dalla cartella di Ozu:
#     mac/compila.sh
set -euo pipefail
cd "$(dirname "$0")/.."
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
for arch in arm64 x86_64; do
  swiftc -O -swift-version 5 -target "$arch-apple-macos13.0" -framework AppKit -framework WebKit \
    mac/Ozu.swift -o "$tmp/Ozu-$arch"
done
lipo -create -output "$tmp/Ozu" "$tmp/Ozu-arm64" "$tmp/Ozu-x86_64"
# La versione mostrata dal Finder è quella del programma (VERSIONE).
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $(tr -d '[:space:]' < VERSIONE)" Ozu.app/Contents/Info.plist
# Si sostituisce il file (non lo si riscrive): un'app aperta continua a girare con il suo.
mv -f "$tmp/Ozu" Ozu.app/Contents/MacOS/Ozu
codesign --force --deep --sign - Ozu.app
codesign --verify --deep --strict Ozu.app
# Dove sta il programma: lo legge la copia di Ozu.app nella cartella Applicazioni (vedi trovaProgramma).
mkdir -p "$HOME/Library/Application Support/Ozu"
pwd > "$HOME/Library/Application Support/Ozu/cartella_programma"
echo "Ozu.app compilata e firmata ($(lipo -archs Ozu.app/Contents/MacOS/Ozu))."
