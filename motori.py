"""L'elenco dei motori di traduzione e l'ordine di ripiego.

In ordine di qualità: Claude (adattamento con il contesto di tutto il film, a
pagamento), il modello locale (lo stesso adattamento, gratuito, su questo Mac),
DeepL, Google Traduttore. Il predefinito è il modello locale. Se il motore scelto non
è disponibile si passa al successivo.

Ogni motore è un modulo con configurato() e, a seconda del tipo:
- i motori che adattano (Claude, modello locale) hanno una classe Sessione con il metodo
  chiedi(blocchi, schema, attesi, avanza) e gli attributi nome, modello e costo: il
  procedimento (scheda, traduzione, revisione) è in adattamento.py;
- quelli che traducono soltanto (DeepL, Google) hanno traduci(testi, opzioni, progresso).
"""

import motore_claude
import motore_deepl
import motore_google
import motore_locale

MODULI = {"claude": motore_claude, "locale": motore_locale, "deepl": motore_deepl, "google": motore_google}
ORDINE = list(MODULI)  # dal migliore al meno affidabile
PREDEFINITO = "locale"  # gratuito
NOMI = {"claude": "Claude", "locale": "Modello locale", "deepl": "DeepL", "google": "Google Traduttore"}


def configurati() -> dict:
    return {m: MODULI[m].configurato() for m in ORDINE}


def disponibile(richiesto: str) -> str:
    """Il motore richiesto se configurato, altrimenti il migliore tra quelli che vengono dopo."""
    pronti = configurati()
    return next(m for m in ORDINE[ORDINE.index(richiesto):] if pronti[m])


def adatta(motore: str) -> bool:
    """Vero per i motori che preparano la scheda e adattano (Claude, modello locale)."""
    return hasattr(MODULI[motore], "Sessione")
