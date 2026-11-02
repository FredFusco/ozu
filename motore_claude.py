"""Claude (API di Anthropic) come motore dell'adattamento: vedi adattamento.py."""

import json
import time

import adattamento
import chiavi

MODELLI = {
    "claude-fable-5-1": {"nome": "Claude Fable 5.1", "input": 10.0, "output": 50.0, "cache_lettura": 0.25},
    "claude-opus-5-5": {"nome": "Claude Opus 5.5", "input": 4.0, "output": 20.0, "cache_lettura": 0.20},
}
MODELLO = "claude-fable-5-1"  # il più capace: la qualità viene prima di tempo e costo
SFORZO = "xhigh"


def configurato() -> bool:
    return bool(chiavi.leggi("anthropic"))


class Sessione:
    """Un client verso l'API, con il conto di quanto si è speso."""

    def __init__(self, modello: str = None, sforzo: str = SFORZO):
        import anthropic
        chiave = chiavi.leggi("anthropic")
        if not chiave:
            raise RuntimeError("Manca la chiave API di Anthropic: inseriscila in Ozu › Impostazioni.")
        self.anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=chiave, max_retries=6)
        self.modello = modello if modello in MODELLI else MODELLO
        self.nome = MODELLI[self.modello]["nome"]
        self.sforzo = sforzo
        self.costo = 0.0

    def prepara(self, caratteri: int) -> None:
        """Con Claude non c'è niente da preparare: il contesto è sempre di un milione di token."""

    def chiedi(self, blocchi: list, schema: dict, attesi: int = 0, avanza=None) -> dict:
        """attesi: quanti caratteri ci si aspetta nella risposta; avanza(frazione) segue la scrittura."""
        a = self.anthropic
        try:
            with self.client.beta.messages.stream(
                model=self.modello,
                max_tokens=64000,
                system=adattamento.SYSTEM,
                messages=[{"role": "user", "content": blocchi}],
                output_config={"effort": self.sforzo, "format": {"type": "json_schema", "schema": schema}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as flusso:
                scritti, ultimo = 0, 0.0
                for pezzo in flusso.text_stream:
                    scritti += len(pezzo)
                    if avanza and attesi and time.time() - ultimo > 1:
                        avanza(min(.95, scritti / attesi))
                        ultimo = time.time()
                r = flusso.get_final_message()
        except a.AuthenticationError:
            raise RuntimeError("La chiave API di Anthropic non è valida: correggila in Ozu › Impostazioni.")
        except a.PermissionDeniedError:
            raise RuntimeError("La chiave API di Anthropic non ha accesso a questo modello.")
        except a.RateLimitError:
            raise RuntimeError("Troppe richieste ad Anthropic anche dopo vari tentativi: riprova tra qualche minuto.")
        except a.BadRequestError as e:
            raise RuntimeError(f"Anthropic ha rifiutato la richiesta: {e.message}")
        except a.APIStatusError as e:
            raise RuntimeError(f"Il servizio di Anthropic ha un problema ({e.status_code}): riprova più tardi.")
        except a.APIConnectionError:
            raise RuntimeError("Non riesco a collegarmi ad Anthropic: controlla la connessione.")

        self._conta(r)
        if r.stop_reason == "refusal":
            raise RuntimeError("Claude ha rifiutato di elaborare una parte del film.")
        if r.stop_reason == "max_tokens":
            raise RuntimeError("La risposta di Claude si è interrotta: il blocco era troppo lungo.")
        return json.loads(next(b.text for b in r.content if b.type == "text"))

    def _conta(self, r) -> None:
        p = MODELLI.get(r.model, MODELLI[self.modello])
        u = r.usage
        letti = getattr(u, "cache_read_input_tokens", 0) or 0
        scritti = getattr(u, "cache_creation_input_tokens", 0) or 0
        self.costo += (u.input_tokens * p["input"] + scritti * p["input"] * 2  # cache di un'ora: 2× l'input
                       + letti * p["cache_lettura"] + u.output_tokens * p["output"]) / 1e6
