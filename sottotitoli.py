"""Lettura e scrittura dei sottotitoli, e i vincoli che una battuta deve rispettare.

Formati: SRT, WebVTT, ASS/SSA, e le tracce di sottotitoli testuali dentro i video
(estratte con ffmpeg). Ogni parser restituisce (testi, durate, ricostruisci):
- testi: il testo di ogni battuta, con gli a capo interni;
- durate: quanti secondi resta a schermo ogni battuta (None se non si sa);
- ricostruisci(tradotti): il file completo con i testi sostituiti, tempi e struttura intatti.
"""

import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ESTENSIONI = (".srt", ".vtt", ".ass", ".ssa")
VIDEO = (".mkv", ".mp4", ".m4v", ".mov", ".avi", ".webm", ".ts")

MAX_CARATTERI_RIGA = 42
MAX_RIGHE = 2
# Velocità di lettura, in caratteri al secondo (spazi inclusi, tag e a capo esclusi). L'adattatore punta a
# 15-17; oltre questo tetto la battuta non si fa in tempo a leggere e torna al modello per essere asciugata.
MAX_CARATTERI_SECONDO = 20


# --------------------------------------------------------------------------
# Lettura
# --------------------------------------------------------------------------

def decodifica(dati: bytes) -> str:
    if dati.startswith((b"\xff\xfe", b"\xfe\xff")):
        return dati.decode("utf-16")
    for codifica in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return dati.decode(codifica)
        except UnicodeDecodeError:
            continue
    return dati.decode("utf-8", errors="replace")


TIMECODE = re.compile(r"\d+:\d+[:.,]\d+.*-->")
TEMPO = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")


def _secondi(s: str):
    m = TEMPO.search(s)
    if not m:
        return None
    h, mi, se, fr = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(se) + int(fr) / (10 ** len(fr))


def _durata(inizio, fine):
    a, b = _secondi(inizio), _secondi(fine)
    return b - a if a is not None and b is not None and b > a else None


def parse_srt_vtt(testo: str):
    testo = testo.replace("\r\n", "\n").replace("\r", "\n")
    blocchi = re.split(r"\n\s*\n", testo.strip("\n"))
    struttura, testi, durate = [], [], []
    for blocco in blocchi:
        righe = blocco.split("\n")
        idx = next((i for i, r in enumerate(righe) if TIMECODE.search(r)), None)
        if idx is None:
            struttura.append((blocco, None))  # intestazione WEBVTT, NOTE, STYLE…
            continue
        testa = "\n".join(righe[: idx + 1])
        corpo = "\n".join(righe[idx + 1:])
        if corpo.strip():
            struttura.append((testa, len(testi)))
            testi.append(corpo)
            durate.append(_durata(*righe[idx].split("-->", 1)))
        else:
            struttura.append((testa, None))

    def ricostruisci(tradotti):
        return "\n\n".join(t if i is None else t + "\n" + tradotti[i] for t, i in struttura) + "\n"

    return testi, durate, ricostruisci


def parse_ass(testo: str):
    righe = testo.replace("\r\n", "\n").split("\n")
    testi, durate, posizioni = [], [], []
    n_campi, in_eventi = 10, False
    for i, r in enumerate(righe):
        s = r.strip()
        if s.startswith("["):
            in_eventi = s.lower() == "[events]"
        if in_eventi and s.lower().startswith("format:"):
            n_campi = len(s.split(":", 1)[1].split(","))
        if in_eventi and s.startswith("Dialogue:"):
            pref, resto = r.split(":", 1)
            campi = resto.split(",", n_campi - 1)
            if len(campi) < n_campi:
                continue
            # I tag di stile iniziali ({\an8}…) restano; quelli in mezzo alla frase si perdono.
            m = re.match(r"^((?:\{[^}]*\})*)(.*)$", campi[-1])
            corpo = re.sub(r"\{[^}]*\}", "", m.group(2)).replace("\\N", "\n").replace("\\n", "\n")
            if corpo.strip():
                posizioni.append((i, pref, campi[:-1], m.group(1)))
                testi.append(corpo)
                durate.append(_durata(campi[1], campi[2]))

    def ricostruisci(tradotti):
        out = list(righe)
        for (i, pref, campi, tag), t in zip(posizioni, tradotti):
            out[i] = pref + ":" + ",".join(campi + [tag + t.replace("\n", "\\N")])
        return "\n".join(out)

    return testi, durate, ricostruisci


def parse(nome: str, testo: str):
    if nome.lower().endswith((".ass", ".ssa")) or "[Events]" in testo:
        return parse_ass(testo)
    return parse_srt_vtt(testo)


def estrai_da_video(percorso: Path) -> str:
    """La prima traccia di sottotitoli testuali del video, convertita in SRT."""
    ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    if not os.path.exists(ffmpeg):
        raise RuntimeError("Per leggere i sottotitoli dentro un video serve ffmpeg (brew install ffmpeg).")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "estratti.srt"
        r = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(percorso), "-map", "0:s:0",
                            "-c:s", "srt", str(out)], capture_output=True, text=True)
        if r.returncode or not out.exists():
            raise RuntimeError("Il video non contiene sottotitoli in formato testo (quelli a immagini, "
                               "come i PGS dei Blu-ray, non si possono tradurre).")
        return decodifica(out.read_bytes())


def leggi(percorso: Path):
    """(testi, durate, ricostruisci, estensione del file tradotto)."""
    if percorso.suffix.lower() in VIDEO:
        testo, estensione = estrai_da_video(percorso), ".srt"
    else:
        testo, estensione = decodifica(percorso.read_bytes()), percorso.suffix.lower()
    testi, durate, ricostruisci = parse(percorso.name, testo)
    if not testi:
        raise RuntimeError(f"Nessuna battuta trovata in {percorso.name}.")
    return testi, durate, ricostruisci, estensione


def nome_tradotto(percorso: Path, estensione: str) -> str:
    base = re.sub(r"[._-](en|eng|english|fr|fre|es|spa|de|ger|ja|jpn|ko|kor|zh|chi|ru|rus|pt|por)$", "",
                  percorso.stem, flags=re.I)
    return f"{base}.it{estensione}"


# --------------------------------------------------------------------------
# Vincoli dei sottotitoli
# --------------------------------------------------------------------------

TAG = re.compile(r"</?[a-zA-Z][^>]*>")


def senza_tag(s: str) -> str:
    return TAG.sub("", s)


def tag(s: str) -> list:
    return sorted(t.lower() for t in TAG.findall(s))


def ritocca_italiano(testo: str) -> str:
    """Corregge un errore tipico dei traduttori automatici: «E'» al posto di «È»."""
    return re.sub(r"(^|[\s\"«(—–-])E'(?=\s)", r"\1È", testo, flags=re.M)


# Parole che nei sottotitoli italiani non si lasciano in fondo alla riga.
NON_IN_FONDO = {"il", "lo", "la", "i", "gli", "le", "l'", "un", "uno", "una", "un'", "di", "a", "da", "in", "con",
                "su", "per", "tra", "fra", "e", "o", "ma", "che", "se", "non", "mi", "ti", "si", "ci", "vi", "ne",
                "del", "della", "dei", "delle", "al", "alla", "ai", "nel", "nella", "sul", "sulla", "dal", "dalla"}


def a_capo_automatico(testo: str) -> str:
    """Toglie gli spazi in eccesso e divide in due una battuta su una riga troppo lunga, come un
    adattatore: le righe entro il limite, meglio dopo la punteggiatura, mai dopo un articolo o
    una preposizione, e il più possibile vicino al centro."""
    testo = "\n".join(r.strip() for r in testo.strip().split("\n"))
    if "\n" in testo or len(senza_tag(testo)) <= MAX_CARATTERI_RIGA:
        return testo
    spazi = [m.start() for m in re.finditer(" ", testo)]
    if not spazi:
        return testo

    def costo(p):
        prima, dopo = senza_tag(testo[:p]), senza_tag(testo[p + 1:])
        c = abs(len(prima) - len(dopo))
        if max(len(prima), len(dopo)) > MAX_CARATTERI_RIGA:
            c += 1000
        if prima[-1:] in ".?!":
            c -= 30
        elif prima[-1:] in ",;:":
            c -= 15
        if prima.split()[-1].lower() in NON_IN_FONDO:
            c += 60
        return c

    taglio = min(spazi, key=costo)
    return testo[:taglio] + "\n" + testo[taglio + 1:]


def caratteri_al_secondo(testo: str, durata: float) -> float:
    return len(senza_tag(testo).replace("\n", "")) / durata


def problemi(originale: str, tradotto: str, durata: float = None) -> list:
    """I vincoli che una battuta tradotta non rispetta (lista vuota se va bene). durata in secondi."""
    errori = []
    if not tradotto.strip():
        errori.append("battuta vuota")
    righe = tradotto.split("\n")
    if len(righe) > MAX_RIGHE:
        errori.append(f"{len(righe)} righe (massimo {MAX_RIGHE})")
    lunghe = [len(senza_tag(r)) for r in righe if len(senza_tag(r)) > MAX_CARATTERI_RIGA]
    if lunghe:
        errori.append(f"riga di {max(lunghe)} caratteri (massimo {MAX_CARATTERI_RIGA})")
    if tag(originale) != tag(tradotto):
        errori.append(f"tag diversi dall'originale: attesi {tag(originale)}, trovati {tag(tradotto)}")
    if durata and caratteri_al_secondo(tradotto, durata) > MAX_CARATTERI_SECONDO:
        errori.append(f"troppo veloce da leggere: {caratteri_al_secondo(tradotto, durata):.1f} caratteri al secondo "
                      f"in {durata:.1f} s (massimo {MAX_CARATTERI_SECONDO}): asciugala")
    return errori


# --------------------------------------------------------------------------
# Traduzione fuori posto
# --------------------------------------------------------------------------

# Parole con la maiuscola che non sono nomi: non contano come «segni» copiati dall'originale.
_NON_NOMI = {"The", "And", "But", "This", "That", "What", "Yes", "You", "They", "There", "When", "Where", "How",
             "Why", "Who", "Not", "Now", "Then", "Well", "Come", "Look", "Listen", "Please", "Sir"}


def _segni(testo: str) -> set:
    """Numeri e nomi propri: passano dall'originale alla traduzione così come sono."""
    return set(re.findall(r"\d+|\b[A-Z][a-z]{2,}\b", senza_tag(testo))) - _NON_NOMI


def _somiglianza(originale: str, tradotto: str, rapporto: float) -> float:
    """Quanto una battuta tradotta somiglia a un originale: lunghezza (rispetto al rapporto tipico del film)
    e numeri o nomi in comune."""
    lunga = lambda t: len(senza_tag(t).replace("\n", ""))
    scarto = abs(math.log((lunga(tradotto) + 8) / (rapporto * lunga(originale) + 8)))
    a, b = _segni(originale), _segni(tradotto)
    return -scarto + 0.8 * (len(a & b) / len(a | b) if a | b else 0)


def sfasamenti(originali: list, tradotti: list, finestra: int = 3, margine: float = 0.15) -> list:
    """I tratti [(primo, ultimo), …] (indici compresi) in cui la traduzione sembra fuori posto: ogni battuta
    somiglia più all'originale di una battuta vicina che al suo. Succede quando un modello salta una
    battuta e continua con la numerazione sfasata: il testo è buono, ma compare al momento sbagliato.
    Misurato su un film: trova i due tratti sfasati di una traduzione e niente nelle traduzioni giuste."""
    n = len(originali)
    if n < 2 or len(tradotti) != n:
        return []
    lunghi = [(len(senza_tag(o)), len(senza_tag(t or ""))) for o, t in zip(originali, tradotti)]
    rapporti = sorted(t / o for o, t in lunghi if o >= 10 and t)
    rapporto = rapporti[len(rapporti) // 2] if rapporti else 1.0
    spostata = [0] * n
    for i in range(n):
        vicine = range(max(0, i - finestra), min(n, i + finestra + 1))

        def media(s):
            v = [_somiglianza(originali[j + s], tradotti[j] or "", rapporto) for j in vicine if 0 <= j + s < n]
            return sum(v) / len(v) if v else -9.0

        migliore, s = max((media(s), s) for s in (-2, -1, 1, 2))
        if migliore > media(0) + margine:
            spostata[i] = s
    for i in range(1, n):  # la stessa traduzione su due originali diversi: una battuta è di troppo
        if (tradotti[i] or "").strip() and (tradotti[i] or "").strip() == (tradotti[i - 1] or "").strip() \
                and originali[i].strip() != originali[i - 1].strip():
            spostata[i] = spostata[i] or 9
    tratti, i = [], 0
    while i < n:
        if not spostata[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and (spostata[j + 1] or (j + 2 < n and spostata[j + 2])):
            j += 1
        if j - i >= 2 or spostata[i] == 9:
            tratti.append((i, j))
        i = j + 1
    return tratti


# --------------------------------------------------------------------------
# Errori OCR tipici dei sottotitoli inglesi. Servono ai motori automatici; Claude
# li corregge da sé, capendo il contesto.
# --------------------------------------------------------------------------

CORREZIONI_OCR = [
    (r"\bl\b(?=[ ,.!?']|$)", "I"), (r"\bl'(m|ll|ve|d)\b", r"I'\1"), (r"\bls\b", "Is"), (r"\blt\b", "It"),
    (r"\blt's\b", "It's"), (r"\bln\b", "In"), (r"\blf\b", "If"), (r"\bifs\b", "it's"), (r"\bIfs\b", "It's"),
    (r"\bthafs\b", "that's"), (r"\bThafs\b", "That's"), (r"\bwhafs\b", "what's"), (r"\bWhafs\b", "What's"),
    (r"\bdon'f\b", "don't"), (r"\bcan'f\b", "can't"),
]


def sembra_inglese(testi: list) -> bool:
    parole = re.findall(r"[a-z']+", " ".join(testi[:200]).lower())
    comuni = sum(p in {"the", "you", "and", "to", "is", "what", "it's", "i'm", "don't"} for p in parole)
    return bool(parole) and comuni / len(parole) > 0.05


def correggi_ocr(testo: str) -> str:
    for schema, sostituto in CORREZIONI_OCR:
        testo = re.sub(schema, sostituto, testo, flags=re.M)
    return testo
