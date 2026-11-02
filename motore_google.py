"""Google Traduttore come motore: gratuito, senza chiavi, ma traduce riga per riga ed è il
meno affidabile. Serve come ultimo ripiego."""

import json
import re
import time
import urllib.parse
import urllib.request

import sottotitoli as st

SEPARATORE = "\n|||\n"
LOTTO = 4000  # caratteri per richiesta


def configurato() -> bool:
    return True


def _richiesta(testo: str, sorgente: str) -> str:
    url = "https://translate.googleapis.com/translate_a/single?" + urllib.parse.urlencode(
        {"client": "gtx", "sl": sorgente, "tl": "it", "dt": "t"})
    richiesta = urllib.request.Request(url, data=urllib.parse.urlencode({"q": testo}).encode(),
                                       headers={"User-Agent": "Mozilla/5.0"})
    for tentativo in range(5):
        try:
            with urllib.request.urlopen(richiesta, timeout=30) as r:
                return "".join(s[0] for s in json.loads(r.read().decode())[0] if s and s[0])
        except Exception:
            if tentativo == 4:
                raise RuntimeError("Google Traduttore non risponde: controlla la connessione.")
            time.sleep(2 * (tentativo + 1))


def traduci(testi: list, opzioni: dict, progresso) -> list:
    sorgente = opzioni.get("lingua_sub", "auto")
    if sorgente in ("auto", "en") and st.sembra_inglese(testi):
        testi = [st.correggi_ocr(t) for t in testi]
    lotti, lotto, lunghezza = [], [], 0
    for i, t in enumerate(testi):
        if lotto and lunghezza + len(t) > LOTTO:
            lotti.append(lotto)
            lotto, lunghezza = [], 0
        lotto.append(i)
        lunghezza += len(t) + len(SEPARATORE)
    lotti += [lotto] if lotto else []

    risultati, fatti = [None] * len(testi), 0
    for lotto in lotti:
        unito = _richiesta(SEPARATORE.join(testi[i] for i in lotto), sorgente)
        parti = [p.strip("\n ") for p in re.split(r"\s*\|\|\|\s*", unito)]
        if len(parti) != len(lotto):
            # Il separatore è stato alterato: una battuta alla volta.
            parti = [_richiesta(testi[i], sorgente).strip() for i in lotto]
        for i, p in zip(lotto, parti):
            if p and testi[i][:1].isupper() and p[:1].islower():  # il lotto a volte toglie la maiuscola
                p = p[0].upper() + p[1:]
            risultati[i] = st.a_capo_automatico(st.ritocca_italiano(p))
        fatti += len(lotto)
        progresso(fatti, len(testi))
    return risultati
