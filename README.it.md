<p align="center"><img src="https://raw.githubusercontent.com/ellethee/pachifast/main/docs/assets/logo.svg" alt="pachifast" width="160"></p>

# pachifast

[English](https://github.com/ellethee/pachifast/blob/main/README.md)

Framework open source per costruire servizi [FastAPI](https://fastapi.tiangolo.com/) con una struttura modulare, una CLI
che genera progetti e moduli, autenticazione integrata con ruoli e permessi e migrazioni Alembic su più database.

Versione: **<!-- version -->0.19.0<!-- /version -->** - Autore: **ilPachi** - Licenza: **MIT** - Stato: **alpha** (0.x: le versioni minori possono contenere modifiche incompatibili).

## Cosa offre

- **Modulare**: registro esplicito dei moduli (`project_modules.py`), `apps/` per i moduli applicativi e `common/` per
  quelli condivisi; ogni modulo porta router, impostazioni, migrazioni, hook e cataloghi di messaggi.
- **Autenticazione e autorizzazione**: token JWT di accesso e refresh, password Argon2id, blocco dopo tentativi falliti,
  utenti, gruppi e permessi, MFA opzionale (TOTP, codici via email e SMS), impostazioni per utente con schema UI.
- **Un contratto API uniforme**: ogni successo è `{success, message, data}`, ogni errore è una risposta non-2xx con
  `{"detail": [{loc, msg, type, code, params?}]}` e un `code` stabile; JSON in camelCase; paginazione integrata.
- **Messaggi localizzati**: codici stabili, cataloghi per modulo e per progetto, ed esportazione dei file JSON per un
  frontend vue-i18n (con modalità di sola verifica per la CI).
- **Eventi in tempo reale**: WebSocket autenticato con ticket monouso, activity log e disconnessione automatica degli
  utenti eliminati, disattivati o declassati.
- **Migrazioni**: un target Alembic per ogni database dichiarato dai moduli.
- **Operatività**: `serve` per la produzione, Dockerfile e pipeline CI/CD generati (GitHub, GitLab, Gitea), endpoint di
  health e versione, cache Redis opzionale.
- **Configurazione comoda da editor**: variabili d'ambiente, `.env` e un `pachifast.yaml` con JSON Schema.

## Installazione

Servono Python 3.11 o successivo e [uv](https://docs.astral.sh/uv/):

```bash
uv tool install pachifast
pachifast --version
```

In un progetto, `uv add pachifast` fa lo stesso. Per installare una versione specifica da PyPI (sostituisci `X.Y.Z`):

```bash
uv tool install pachifast==X.Y.Z
```

La cache Redis è un extra: `pachifast[redis]`; PostgreSQL è `pachifast[postgres]`. Database supportati: SQLite e
PostgreSQL. Per altre opzioni di installazione vedi [Getting started](https://pachifast.ilpachi.it/getting-started/)
(in inglese).

## Stabilità

pachifast è in **alpha**: il contratto dell'API (envelope di risposta, codici dei messaggi) è protetto da test, ma
impostazioni, CLI e schema del database del core possono cambiare in ogni versione minore 0.x, sempre annunciate nel
[changelog](https://github.com/ellethee/pachifast/blob/main/CHANGELOG.md) e in [Upgrading](https://pachifast.ilpachi.it/guides/upgrading/). Dettagli e politica di deprecazione:
[Stability](https://pachifast.ilpachi.it/guides/stability/) (in inglese).

## Avvio rapido

```bash
pachifast new project demo && cd demo   # crea un progetto
pachifast new app hello                 # aggiunge un modulo applicativo
pachifast init                          # .env con chiave segreta, database SQLite, migrazioni, primo superuser
pachifast dev                           # avvio con ricarica automatica
```

Poi apri <http://127.0.0.1:8000/docs> (endpoint dell'applicazione) o <http://127.0.0.1:8000/docs/core> (autenticazione,
amministrazione, health, versione). Le rotte dell'applicazione richiedono un token: accedi con `POST /api/auth/token`.

Vuoi vedere prima un progetto completo? [`examples/demo`](https://github.com/ellethee/pachifast/tree/main/examples/demo) è un backend funzionante (MFA, model admin, un modulo con
tabelle, upload, azioni di massa e impostazioni per utente) che gira su SQLite, senza servizi esterni.

Un progetto ha questa struttura:

```text
demo/
  apps/            # moduli applicativi
  common/          # moduli condivisi
  main.py          # l'app ASGI
  project_modules.py
  pachifast.yaml   # configurazione opzionale (con JSON Schema per gli editor)
  pyproject.toml
```

## Documentazione

La documentazione completa (in inglese) è pubblicata su <https://pachifast.ilpachi.it/> e si trova in [`docs/`](https://github.com/ellethee/pachifast/tree/main/docs): guida introduttiva, guide per argomento,
riferimenti generati dal codice (CLI, impostazioni, codici dei messaggi), [changelog](https://github.com/ellethee/pachifast/blob/main/CHANGELOG.md) e
[contributi](https://pachifast.ilpachi.it/contributing/).

## Licenza

MIT, vedi [LICENSE](https://github.com/ellethee/pachifast/blob/main/LICENSE).

**Esclusione di garanzia.** pachifast è fornito "così com'è" ("as is"), senza garanzie di alcun tipo. È software alpha:
può contenere errori e cambiare in modo incompatibile tra versioni minori. Sei responsabile di verificare, testare e
mettere in sicurezza le applicazioni che costruisci con pachifast prima di usarle in produzione. Vedi la
[politica di stabilità](https://pachifast.ilpachi.it/guides/stability/).
