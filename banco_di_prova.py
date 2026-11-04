#!/usr/bin/env python3
"""Banco di prova: dice se un modello nuovo o aggiornato traduce meglio o peggio di prima.

Prima di adottare un modello (un aggiornamento di Gemma o di Qwen in Ollama, o un modello nuovo),
lo si fa lavorare su film già tradotti e corretti a mano, e si confronta con le prove precedenti:

    ~/.venvs/ozu/bin/python banco_di_prova.py aggiungi NOME ORIGINALE CORRETTA [--prima FILE]
        [--scheda FILE] [--da en] [--audio fr] [--note "…"]
    ~/.venvs/ozu/bin/python banco_di_prova.py prova [NOME…] [--modello gemma4:26b]
        [--revisore qwen3.8:27b-dflash2-nvfp4 | --senza-rilettura]
    ~/.venvs/ozu/bin/python banco_di_prova.py storia [NOME…]

Un caso è un film con:
- ORIGINALE: i sottotitoli di partenza (per esempio inglesi);
- CORRETTA: la traduzione italiana corretta a mano, il riferimento;
- --prima (facoltativo): la traduzione di Ozu prima delle correzioni. Le parole che hai tolto
  correggendo diventano «errori noti»: il banco controlla che il modello non le rifaccia;
- --scheda (facoltativo): la scheda del film, così ogni prova parte dalla stessa e si confrontano
  solo i modelli (senza, il modello la prepara ogni volta).

Per ogni prova misura: tempi di traduzione e di rilettura, somiglianza al riferimento (chrF, da 0
a 100: più alto è meglio), errori noti rifatti, parole sospette (straniere o sconosciute al
correttore), tratti fuori posto (nella traduzione grezza e dopo il controllo), punti da controllare
(lunghezza, velocità di lettura). Poi la confronta con la prova precedente dello stesso caso.

La somiglianza al riferimento va letta con giudizio: il riferimento nasce da una traduzione di Ozu
corretta a mano, quindi un modello diverso che scrive bene ma con altre parole ne esce un po'
penalizzato. Gli errori noti, le parole sospette e i tratti fuori posto invece non dipendono dallo stile.

I casi e la storia restano in ~/Library/Application Support/Ozu/banco_di_prova/ (mai su GitHub:
contengono i percorsi e i sottotitoli dei film); lì ci sono anche le traduzioni di ogni prova.
"""

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

import adattamento
import chiavi
import motore_locale
import revisione
import sottotitoli as st

CARTELLA = chiavi.DATI / "banco_di_prova"
CASI = CARTELLA / "casi.json"
STORIA = CARTELLA / "storia.jsonl"
USCITE = CARTELLA / "uscite"
# Le misure, con il verso giusto: True se più alto è meglio.
MISURE = {"chrf": ("somiglianza al riferimento (chrF)", True), "errori_noti": ("errori noti rifatti", False),
          "sospette": ("battute con parole sospette", False), "fuori_posto_grezza": ("tratti fuori posto, grezza", False),
          "fuori_posto": ("tratti fuori posto, finale", False), "da_controllare": ("punti da controllare", False),
          "secondi_traduzione": ("secondi di traduzione", False), "secondi_rilettura": ("secondi di rilettura", False),
          "correzioni_rilettura": ("correzioni della rilettura", None)}


# --------------------------------------------------------------------------
# Misure
# --------------------------------------------------------------------------

def _ngrammi(testo: str, n: int) -> Counter:
    testo = " ".join(st.senza_tag(testo).split())
    return Counter(testo[i:i + n] for i in range(len(testo) - n + 1))


def chrf(ipotesi: list, riferimenti: list, ordine: int = 6, beta: float = 2.0) -> float:
    """chrF di tutto il film (0-100): quanto i caratteri della traduzione somigliano a quelli del riferimento,
    a gruppi da 1 a `ordine` caratteri, sommando su tutte le battute. È la misura usata nelle gare di traduzione
    automatica quando conta la forma delle parole; beta=2 pesa più la completezza della precisione."""
    precisione, completezza = [], []
    for n in range(1, ordine + 1):
        comuni = proposti = attesi = 0
        for h, r in zip(ipotesi, riferimenti):
            a, b = _ngrammi(h or "", n), _ngrammi(r or "", n)
            comuni += sum((a & b).values())
            proposti += sum(a.values())
            attesi += sum(b.values())
        if proposti and attesi:
            precisione.append(comuni / proposti)
            completezza.append(comuni / attesi)
    if not precisione:
        return 0.0
    p, c = sum(precisione) / len(precisione), sum(completezza) / len(completezza)
    return 0.0 if p + c == 0 else 100 * (1 + beta ** 2) * p * c / (beta ** 2 * p + c)


def _parole(testo: str) -> set:
    return set(re.findall(r"[\w']+", st.senza_tag(testo or "").lower()))


def errori_noti(testi: list, prima: list, corretta: list) -> dict:
    """{indice: parole sbagliate che l'utente ha tolto correggendo la battuta} («dozens», «de»). Contano solo
    le parole sospette (straniere o sconosciute al correttore): una parola italiana tolta può essere una scelta
    di stile («villeggiatura» → «vacanza») o un refuso di chi corregge, e non è un errore da cercare."""
    sospette = revisione.sospette(testi, prima)
    noti = {}
    for i, (vecchia, giusta) in enumerate(zip(prima, corretta)):
        tolte = (_parole(vecchia) - _parole(giusta)) & {p.lower() for p in sospette.get(i, [])}
        if tolte:
            noti[i] = tolte
    return noti


def rifatti(noti: dict, tradotti: list) -> list:
    """Gli indici degli errori noti che la nuova traduzione rifà (contiene di nuovo una parola tolta)."""
    return [i for i, parole in noti.items() if i < len(tradotti) and parole & _parole(tradotti[i])]


def misura(caso: dict, testi: list, durate: list, grezza: list, finale: list, scheda: dict) -> dict:
    corretta = st.leggi(Path(caso["corretta"]))[0]
    noti = errori_noti(testi, st.leggi(Path(caso["prima"]))[0], corretta) if caso.get("prima") else {}
    return {"chrf": round(chrf(finale, corretta), 2),
            "errori_noti": len(rifatti(noti, finale)), "errori_noti_totali": len(noti),
            "sospette": len(revisione.sospette(testi, finale, scheda)),
            "fuori_posto_grezza": len(st.sfasamenti(testi, grezza)),
            "fuori_posto": len(st.sfasamenti(testi, finale)),
            "da_controllare": len(adattamento.avvisi(testi, finale, durate))}


# --------------------------------------------------------------------------
# Casi e storia
# --------------------------------------------------------------------------

def leggi_casi() -> dict:
    try:
        return json.loads(CASI.read_text())
    except (OSError, ValueError):
        return {}


def storia(nome: str = None) -> list:
    try:
        righe = [json.loads(r) for r in STORIA.read_text().splitlines() if r.strip()]
    except OSError:
        return []
    return [r for r in righe if nome is None or r["caso"] == nome]


def aggiungi(a) -> None:
    casi = leggi_casi()
    controllati = {"originale": a.originale, "corretta": a.corretta, "prima": a.prima, "scheda": a.scheda}
    for chiave, percorso in controllati.items():
        if percorso and not Path(percorso).exists():
            sys.exit(f"Non trovo il file {chiave}: {percorso}")
    testi, corretta = st.leggi(Path(a.originale))[0], st.leggi(Path(a.corretta))[0]
    if len(testi) != len(corretta):
        sys.exit(f"Originale ({len(testi)} battute) e traduzione corretta ({len(corretta)}) devono avere le stesse battute.")
    casi[a.nome] = {k: str(Path(v).resolve()) for k, v in controllati.items() if v}
    casi[a.nome]["opzioni"] = {"lingua_sub": a.da, "lingua_audio": a.audio, "note": a.note}
    CARTELLA.mkdir(parents=True, exist_ok=True)
    CASI.write_text(json.dumps(casi, ensure_ascii=False, indent=2))
    print(f"Caso «{a.nome}» aggiunto: {len(testi)} battute.")


# --------------------------------------------------------------------------
# Prova
# --------------------------------------------------------------------------

def prova(nome: str, caso: dict, modello: str, revisore) -> dict:
    testi, durate, ricostruisci, _ = st.leggi(Path(caso["originale"]))
    opzioni = caso.get("opzioni", {})
    traduttore = motore_locale.Sessione(modello)
    try:
        scheda = json.loads(Path(caso["scheda"]).read_text()) if caso.get("scheda") else None
        film = [(Path(caso["originale"]).name, testi, durate)]
        traduttore.prepara(adattamento.caratteri_massimi(film, opzioni, scheda))
        inizio = time.time()
        if scheda is None:
            scheda = adattamento.analizza(traduttore, film, opzioni)
        # La traduzione grezza è quella prima del controllo dei tratti fuori posto: la si prende al volo.
        grezza = {}
        rimetti = adattamento._rimetti_a_posto

        def registra(sessione, prefisso, testi_, durate_, tradotti, progresso):
            grezza["t"] = list(tradotti)
            rimetti(sessione, prefisso, testi_, durate_, tradotti, progresso)

        adattamento._rimetti_a_posto = registra
        try:
            finale, _ = adattamento.traduci(traduttore, testi, durate, opzioni, scheda, lambda *a: None)
        finally:
            adattamento._rimetti_a_posto = rimetti
        secondi_traduzione = time.time() - inizio
    finally:
        traduttore.libera()
    correzioni, secondi_rilettura = 0, 0.0
    if revisore:
        lettore = motore_locale.Sessione(revisore)
        try:
            lettore.prepara(revisione.caratteri_massimi(testi, finale, scheda, opzioni))
            inizio = time.time()
            finale, modifiche = revisione.rivedi(lettore, testi, durate, finale, scheda, opzioni)
            secondi_rilettura, correzioni = time.time() - inizio, len(modifiche)
        finally:
            lettore.libera()
    risultato = {"caso": nome, "quando": time.strftime("%Y-%m-%d %H:%M"), "modello": modello,
                 "revisore": revisore or "", **misura(caso, testi, durate, grezza["t"], finale, scheda),
                 "secondi_traduzione": round(secondi_traduzione), "secondi_rilettura": round(secondi_rilettura),
                 "correzioni_rilettura": correzioni}
    USCITE.mkdir(parents=True, exist_ok=True)
    uscita = USCITE / f"{nome} - {time.strftime('%Y%m%d-%H%M')} - {modello.replace(':', '_')}.srt"
    uscita.write_text(ricostruisci(finale), encoding="utf-8")
    with STORIA.open("a") as f:
        f.write(json.dumps(risultato, ensure_ascii=False) + "\n")
    return risultato


def confronto(attuale: dict, precedente: dict = None) -> str:
    """Le misure della prova, con la differenza dalla precedente e un giudizio: meglio, peggio, uguale."""
    righe = [f"«{attuale['caso']}» — {attuale['modello']}"
             + (f", riletto da {attuale['revisore']}" if attuale["revisore"] else ", senza rilettura")]
    if precedente:
        righe.append(f"  confronto con la prova del {precedente['quando']} ({precedente['modello']}"
                     + (f" + {precedente['revisore']}" if precedente.get("revisore") else "") + ")")
    for chiave, (nome, alto_meglio) in MISURE.items():
        valore = attuale[chiave]
        testo = f"{valore}" + (f" su {attuale['errori_noti_totali']}" if chiave == "errori_noti" else "")
        if precedente and chiave in precedente and alto_meglio is not None:
            diff = valore - precedente[chiave]
            soglia = 0.5 if chiave == "chrf" else (max(10, 0.1 * precedente[chiave]) if chiave.startswith("secondi") else 0)
            giudizio = "uguale" if abs(diff) <= soglia else ("meglio" if (diff > 0) == alto_meglio else "PEGGIO")
            testo += f"  (prima {precedente[chiave]}: {giudizio})"
        righe.append(f"  {nome}: {testo}")
    return "\n".join(righe)


def main() -> int:
    p = argparse.ArgumentParser(description="Banco di prova dei modelli di Ozu.")
    sotto = p.add_subparsers(dest="comando", required=True)
    agg = sotto.add_parser("aggiungi", help="aggiunge un film già tradotto e corretto")
    agg.add_argument("nome")
    agg.add_argument("originale")
    agg.add_argument("corretta")
    agg.add_argument("--prima", help="la traduzione di Ozu prima delle correzioni a mano")
    agg.add_argument("--scheda", help="la scheda del film")
    agg.add_argument("--da", default="auto")
    agg.add_argument("--audio", default="auto")
    agg.add_argument("--note", default="")
    pro = sotto.add_parser("prova", help="traduce i casi e confronta con le prove precedenti")
    pro.add_argument("nomi", nargs="*")
    pro.add_argument("--modello", default=motore_locale.MODELLO)
    pro.add_argument("--revisore", default=revisione.REVISORE)
    pro.add_argument("--senza-rilettura", action="store_true")
    sto = sotto.add_parser("storia", help="le prove fatte")
    sto.add_argument("nomi", nargs="*")
    a = p.parse_args()

    if a.comando == "aggiungi":
        aggiungi(a)
        return 0
    casi = leggi_casi()
    nomi = a.nomi or list(casi)
    mancanti = [n for n in nomi if n not in casi]
    if mancanti or not nomi:
        sys.exit(f"Casi sconosciuti: {', '.join(mancanti)}" if mancanti
                 else "Nessun caso: aggiungine uno con «banco_di_prova.py aggiungi».")
    if a.comando == "storia":
        for nome in nomi:
            prove = storia(nome)
            for precedente, attuale in zip([None] + prove, prove):
                print(confronto(attuale, precedente), end="\n\n")
        return 0
    for nome in nomi:
        precedenti = storia(nome)
        print(f"Prova di «{nome}»…", flush=True)
        risultato = prova(nome, casi[nome], a.modello, None if a.senza_rilettura else a.revisore)
        print(confronto(risultato, precedenti[-1] if precedenti else None), end="\n\n", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
