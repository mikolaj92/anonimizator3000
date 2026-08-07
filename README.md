# Anonimizator3000

Publiczny portal do lokalnej anonimizacji dokumentów.

Upload dokumentów trafia wyłącznie do pamięci procesu. Aplikacja nie zapisuje oryginalnych dokumentów na dysku. Oryginał jest usuwany z pamięci zaraz po zakończeniu zadania. Wynik anonimizacji jest trzymany krótko w pamięci, żeby użytkownik mógł go pobrać.

Osobno od dokumentów działa trwała baza SQLite platformy auth (passkey / konta użytkowników) — patrz sekcja Auth i zmienna `ANON_AUTH_DB`.

## Architektura

Ten projekt jest tylko portalem/orchestratorem. Nie zawiera własnego ekstraktora
dokumentów, silnika anonimizacji ani runtime'u workflow.

Logika jest w osobnych pakietach pobieranych z GitHub przez `uv`:

- `github.com/mikolaj92/DocToText` - odczyt tekstu z dokumentów i zapis podmienionego tekstu z powrotem do dokumentu.
- `github.com/mikolaj92/Posejdon` - anonimizacja tekstu przez Presidio, regex/walidację PL i opcjonalny GLiNER.
- `github.com/mikolaj92/Fala` - runtime procesu: pipeline, statusy, claimy workerów i event log przetwarzania.
- `src/anonimizator3000` - UI, upload, limity per IP, lokalny worker i integracja trzech pakietów.

`pyproject.toml` wskazuje branche `main`, a `uv.lock` przypina konkretne commity.

### Fala compatibility boundary

Anonimizator3000 remains on the immutable legacy `fala-runtime` revision. Its
pipeline imports the CPython carrier API (`fala.models`,
`fala.carrier_runtime.FalaRuntime`, and `fala.runtime_backend`), which is not
provided by Fala v0.7.x. Fala v0.7.18 is therefore **not** a drop-in upgrade for
this product; migrating it requires a separate runtime/API migration and is
outside this platform UI audit. Argus and MSDS use the separate v0.7.18 host/sdk
surface.

## Stack

- `uv`
- FastAPI
- HTMX
- Basecoat UI
- DocToText
- Posejdon
- Fala

## Uruchomienie

`Posejdon` jest prywatnym repo. `uv sync` wymaga konta albo tokenu GitHub z
dostępem do `mikolaj92/Posejdon`.

```bash
git clone https://github.com/mikolaj92/anonimizator3000.git
cd anonimizator3000
uv sync
uv run uvicorn anonimizator3000.main:app --reload
```

Potem otwórz `http://127.0.0.1:8000`.

Żeby wystawić aplikację w LAN:

```bash
uv run uvicorn anonimizator3000.main:app --host 0.0.0.0 --port 8000 --reload
```

## Obsługiwane wejście

- tekstowe: `.txt`, `.md`, `.csv`, `.json`, `.xml`, `.html`, `.log`
- `.pdf` przez `DocToText`
- `.docx` przez `DocToText`

Wynik zachowuje typ wejścia:

- PDF -> PDF
- DOCX -> DOCX
- tekst -> TXT

PDF z warstwą tekstową jest modyfikowany przez redakcje na oryginalnych stronach, więc liczba stron i grafika dokumentu zostają zachowane, gdy zmienione fragmenty da się dopasować do tekstu strony. Jeśli fragmentu nie da się bezpiecznie odnaleźć, `DocToText` zamyka ryzyko wycieku przez zastąpienie tekstu tej strony. DOCX jest modyfikowany w pamięci przez `DocToText`; struktura akapitów i tabel zostaje, ale formatowanie w ramach jednego akapitu może się uprościć.

## Detekcja

Warstwy są w `Posejdon`:

1. Presidio jako pipeline wykrywania i anonimizacji.
2. Opcjonalny GLiNER dla `PERSON`, `ORG`, `LOC`, adresów, szpitali, urzędów, spraw i umów.
3. Regex + walidacja dla identyfikatorów i numerów.

Regex/walidacja obejmuje między innymi:

- imiona i nazwiska, też część odmian typu `Jana Kowalskiego`
- nazwiska z kontekstem typu `Pani Nowak`, `pacjenta Jana Kowalskiego`
- PESEL z checksumą
- NIP z checksumą
- REGON z checksumą
- dowód osobisty i paszport
- polski IBAN/NRB z checksumą
- telefony, e-maile, karty płatnicze z Luhnem
- datę i miejsce urodzenia z kontekstem
- adresy uliczne i kody pocztowe
- szpitale, przychodnie, urzędy, sądy, firmy
- sygnatury spraw i numery umów
- IP, MAC, UUID, VIN, JWT, bearer/API tokens

GLiNER jest opcjonalny, bo wymaga cięższych zależności i modelu:

```bash
uv sync --extra detectors
ANON_GLINER_ENABLED=true uv run uvicorn anonimizator3000.main:app --reload
```

Zmienne:

- `ANON_GLINER_ENABLED=false`
- `ANON_GLINER_MODEL=urchade/gliner_multi_pii-v1`
- `ANON_GLINER_THRESHOLD=0.45`

## Auth (passkeys + usermanager)

Portal używa wspólnego stacku platformy: `my-auth` (passkeys WebAuthn) i `my-usermanager` (konta, role, `/account`, `/admin/users`).

### Polityka rejestracji

Rejestracja jest **otwarta** — każdy może założyć konto passkey przez `/register`. Pierwszy zarejestrowany użytkownik dostaje rolę admin. Dodatkowo można wymusić admina dla znanego `user_id` przez `BOOTSTRAP_ADMIN_ID` / `ANON_BOOTSTRAP_ADMIN_ID` (idempotentnie przy starcie).

To celowa polityka produktu (lokalny / self-hosted portal), nie bug. Jeśli wdrażasz publicznie, zabezpiecz reverse proxy / sieć albo wyłącz publiczną rejestrację w przyszłej konfiguracji.

### Zmienne środowiskowe auth

| Zmienna | Domyślnie | Znaczenie |
| --- | --- | --- |
| `ANON_SESSION_SECRET` | `dev-anon-session-secret-change-me` | Sekret podpisu cookie sesji (Starlette). **W produkcji ustaw własny, losowy sekret.** |
| `ANON_SESSION_COOKIE` | `anon_session` | Nazwa cookie sesji |
| `ANON_SESSION_COOKIE_SECURE` | `false` | `Secure` na cookie sesji i passkey cookies (`true` za HTTPS) |
| `ANON_SESSION_COOKIE_SAMESITE` | `lax` | `SameSite` cookie sesji |
| `ANON_SESSION_MAX_AGE` | `1209600` (14 dni) | Max age sesji w sekundach |
| `ANON_AUTH_DB` | `storage/auth.sqlite3` (w katalogu repo) | Ścieżka trwałej bazy SQLite auth (credentials, users, grants) |
| `ANON_PASSKEY_RP_ID` | `localhost` | WebAuthn Relying Party ID (domena bez schematu) |
| `ANON_PASSKEY_RP_NAME` | `Dokumenty` | Wyświetlana nazwa RP |
| `ANON_PASSKEY_ORIGIN` | `http://localhost:8000` | Dozwolony origin WebAuthn (pełny URL) |
| `BOOTSTRAP_ADMIN_ID` / `ANON_BOOTSTRAP_ADMIN_ID` | *(puste)* | Opcjonalny `user_id` dostający rolę admin przy starcie |

Produkcja (HTTPS):

```bash
export ANON_SESSION_SECRET="$(openssl rand -hex 32)"
export ANON_SESSION_COOKIE_SECURE=true
export ANON_PASSKEY_RP_ID=anon.example.com
export ANON_PASSKEY_ORIGIN=https://anon.example.com
export ANON_AUTH_DB=/var/lib/anonimizator3000/auth.sqlite3
```

## Limity

Domyślne limity można zmienić przez zmienne środowiskowe:

| Zmienna | Domyślnie | Znaczenie |
| --- | ---: | --- |
| `ANON_MAX_FILE_BYTES` | `5000000` | Maksymalny rozmiar uploadu |
| `ANON_MAX_TEXT_CHARS` | `250000` | Maksymalna długość wyciągniętego tekstu |
| `ANON_QUEUE_MAX_SIZE` | `20` | Maksymalna liczba zadań w kolejce |
| `ANON_WORKER_COUNT` | `1` | Liczba lokalnych workerów |
| `ANON_MAX_ACTIVE_JOBS_PER_IP` | `2` | Maksymalna liczba aktywnych zadań per IP |
| `ANON_RATE_LIMIT_SUBMISSIONS` | `100` | Liczba uploadów per okno czasowe |
| `ANON_RATE_LIMIT_WINDOW_SECONDS` | `600` | Okno limitu per IP |
| `ANON_JOB_TTL_SECONDS` | `900` | Czas trzymania zakończonego wyniku w pamięci |
| `ANON_TRUST_PROXY_HEADERS` | `false` | Czy ufać `X-Forwarded-For` |
| `ANON_GLINER_ENABLED` | `false` | Włącza GLiNER |
| `ANON_GLINER_MODEL` | `urchade/gliner_multi_pii-v1` | Model GLiNER |
| `ANON_GLINER_THRESHOLD` | `0.45` | Próg predykcji GLiNER |

`ANON_TRUST_PROXY_HEADERS=true` włączaj tylko za reverse proxy, który czyści i ustawia `X-Forwarded-For`.

## Testy

```bash
uv run pytest
uv run ruff check .
```

## Prywatność

- brak zapisu **uploadów dokumentów** na dysku — oryginały i wyniki są tylko w pamięci procesu
- workflow Fali działa na lokalnym store w pamięci procesu
- wynik anonimizacji wygasa po TTL (`ANON_JOB_TTL_SECONDS`)
- **auth jest trwały**: konta, passkey credentials i role leżą w SQLite (`ANON_AUTH_DB`, domyślnie `storage/auth.sqlite3`) — to nie jest baza dokumentów
- odrzucanie za dużych plików przed anonimizacją
- limit aktywnych zadań i rate limit per IP
