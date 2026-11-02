#!/usr/bin/env python3
"""Disegna l'icona di Ozu: tutta un sigillo rosso, con 小津 e due righe di sottotitoli color carta.

Scrive icona_ozu.png (1024 px) nella cartella del programma e Ozu.app/Contents/Resources/ozu.icns.
Usa AppKit, quindi va lanciato con l'ambiente di Ozu:

    ~/.venvs/ozu/bin/python icona/genera_icona.py
"""

import subprocess
import tempfile
from pathlib import Path

from AppKit import (NSBezierPath, NSBitmapImageRep, NSColor, NSFont, NSFontAttributeName,
                    NSForegroundColorAttributeName, NSGraphicsContext, NSMakeRect, NSPNGFileType,
                    NSShadow, NSString)

CARTELLA = Path(__file__).resolve().parent.parent
CARTA, ROSSO = (0.953, 0.933, 0.894), (0.722, 0.196, 0.122)


def colore(rgb, alfa=1.0):
    return NSColor.colorWithSRGBRed_green_blue_alpha_(*rgb, alfa)


def disegna(lato: int) -> NSBitmapImageRep:
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, lato, lato, 8, 4, True, False, "NSDeviceRGBColorSpace", 0, 0)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep))
    s = lato / 1024
    # Le misure sono quelle della proposta 5 di icone_proposte.html: un quadrato di 180 unità
    # (da 10 a 190), che qui diventa il riquadro delle icone di macOS, 824 px su 1024.
    k = 824 / 180 * s

    def x(u):
        return (100 * s) + (u - 10) * k

    def y(u):  # in AppKit l'origine è in basso
        return lato - (100 * s) - (u - 10) * k

    def rettangolo(ux, uy, larghezza, altezza, raggio):
        return NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(x(ux), y(uy + altezza), larghezza * k, altezza * k), raggio * k, raggio * k)

    # Il timbro: tutto il riquadro, rosso, con l'ombra delle icone di macOS.
    ombra = NSShadow.alloc().init()
    ombra.setShadowOffset_((0, -10 * s))
    ombra.setShadowBlurRadius_(24 * s)
    ombra.setShadowColor_(NSColor.colorWithWhite_alpha_(0, .28))
    NSGraphicsContext.saveGraphicsState()
    ombra.set()
    colore(ROSSO).setFill()
    rettangolo(10, 10, 180, 180, 40).fill()
    NSGraphicsContext.restoreGraphicsState()

    # La cornice interna del sigillo.
    colore(CARTA).setStroke()
    cornice = rettangolo(22, 22, 156, 156, 20)
    cornice.setLineWidth_(4.5 * k)
    cornice.stroke()

    # 小津 in verticale; la y della proposta è la linea di base dei caratteri.
    font = NSFont.fontWithName_size_("HiraMinProN-W6", 50 * k) or NSFont.boldSystemFontOfSize_(50 * k)
    attributi = {NSFontAttributeName: font, NSForegroundColorAttributeName: colore(CARTA)}
    for carattere, base in (("小", 88), ("津", 138)):
        testo = NSString.stringWithString_(carattere)
        larghezza = testo.sizeWithAttributes_(attributi).width
        testo.drawAtPoint_withAttributes_((x(100) - larghezza / 2, y(base) + font.descender()), attributi)

    # Due righe di sottotitoli.
    colore(CARTA).setFill()
    for ux, uy, larghezza in ((54, 152, 92), (70, 161.5, 60)):
        rettangolo(ux, uy, larghezza, 5, 2.5).fill()
    NSGraphicsContext.restoreGraphicsState()
    return rep


def salva(rep, percorso: Path) -> None:
    rep.representationUsingType_properties_(NSPNGFileType, {}).writeToFile_atomically_(str(percorso), True)


def main() -> None:
    salva(disegna(1024), CARTELLA / "icona_ozu.png")
    with tempfile.TemporaryDirectory() as tmp:
        cartella = Path(tmp) / "ozu.iconset"
        cartella.mkdir()
        for lato in (16, 32, 128, 256, 512):
            salva(disegna(lato), cartella / f"icon_{lato}x{lato}.png")
            salva(disegna(lato * 2), cartella / f"icon_{lato}x{lato}@2x.png")
        risorse = CARTELLA / "Ozu.app" / "Contents" / "Resources"
        risorse.mkdir(parents=True, exist_ok=True)
        subprocess.run(["iconutil", "-c", "icns", str(cartella), "-o", str(risorse / "ozu.icns")], check=True)
    print("Scritte icona_ozu.png e Ozu.app/Contents/Resources/ozu.icns")


if __name__ == "__main__":
    main()
