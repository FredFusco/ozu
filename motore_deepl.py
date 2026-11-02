"""DeepL come motore: traduzione automatica con le battute precedenti come contesto,
senza scheda né adattamento. Serve una chiave API (anche quella gratuita, che finisce
con «:fx»)."""

import json
import re
import time
import urllib.error
import urllib.request

import chiavi
import sottotitoli as st

LINGUE = {"pt-BR": "PT", "zh": "ZH", "yue": "ZH", "no": "NB"}
LOTTO = 40  # battute per richiesta
CONTESTO = 15  # battute precedenti mandate come contesto (non tradotte né conteggiate)


def configurato() -> bool:
    return bool(chiavi.leggi("deepl"))


def _richiesta(chiave: str, testi: list, sorgente: str, contesto: str) -> list:
    host = "api-free.deepl.com" if chiave.endswith(":fx") else "api.deepl.com"
    corpo = {"text": testi, "target_lang": "IT", "tag_handling": "html", "preserve_formatting": True}
    if sorgente != "auto":
        corpo["source_lang"] = LINGUE.get(sorgente, sorgente.split("-")[0].upper())
    if contesto:
        corpo["context"] = contesto
    richiesta = urllib.request.Request(f"https://{host}/v2/translate", data=json.dumps(corpo).encode(),
                                       headers={"Authorization": f"DeepL-Auth-Key {chiave}",
                                                "Content-Type": "application/json"})
    for tentativo in range(5):
        try:
            with urllib.request.urlopen(richiesta, timeout=60) as r:
                return [t["text"] for t in json.loads(r.read())["translations"]]
        except urllib.error.HTTPError as e:
            if e.code == 403:
                raise RuntimeError("La chiave di DeepL non è valida.")
            if e.code == 456:
                raise RuntimeError("La quota di DeepL è esaurita.")
            if tentativo == 4 or e.code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f"DeepL ha risposto con un errore ({e.code}).")
        except urllib.error.URLError:
            if tentativo == 4:
                raise RuntimeError("DeepL non risponde: controlla la connessione.")
        time.sleep(2 * (tentativo + 1))


def traduci(testi: list, opzioni: dict, progresso) -> list:
    chiave = chiavi.leggi("deepl")
    sorgente = opzioni.get("lingua_sub", "auto")
    if sorgente in ("auto", "en") and st.sembra_inglese(testi):
        testi = [st.correggi_ocr(t) for t in testi]
    # Con tag_handling=html DeepL ignorerebbe gli a capo: diventano <br/> e poi tornano a capo.
    html = [t.replace("\n", "<br/>") for t in testi]
    risultati = []
    for inizio in range(0, len(testi), LOTTO):
        contesto = " ".join(testi[max(0, inizio - CONTESTO):inizio]).replace("\n", " ")
        tradotte = _richiesta(chiave, html[inizio:inizio + LOTTO], sorgente, contesto)
        risultati += [st.a_capo_automatico(st.ritocca_italiano(re.sub(r"\s*<br\s*/?>\s*", "\n", t)))
                      for t in tradotte]
        progresso(len(risultati), len(testi))
    return risultati
