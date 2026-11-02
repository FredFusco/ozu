"""Libera spazio: file temporanei e di cache creati da Ozu.

Ogni voce dice quanto occupa e se si può cancellare senza pensarci. Non tocca mai i
sottotitoli, le traduzioni, le schede dei film né le sessioni: solo cose che Ozu ha
creato o scaricato.
"""

import shutil
from pathlib import Path

DATI = Path.home() / "Library" / "Application Support" / "Ozu"
PIP_CACHE = Path.home() / "Library" / "Caches" / "pip"


def _dimensione(percorso: Path) -> int:
    if not percorso.exists():
        return 0
    if percorso.is_file():
        return percorso.stat().st_size
    totale = 0
    for f in percorso.rglob("*"):
        try:
            if f.is_file() and not f.is_symlink():
                totale += f.stat().st_size
        except OSError:
            pass
    return totale


def voci() -> list:
    """Cosa si può cancellare, con la dimensione in byte."""
    v = [
        {"chiave": "parziali", "nome": "Traduzioni interrotte",
         "spiega": "Le battute già tradotte di elaborazioni fermate a metà, per riprenderle senza pagarle "
                   "di nuovo. Cancellandole, una traduzione interrotta ricomincia da capo.",
         "byte": _dimensione(DATI / "parziali"), "consigliata": False},
        {"chiave": "versioni", "nome": "Versioni precedenti di Ozu",
         "spiega": "Copie dei file sostituiti dagli aggiornamenti, per poter tornare indietro.",
         "byte": _dimensione(DATI / "versioni_precedenti"), "consigliata": True},
        {"chiave": "registri", "nome": "Registro",
         "spiega": "Il diario tecnico delle traduzioni, utile solo per capire un errore.",
         "byte": _dimensione(DATI / "ozu.log"), "consigliata": True},
        {"chiave": "pip", "nome": "File scaricati per installare le librerie",
         "spiega": "Copie dei pacchetti Python scaricati durante le installazioni. Non servono più.",
         "byte": _dimensione(PIP_CACHE), "consigliata": True},
    ]
    return [x for x in v if x["byte"] > 0]


def cancella(chiavi: list) -> int:
    """Cancella le voci scelte e restituisce i byte liberati."""
    liberati = 0
    for chiave, cartella in (("parziali", DATI / "parziali"), ("versioni", DATI / "versioni_precedenti"),
                             ("pip", PIP_CACHE)):
        if chiave in chiavi and cartella.exists():
            liberati += _dimensione(cartella)
            shutil.rmtree(cartella, ignore_errors=True)
    if "registri" in chiavi and (DATI / "ozu.log").exists():
        liberati += (DATI / "ozu.log").stat().st_size
        (DATI / "ozu.log").open("w").close()  # l'app ci sta scrivendo: si svuota, non si cancella
    return liberati
