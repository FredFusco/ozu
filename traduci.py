#!/usr/bin/env python3
"""Ozu: traduce in italiano i sottotitoli di un film, da qualsiasi lingua.

Lavora come un adattatore: legge tutto il film, prepara la scheda (personaggi,
registri tu/lei, glossario, errori OCR) e traduce tenendone conto. Lo fa con il modello
locale (predefinito: gratuito, su questo Mac con Ollama) o con Claude (a pagamento,
qualità più alta). DeepL e Google Traduttore restano come ripiego.

    ~/.venvs/ozu/bin/python traduci.py FILM.en.srt --audio ja
    ~/.venvs/ozu/bin/python traduci.py EP01.srt EP02.srt EP03.srt --solo-scheda
    ~/.venvs/ozu/bin/python traduci.py EP*.srt --scheda SERIE.scheda.json
    ~/.venvs/ozu/bin/python traduci.py cerca FILM.en.srt [--video FILM.mkv]
    ~/.venvs/ozu/bin/python traduci.py FILM.en.srt --cerca

«cerca» cerca sottotitoli italiani già esistenti (vedi ricerca.py); con --cerca la ricerca
viene prima della traduzione, e se trova sottotitoli validi la traduzione non parte
(uscita con codice 10).

Con il modello locale, finita la traduzione un secondo modello (Qwen 3.8 27B) rilegge tutto e
corregge gli errori veri (vedi revisione.py); --senza-rilettura la salta.

Il file tradotto (FILM.it.srt) e la scheda (FILM.scheda.json) vengono salvati accanto
all'originale, o nella cartella indicata con --out. Se l'elaborazione si interrompe,
rilanciando lo stesso comando riparte dal punto in cui si era fermata.
"""

import argparse
import hashlib
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

import adattamento
import chiavi
import motori
import motore_locale
import progresso
import ricerca
import revisione
import sottotitoli as st

PARZIALI = chiavi.DATI / "parziali"
SESSIONI_DA_LIBERARE = []  # le sessioni dei motori, da chiudere all'uscita (vedi in fondo)
CODICE_TROVATI = 10  # uscita quando la ricerca trova sottotitoli italiani: la traduzione non parte


def segnale(tipo: str, dati) -> None:
    """Righe @@ per l'app (vedi progresso.py)."""
    print(f"@@{tipo} {json.dumps(dati, ensure_ascii=False)}", flush=True)


def generale(fatti: float, totale: int) -> None:
    """Avanzamento di tutto il lavoro (analisi più traduzione), per la percentuale e il tempo che resta."""
    print(f"@@avanz lavoro {min(int(fatti), totale)} {totale}", flush=True)


def _impronta(*parti) -> str:
    h = hashlib.sha256()
    for p in parti:
        h.update(json.dumps(p, ensure_ascii=False, sort_keys=True, default=str).encode())
    return h.hexdigest()[:24]


def argomenti():
    p = argparse.ArgumentParser(description="Traduce sottotitoli in italiano.")
    p.add_argument("file", nargs="*", type=Path, help="sottotitoli (.srt .vtt .ass .ssa) o video con sottotitoli")
    p.add_argument("--motore", choices=motori.ORDINE, default=motori.PREDEFINITO)
    p.add_argument("--modello", default=None, help="modello di Claude (claude-fable-5-1, claude-opus-5-5) "
                                                    "o di Ollama (predefinito: gemma4:26b)")
    p.add_argument("--da", dest="lingua_sub", default="auto", help="lingua dei sottotitoli (es. en)")
    p.add_argument("--audio", dest="lingua_audio", default="auto", help="lingua parlata nel film (es. ja, zh, pt-BR)")
    p.add_argument("--note", default="", help="indicazioni per l'adattatore (Claude e modello locale)")
    p.add_argument("--note-file", type=Path, help="le indicazioni, lette da un file di testo")
    p.add_argument("--scheda", type=Path, help="usa questa scheda del film invece di prepararne una nuova")
    p.add_argument("--salva-scheda", type=Path, help="dove salvare la scheda (predefinito: accanto al primo file)")
    p.add_argument("--solo-scheda", action="store_true", help="prepara la scheda e si ferma, senza tradurre")
    p.add_argument("--senza-rilettura", action="store_true",
                   help=f"con il modello locale, non far rileggere la traduzione a {revisione.NOME_REVISORE}")
    p.add_argument("--out", type=Path, help="cartella dei file tradotti (predefinito: accanto agli originali)")
    p.add_argument("--cerca", action="store_true",
                   help="prima di tradurre cerca sottotitoli italiani esistenti; se li trova si ferma")
    p.add_argument("--video", type=Path, help="il video del film, per la ricerca per hash (predefinito: "
                                              "quello accanto ai sottotitoli)")
    p.add_argument("--chiave", metavar="sk-ant-…", help="salva la chiave API di Anthropic ed esce")
    p.add_argument("--chiave-deepl", metavar="CHIAVE", help="salva la chiave API di DeepL ed esce")
    return p.parse_args()


# --------------------------------------------------------------------------
# 0. Ricerca di sottotitoli italiani esistenti (ricerca.py)
# --------------------------------------------------------------------------

def cerca_esistenti(percorsi: list, video: Path = None) -> bool:
    """Cerca per ogni file; True se per almeno uno ha trovato e salvato sottotitoli italiani validi."""
    progresso.fase("Ricerca di sottotitoli italiani esistenti")
    trovati = False
    for i, percorso in enumerate(percorsi, 1):
        esito = ricerca.cerca(percorso, video if len(percorsi) == 1 else None)
        segnale("ricerca", {"file": str(percorso), **esito.come_dati()})
        print(esito.messaggio(), flush=True)
        trovati = trovati or bool(esito.trovato)
        progresso.avanzamento("ricerca", i, len(percorsi))
    return trovati


def comando_cerca(argv: list) -> int:
    """traduci.py cerca FILE… [--video VIDEO]: solo la ricerca, senza tradurre."""
    p = argparse.ArgumentParser(prog="traduci.py cerca",
                                description="Cerca sottotitoli italiani esistenti (OpenSubtitles, SUBDL) e li "
                                            "salva accanto al file come «<nome> - Sottotitoli IT (scaricati).srt».")
    p.add_argument("file", nargs="+", type=Path, help="i sottotitoli di partenza (o il video)")
    p.add_argument("--video", type=Path, help="il video, per la ricerca per hash (solo con un file)")
    a = p.parse_args(argv)
    return CODICE_TROVATI if cerca_esistenti(a.file, a.video) else 0


# --------------------------------------------------------------------------
# 1. Lettura
# --------------------------------------------------------------------------

def leggi_film(percorsi: list) -> list:
    """I file leggibili, come [(percorso, testi, durate, ricostruisci, estensione), …]. Un file rovinato
    o senza sottotitoli di testo viene segnalato e saltato: gli altri si traducono lo stesso."""
    progresso.fase("Lettura dei sottotitoli")
    film = []
    for i, percorso in enumerate(percorsi, 1):
        try:
            film.append((percorso, *st.leggi(percorso)))
        except (OSError, RuntimeError, UnicodeError, ValueError) as e:
            dettaglio = e.strerror if isinstance(e, OSError) and e.strerror else str(e)
            print(f"Salto {percorso.name}: {dettaglio}", flush=True)
            segnale("esito", {"file": str(percorso), "errore": f"illeggibile: {dettaglio}"})
        progresso.avanzamento("lettura", i, len(percorsi))
    return film


# --------------------------------------------------------------------------
# 2. Scheda del film
# --------------------------------------------------------------------------

def prepara_scheda(a, sessione, film: list, opzioni: dict, totale: int, peso: int) -> dict:
    """Una scheda nuova, preparata dal modello e salvata accanto ai sottotitoli (o dove indicato)."""
    progresso.fase("Analisi del film")
    generale(0, totale)
    scheda = adattamento.analizza(sessione, [(p.name, t, d) for p, t, d, *_ in film], opzioni,
                                  avanza=lambda f: generale(f * peso, totale))
    generale(peso, totale)
    primo = film[0][0]
    destinazione = a.salva_scheda or (a.out or primo.parent) / (
        Path(st.nome_tradotto(primo, "")).stem.removesuffix(".it") + ".scheda.json")
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    provvisorio = destinazione.with_name(destinazione.name + ".tmp")
    provvisorio.write_text(json.dumps(scheda, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(provvisorio, destinazione)
    segnale("scheda", str(destinazione))
    print(f"Scheda del film: {destinazione}", flush=True)
    return scheda


# --------------------------------------------------------------------------
# 3. Traduzione
# --------------------------------------------------------------------------

def traduci_file(motore: str, sessione, scheda, opzioni: dict, voce: tuple, avanza) -> tuple:
    """(traduzioni, avvisi) di un file. Con i motori che adattano, le battute già tradotte di un
    tentativo interrotto vengono riprese da PARZIALI."""
    percorso, testi, durate, *_ = voce
    if not motori.adatta(motore):
        return motori.MODULI[motore].traduci(testi, opzioni, avanza), []
    # L'impronta riconosce lo stesso lavoro: stesso file, stesse battute, stesso modello, scheda e
    # indicazioni. Si calcola dalle battute, mai leggendo il file (un video può pesare decine di GB).
    parziale = PARZIALI / (_impronta(percorso.name, testi, sessione.modello, scheda, opzioni) + ".json")
    try:
        fatte = json.loads(parziale.read_text()) if parziale.exists() else {}
    except (OSError, ValueError):
        fatte = {}
    if fatte:
        print(f"Riprendo da dove mi ero fermato: {len(fatte)} battute già tradotte.", flush=True)

    def salva(fatte_ora):
        PARZIALI.mkdir(parents=True, exist_ok=True)
        provvisorio = parziale.with_suffix(".tmp")
        provvisorio.write_text(json.dumps(fatte_ora, ensure_ascii=False))
        os.replace(provvisorio, parziale)

    risultato = adattamento.traduci(sessione, testi, durate, opzioni, scheda, avanza, fatte, salva)
    parziale.unlink(missing_ok=True)
    return risultato


def main() -> int:
    a = argomenti()
    if a.chiave or a.chiave_deepl:
        if a.chiave:
            chiavi.salva("anthropic", a.chiave)
        if a.chiave_deepl:
            chiavi.salva("deepl", a.chiave_deepl)
        print("Chiave salvata.")
        return 0
    if not a.file:
        sys.exit("Indica almeno un file di sottotitoli. Aiuto: traduci.py --help")
    opzioni = {"lingua_sub": a.lingua_sub, "lingua_audio": a.lingua_audio,
               "note": a.note_file.read_text(encoding="utf-8") if a.note_file else a.note}

    if a.cerca and cerca_esistenti(a.file, a.video):
        return CODICE_TROVATI

    motore = motori.disponibile(a.motore)
    if motore != a.motore:
        print(f"{motori.NOMI[a.motore]} non è configurato: uso {motori.NOMI[motore]}.", flush=True)
    adatta = motori.adatta(motore)
    if a.solo_scheda and not adatta:
        sys.exit("La scheda del film la prepara Claude o il modello locale, non " + motori.NOMI[motore] + ".")

    film = leggi_film(a.file)
    if not film:
        print("Nessun file da tradurre.", flush=True)
        return 1

    sessione, scheda = None, None
    if adatta:
        sessione = motori.MODULI[motore].Sessione(a.modello)
        SESSIONI_DA_LIBERARE.append(sessione)
        segnale("motore", f"{motori.NOMI[motore]} ({sessione.nome})")
        scheda = json.loads(a.scheda.read_text(encoding="utf-8")) if a.scheda else None
        sessione.prepara(adattamento.caratteri_massimi([(p.name, t, d) for p, t, d, *_ in film], opzioni,
                                                       scheda))
    else:
        segnale("motore", motori.NOMI[motore])

    # Tutto il lavoro misurato in battute, per la percentuale e il tempo che resta.
    battute = sum(len(t) for _, t, *_ in film)
    peso = adattamento.peso_analisi(battute) if adatta and not a.scheda else 0
    rilettura = motore == "locale" and not a.senza_rilettura and not a.solo_scheda
    totale = peso + (0 if a.solo_scheda else battute) + (revisione.PESO * battute if rilettura else 0)
    if adatta:
        if scheda is None:
            scheda = prepara_scheda(a, sessione, film, opzioni, totale, peso)
        segnale("costo", round(sessione.costo, 2))
        if a.solo_scheda:
            return 0

    esito, prima, tradotti_tutti = 0, peso, []
    for n, voce in enumerate(film, 1):
        percorso, testi, _, ricostruisci, estensione = voce
        segnale("file", {"numero": n, "totale": len(film), "nome": percorso.name})
        progresso.fase(f"Traduzione di {percorso.name}" if len(film) > 1 else "Traduzione")

        def avanza(fatti, quante, fase=None, prima=prima):
            if fase:
                progresso.fase(fase)
            progresso.avanzamento("battute", int(fatti), quante)
            generale(prima + fatti, totale)

        try:
            tradotti, avvisi = traduci_file(motore, sessione, scheda, opzioni, voce, avanza)
            cartella = a.out or percorso.parent
            cartella.mkdir(parents=True, exist_ok=True)
            uscita = cartella / st.nome_tradotto(percorso, estensione)
            provvisorio = uscita.with_name(uscita.name + ".tmp")
            provvisorio.write_text(ricostruisci(tradotti), encoding="utf-8")
            os.replace(provvisorio, uscita)
            print(f"Salvato: {uscita}", flush=True)
            for avviso in avvisi[:30]:
                print(f"  ! {avviso}", flush=True)
            dati = {"file": str(percorso), "uscita": str(uscita), "battute": len(testi), "avvisi": avvisi,
                    "motore": motori.NOMI[motore]}
            segnale("esito", dati)
            tradotti_tutti.append((voce, uscita, tradotti, dati))
        except Exception as e:
            print(f"Errore con {percorso.name}: {e}", flush=True)
            segnale("esito", {"file": str(percorso), "errore": str(e)})
            esito = 1
            if adatta and isinstance(e, RuntimeError) and any(p in str(e) for p in ("chiave", "Ollama")):
                break  # inutile continuare con gli altri file
        if sessione:
            segnale("costo", round(sessione.costo, 2))
        prima += len(testi)
    if rilettura and tradotti_tutti:
        rileggi(sessione, scheda, opzioni, tradotti_tutti, prima, totale)
    if motore == "claude":
        print(f"Costo stimato: ${sessione.costo:.2f} ({sessione.nome})", flush=True)
    return esito


# --------------------------------------------------------------------------
# 4. Rilettura con un secondo modello (revisione.py)
# --------------------------------------------------------------------------

def rileggi(sessione, scheda: dict, opzioni: dict, tradotti_tutti: list, prima: int, totale: int) -> None:
    """Dopo che tutti i file sono tradotti e salvati: così il modello cambia una volta sola, e se la
    rilettura si interrompe o non riesce restano le traduzioni senza rilettura."""
    if not motore_locale.pronto(revisione.REVISORE):
        print(f"Rilettura saltata: il modello {revisione.NOME_REVISORE} non è scaricato "
              f"(si scarica con: ollama pull {revisione.REVISORE}).", flush=True)
        return
    sessione.libera()  # il traduttore esce dalla memoria prima che entri il revisore
    revisore = motore_locale.Sessione(revisione.REVISORE)
    SESSIONI_DA_LIBERARE.append(revisore)
    segnale("motore", f"{motori.NOMI['locale']} ({sessione.nome}, riletto da {revisione.NOME_REVISORE})")
    revisore.prepara(max(revisione.caratteri_massimi(v[1], t, scheda, opzioni) for v, _, t, _ in tradotti_tutti))
    for voce, uscita, tradotti, dati in tradotti_tutti:
        percorso, testi, durate, ricostruisci, _ = voce
        progresso.fase(f"Rilettura di {percorso.name}" if len(tradotti_tutti) > 1
                       else f"Rilettura ({revisione.NOME_REVISORE})")

        def avanza(fatti, quante, prima=prima):
            progresso.avanzamento("battute", fatti, quante)
            generale(prima + revisione.PESO * fatti, totale)

        try:
            riletti, modifiche = revisione.rivedi(revisore, testi, durate, tradotti, scheda, opzioni, avanza)
        except RuntimeError as e:
            print(f"Rilettura non riuscita per {percorso.name}: {e} Resta la traduzione senza rilettura.", flush=True)
            continue
        finally:
            prima += revisione.PESO * len(testi)
        if modifiche:
            provvisorio = uscita.with_name(uscita.name + ".tmp")
            provvisorio.write_text(ricostruisci(riletti), encoding="utf-8")
            os.replace(provvisorio, uscita)
        print(f"Rilettura: {len(modifiche)} battute corrette in {percorso.name}", flush=True)
        for m in modifiche:
            vecchia, nuova = (m[k].replace("\n", " / ") for k in ("prima", "dopo"))
            print(f"  ✎ #{m['n']}: {vecchia} → {nuova} ({m['motivo']})", flush=True)
        segnale("esito", {**dati, "avvisi": adattamento.avvisi(testi, riletti, durate), "rilette": modifiche,
                          "revisore": revisione.NOME_REVISORE})


def sorveglia_genitore() -> None:
    """Se chi ha lanciato la traduzione (l'app Ozu o il Terminale) si chiude, ci si ferma anche qui."""
    genitore = os.getppid()

    def controlla():
        while True:
            time.sleep(2)
            if os.getppid() != genitore:
                os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=controlla, daemon=True).start()


if __name__ == "__main__":
    # Fermato dall'app (o dal Terminale): si esce passando dal finally, che libera il modello.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    sorveglia_genitore()
    try:
        sys.exit(comando_cerca(sys.argv[2:]) if sys.argv[1:2] == ["cerca"] else main())
    except RuntimeError as e:
        print(f"Errore: {e}", flush=True)
        sys.exit(1)
    finally:
        for s in SESSIONI_DA_LIBERARE:
            if hasattr(s, "libera"):
                s.libera()
