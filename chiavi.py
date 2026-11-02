"""Le chiavi API dei motori a pagamento o esterni, e la cartella dei dati di Ozu.

Le chiavi si salvano in ~/Library/Application Support/Ozu/chiavi.json, leggibile solo
dal tuo utente. Le variabili d'ambiente ANTHROPIC_API_KEY e DEEPL_API_KEY, se presenti,
hanno la precedenza.
"""

import json
import os
from pathlib import Path

DATI = Path.home() / "Library" / "Application Support" / "Ozu"
CHIAVI = DATI / "chiavi.json"
VARIABILI = {"anthropic": "ANTHROPIC_API_KEY", "deepl": "DEEPL_API_KEY"}


def _salvate() -> dict:
    try:
        return json.loads(CHIAVI.read_text())
    except (OSError, ValueError):
        return {}


def leggi(servizio: str):
    return os.environ.get(VARIABILI[servizio]) or _salvate().get(servizio) or None


def salva(servizio: str, chiave: str) -> None:
    chiave = chiave.strip()
    if servizio == "anthropic" and chiave and not chiave.startswith("sk-ant-"):
        raise RuntimeError("La chiave di Anthropic inizia con sk-ant-.")
    tutte = _salvate()
    if chiave:
        tutte[servizio] = chiave
    else:
        tutte.pop(servizio, None)
    DATI.mkdir(parents=True, exist_ok=True)
    # Si scrive accanto e poi si sostituisce: un'interruzione non lascia mai un file a metà.
    provvisorio = CHIAVI.with_suffix(".tmp")
    fd = os.open(provvisorio, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(tutte, f)
    os.replace(provvisorio, CHIAVI)
