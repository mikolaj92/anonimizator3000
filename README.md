# Anonimizator3000

Publiczny portal do lokalnej anonimizacji dokumentów.

## Architektura

Portal odpowiada za UI, upload, limity per IP i lokalnego workera.
Logika przetwarzania pochodzi z pakietów pobieranych przez `uv`:

- `github.com/mikolaj92/Docxtor` — odczyt dokumentów i zapis podmienionego tekstu.
- `github.com/mikolaj92/Posejdon` — anonimizacja przez Presidio, regex/walidację PL i opcjonalny GLiNER.
- `github.com/mikolaj92/Fala` — runtime procesu: pipeline, statusy, claimy workerów i event log.

`pyproject.toml` przypina źródła Git tagami lub rewizjami, nie branchem `main`:
`app-factory v0.6.22`, `my-auth v0.5.4`, `my-usermanager v0.6.5`,
`Docxtor v0.14.1`, `posejdon v0.2.3` i `fala-runtime` przez `rev`.
`uv.lock` przypina konkretne commity; to nie jest wiersz COMPAT.md.

### Przepływ dokumentu

`pipeline.py` uruchamia graf `DOCUMENT_FLOW` w tymczasowym runtime Fala opartym na SQLite:
`convert` → `load` → `anonymize` → `serialize` → `redact_authors`.
Adaptery `python_function` w `steps.py` walidują typ, odczytują dokument, anonimizują tekst,
zapisują wynik w formacie wejściowym i usuwają tożsamości autorów DOCX.
Dokument pozostaje w pamięci `JobContext`; runtime zapisuje tylko metadane opisane w sekcji „Prywatność”.
Portal używa API CPython carrier: `fala.models`, `fala.carrier_runtime.FalaRuntime`
i `fala.runtime_backend`, w tym `RuntimeBackendService`.
Przypięta rewizja `fala-runtime` dostarcza to API; Fala v0.7.x go nie dostarcza.
Zmiana na host/sdk wymaga migracji integracji, nie samej aktualizacji zależności.

## Stack

Portal używa `uv`, FastAPI, HTMX i Basecoat UI.

### Klient HTTP

Własny kod i testy używają bezpośrednio `import httpx2` oraz typów
i wyjątków `httpx2.*`, bez aliasów `as httpx`, shimów i fallbacku do starszych klientów.
`fastapi.testclient.TestClient` pozostaje publicznym klientem testowym.
Extra `dev` i grupa `dev` deklarują HTTPX2 bezpośrednio.
Biblioteki zewnętrzne mogą nadal wymagać `httpx`, `httpcore`, `requests`,
`urllib3` i `spacy-legacy`. Nie zastępujemy ich niezgodnymi override'ami.
Bieżące ścieżki zależności, dodatki i markery można odczytać z lockfile:

```bash
uv run --locked python -m anonimizator3000.dependency_audit
```

## Uruchomienie

`Posejdon` jest prywatnym repozytorium. Synchronizacja wymaga dostępu GitHub
do `mikolaj92/Posejdon` przez skonfigurowane uwierzytelnianie Git.
Projekt wymaga Python `>=3.12,<3.14`.

```bash
git clone https://github.com/mikolaj92/anonimizator3000.git
cd anonimizator3000
uv sync
uv run anonimizator3000
```

Skrypt używa hosta `127.0.0.1`, portu `8000` i automatycznego przeładowania.
Nie obsługuje flag `--host`, `--port` ani `--reload`.
Otwórz `http://127.0.0.1:8000`.
Inne ustawienia, na przykład udostępnienie w LAN, wymagają bezpośredniego uruchomienia uvicorn:

```bash
uv run uvicorn anonimizator3000.main:app --host 0.0.0.0 --port 8000 --reload
```

## Obsługiwane wejście

Portal przyjmuje `.pdf` i `.docx` przez `Docxtor`. Wynik zachowuje typ wejścia.

PDF z warstwą tekstową jest modyfikowany przez redakcje na oryginalnych stronach.
Liczba stron i grafika pozostają zachowane, gdy fragmenty można dopasować do tekstu strony.
Gdy bezpieczne dopasowanie jest niemożliwe, `Docxtor` zastępuje tekst całej strony, aby zapobiec wyciekowi.
DOCX jest modyfikowany w pamięci. Struktura akapitów i tabel pozostaje,
ale formatowanie w ramach jednego akapitu może się uprościć.

## Detekcja

Warstwy detekcji pochodzą z `Posejdon`:

1. Presidio. Przypięty `TextAnonymizer` montuje regex i opcjonalny GLiNER;
   host dokłada `PresidioDetector`. Brak działającego backendu zatrzymuje start.
2. Opcjonalny GLiNER dla osób, organizacji, lokalizacji, adresów, szpitali, urzędów, spraw i umów.
3. Regex i walidacja identyfikatorów oraz numerów.

Regex/walidacja obejmuje między innymi:

- imiona i nazwiska, również odmiany i wystąpienia z kontekstem
- PESEL, NIP, REGON, polski IBAN/NRB i karty płatnicze z kontrolą sum
- dowód osobisty i paszport
- telefony i e-maile
- datę i miejsce urodzenia z kontekstem
- adresy uliczne i kody pocztowe
- szpitale, przychodnie, urzędy, sądy i firmy
- sygnatury spraw i numery umów
- IP, MAC, UUID, VIN, JWT i tokeny bearer/API

GLiNER jest domyślnie włączony z modelem `urchade/gliner_multi_pii-v1`.
Wymaga cięższych zależności:

```bash
uv sync --extra detectors
uv run uvicorn anonimizator3000.main:app --reload
```

Brak modelu lub backendu zatrzymuje start zamiast uruchamiać słabszy stos.
Operator może jawnie wyłączyć GLiNER przez `ANON_GLINER_ENABLED=false`.
Aplikacja sprawdza dostępność skonfigurowanego modelu i nie wybiera modelu zastępczego.
Monitor w `anonymizer.py` wykrywa wyjątki backendów Presidio i GLiNER,
które detektory zamieniają na pusty wynik, i przerywa zadanie.
Aplikacja nie tworzy klienta LLM ani reviewera; review LLM w `TextAnonymizer` jest wyłączone.

## Auth (passkeys + usermanager)

Portal używa `my-auth` (passkeys WebAuthn) oraz `my-usermanager`
(konta, role, `/account`, `/admin/users`).
`install_identity_adapters` montuje chrome, passkey i usermanager raz.
Host odpowiada za persistence, pierwszego admina, zaproszenia, RBAC i menu produktu.

Tożsamość i uprawnienia pochodzą wyłącznie z typed principal `my_usermanager.principal`.
Stare `session["user"]` nie uwierzytelnia użytkownika.
`AuthDatabaseBinding` przełącza zainstalowane routery na bazę otwartą ponownie podczas lifespan.
Powtórzona ceremonia WebAuthn i równoległe nadanie pierwszej roli są idempotentne.

### Polityka rejestracji

Rejestracja jest **otwarta**: każdy może założyć konto passkey przez `/register`.
Pierwszy użytkownik dostaje rolę admin.
`BOOTSTRAP_ADMIN_ID` / `ANON_BOOTSTRAP_ADMIN_ID` pozwala nadać admina znanemu `user_id`, idempotentnie przy starcie.
Przy publicznym wdrożeniu zabezpiecz reverse proxy lub sieć.

### Migracja bazy tożsamości

Przed zmianą schematu `auth.sqlite3` dostaje spójną kopię obok pliku:
`auth.sqlite3.pre-migration-<UTC>.sqlite3`.
Migracja my-auth i my-usermanager jest jedną transakcją, bez dual-read.
Nieznany układ zatrzymuje start.
Aby wycofać migrację, zatrzymaj usługę, zachowaj uszkodzony plik i przywróć kopię.

### Zmienne środowiskowe auth

| Zmienna | Domyślnie | Znaczenie |
| --- | --- | --- |
| `ANON_SESSION_SECRET` | `dev-anon-session-secret-change-me` | Sekret podpisu cookie sesji. **W produkcji ustaw własny, losowy sekret.** |
| `ANON_SESSION_COOKIE` | `anon_session` | Nazwa cookie sesji |
| `ANON_SESSION_COOKIE_SECURE` | `false` | `Secure` na cookie sesji i passkey cookies (`true` za HTTPS) |
| `ANON_SESSION_COOKIE_SAMESITE` | `lax` | Dotyczy cookie sesji; cookies passkey zawsze mają `SameSite=lax` |
| `ANON_SESSION_MAX_AGE` | `1209600` (14 dni) | Czas życia sesji w sekundach |
| `ANON_AUTH_DB` | `storage/auth.sqlite3` (w katalogu repo) | Trwała baza credentials, użytkowników i ról |
| `ANON_PASSKEY_RP_ID` | `localhost` | Domena WebAuthn bez schematu |
| `ANON_PASSKEY_RP_NAME` | `Dokumenty` | Wyświetlana nazwa RP |
| `ANON_PASSKEY_ORIGIN` | `http://localhost:8000` | Dozwolony origin WebAuthn |
| `BOOTSTRAP_ADMIN_ID` / `ANON_BOOTSTRAP_ADMIN_ID` | *(puste)* | Opcjonalny `user_id` dostający admina przy starcie |

Produkcja (HTTPS):

```bash
export ANON_SESSION_SECRET="$(openssl rand -hex 32)"
export ANON_SESSION_COOKIE_SECURE=true
export ANON_PASSKEY_RP_ID=anon.example.com
export ANON_PASSKEY_ORIGIN=https://anon.example.com
export ANON_AUTH_DB=/var/lib/anonimizator3000/auth.sqlite3
```

## Limity

| Zmienna | Domyślnie | Znaczenie |
| --- | ---: | --- |
| `ANON_MAX_FILE_BYTES` | `5000000` | Maksymalny rozmiar uploadu |
| `ANON_MAX_TEXT_CHARS` | `250000` | Maksymalna długość wyciągniętego tekstu |
| `ANON_QUEUE_MAX_SIZE` | `20` | Maksymalna liczba zadań w kolejce |
| `ANON_WORKER_COUNT` | `1` | Liczba lokalnych workerów |
| `ANON_MAX_ACTIVE_JOBS_PER_IP` | `2` | Maksymalna liczba aktywnych zadań per IP |
| `ANON_RATE_LIMIT_SUBMISSIONS` | `100` | Liczba uploadów w oknie czasowym |
| `ANON_RATE_LIMIT_WINDOW_SECONDS` | `600` | Okno limitu per IP |
| `ANON_JOB_TTL_SECONDS` | `900` | Czas przechowywania zakończonego wyniku w pamięci |
| `ANON_TRUST_PROXY_HEADERS` | `false` | Czy ufać `X-Forwarded-For` |
| `ANON_GLINER_ENABLED` | `true` | Włącza GLiNER; `false` to jawna zgoda na słabszy stos |
| `ANON_GLINER_MODEL` | `urchade/gliner_multi_pii-v1` | Model GLiNER; nie może być pusty przy włączonym GLiNER |
| `ANON_GLINER_THRESHOLD` | `0.45` | Próg predykcji GLiNER z zakresu `(0, 1]` |
| `ANON_REPLACEMENT_STYLE` | `mask` | `mask` daje `****`, a `labels` etykiety, np. `[OSOBA_1]` |

UI nie pokazuje selektora stylu.
Pole multipart `style` w `POST /jobs` przyjmuje `mask` lub `labels` i nadpisuje ustawienie dla zadania.
Brak pola lub niepoprawna wartość oznacza użycie ustawienia środowiskowego.
`ANON_TRUST_PROXY_HEADERS=true` włączaj tylko za reverse proxy, które czyści i ustawia `X-Forwarded-For`.

## Testy

```bash
uv run --extra dev pytest
uv run --extra dev ruff check .
```

`tests/test_http_client_contract.py` pilnuje bezpośredniego użycia HTTPX2 w kodzie, testach i manifeście.

## Prywatność

- Bajty i tekst dokumentu są przetwarzane w pamięci w `JobContext`; kontekst znika po zakończeniu zadania.
- Pobranie nie usuwa wyniku; pozostaje on w pamięci do sprzątania po upływie `ANON_JOB_TTL_SECONDS`.
- Fala zapisuje metadane workflow w `run.sqlite` w katalogu `anon-run-*` w systemowym katalogu tymczasowym.
- Metadane obejmują nazwę pliku wynikowego i liczby wykryć; mogą zawierać dane wrażliwe.
- Katalog workflow jest sprzątany przy normalnym wyjściu; usunięcie po awarii nie jest gwarantowane.
- **Auth jest trwały**: SQLite `ANON_AUTH_DB` przechowuje konta, passkey credentials i role niezależnie od runtime'u Fala.
