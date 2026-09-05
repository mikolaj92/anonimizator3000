# Anonimizator3000

Publiczny portal do lokalnej anonimizacji dokumentów.

Upload dokumentów trafia wyłącznie do pamięci procesu. Aplikacja nie zapisuje oryginalnych dokumentów na dysku. Oryginał jest usuwany z pamięci zaraz po zakończeniu zadania. Wynik anonimizacji jest trzymany krótko w pamięci, żeby użytkownik mógł go pobrać.

Osobno od dokumentów działa trwała baza SQLite platformy auth (passkey / konta użytkowników) — patrz sekcja Auth i zmienna `ANON_AUTH_DB`.

## Architektura

Ten projekt jest tylko portalem/orchestratorem. Nie zawiera własnego ekstraktora
dokumentów, silnika anonimizacji ani runtime'u workflow.

Logika jest w osobnych pakietach pobieranych z GitHub przez `uv`:

- `github.com/mikolaj92/Docxtor` - odczyt tekstu z dokumentów i zapis podmienionego tekstu z powrotem do dokumentu.
- `github.com/mikolaj92/Posejdon` - anonimizacja tekstu przez Presidio, regex/walidację PL i opcjonalny GLiNER.
- `github.com/mikolaj92/Fala` - runtime procesu: pipeline, statusy, claimy workerów i event log przetwarzania.
- `src/anonimizator3000` - UI, upload, limity per IP, lokalny worker i integracja trzech pakietów.

`pyproject.toml` pinuje źródła Git tagami i rewizjami, nie branchem `main`: aktualny BOM `app-factory` `v0.6.22`, `my-auth` `v0.5.4`, `my-usermanager` `v0.6.5`, `Docxtor` `v0.4.3`, `posejdon` `v0.1.5`, a `fala-runtime` po `rev`. `uv.lock` przypina konkretne commity.

### Przepływy AI / analizy (issue #23)

Aplikacja ma jeden przepływ analizy: anonimizację dokumentu. `pipeline.py` nie
wykonuje jej jako monolitycznego agenta; tworzy run w przypiętym runtime Fala i
uruchamia graf `DOCUMENT_FLOW`. Każdy węzeł jest osobnym adapterem
`python_function`, a zależności ustalają kolejność:

| Krok | Wejście ze współdzielonego `JobContext` | Wyjście do kontekstu | Małe wyjście rejestrowane przez Fala |
| --- | --- | --- | --- |
| `convert` | nazwa i MIME | — | zwalidowany typ źródła |
| `load` | nazwa, MIME, bajty źródłowe, limit znaków | sparsowany dokument Docxtor | liczba segmentów i znaków |
| `anonymize` | tekst dokumentu, styl, `SegmentAnonymizer` | zanonimizowane segmenty i findings | liczby findings według kategorii |
| `serialize` | dokument i zanonimizowane segmenty | nazwa, MIME i bajty wyniku w formacie wejściowym | metadane pliku i rozmiar |
| `redact_authors` | bajty wyniku | bajty bez tożsamości autorów | liczba zanonimizowanych autorów |

Ciężkie lub wrażliwe dane pozostają tylko w pamięci procesu i są usuwane po
runie; przez magazyn Fala przechodzą wyłącznie małe metadane JSON. Kontrakty
adapterów są typowane w `steps.py`. Nie znaleziono innych przepływów AI ani luk
wymagających osobnych zadań.

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
- Docxtor
- Posejdon
- Fala


### Migracja bazy tożsamości

Przed zmianą schematu istniejący `auth.sqlite3` dostaje spójną kopię obok pliku:
`auth.sqlite3.pre-migration-<UTC>.sqlite3`. Migracja my-auth i my-usermanager jest
jedną transakcją i nie uruchamia dual-read. Nieznany układ nadal zatrzymuje start.
Rollback: zatrzymaj usługę, zachowaj uszkodzony plik jako dowód i przywróć nazwę
kopii sprzed migracji.

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
- `.pdf` przez `Docxtor`
- `.docx` przez `Docxtor`

Wynik zachowuje typ wejścia:

- PDF -> PDF
- DOCX -> DOCX
- tekst -> TXT

PDF z warstwą tekstową jest modyfikowany przez redakcje na oryginalnych stronach, więc liczba stron i grafika dokumentu zostają zachowane, gdy zmienione fragmenty da się dopasować do tekstu strony. Jeśli fragmentu nie da się bezpiecznie odnaleźć, `Docxtor` zamyka ryzyko wycieku przez zastąpienie tekstu tej strony. DOCX jest modyfikowany w pamięci przez `Docxtor`; struktura akapitów i tabel zostaje, ale formatowanie w ramach jednego akapitu może się uprościć.

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

GLiNER jest domyślnie włączony z modelem `urchade/gliner_multi_pii-v1` i wymaga
cięższych zależności:

```bash
uv sync --extra detectors
uv run uvicorn anonimizator3000.main:app --reload
```

Brak modelu lub backendu zatrzymuje start aplikacji zamiast uruchamiać słabszy stos.
Operator może jawnie wyłączyć GLiNER przez `ANON_GLINER_ENABLED=false`.

### Audyt cichych fallbacków AI (issue #21)

| Ścieżka | Wynik audytu / zabezpieczenie |
| --- | --- |
| Inicjalizacja detektorów Posejdon | Posejdon pomija błędy Presidio i GLiNER; `create_anonymizer` wymaga całego skonfigurowanego stosu i przerywa start przy braku detektora lub backendu. |
| Wywołania backendów Presidio i GLiNER | Detektory Posejdon zamieniają wyjątek backendu na pusty wynik; monitor w `anonymizer.py` wykrywa ten przypadek i przerywa zadanie. |
| Wybór/fallback modelu GLiNER | Aplikacja przekazuje wyłącznie skonfigurowany model i sprawdza jego dostępność; nie wybiera modelu zastępczego. |
| Klient LLM i reviewer | Aplikacja ich nie tworzy, a kompatybilny `TextAnonymizer` Posejdona ma review LLM wyłączone; brak ścieżki do naprawy w tym repozytorium. |
| Syntetyczny sukces AI | Nie znaleziono; wynik powstaje tylko po wykonaniu detektorów, a ich błędy kończą zadanie błędem. |

### Inwentaryzacja rzadkich ścieżek zgodności (issue #22)

| Symbol / ścieżka | Decyzja |
| --- | --- |
| `AnonUserManagerHooks.get_current_user`: odczyt starego `session["user"]`, gdy brak typed principal | **Usunięto.** Logowanie zapisuje `my_usermanager.principal`; stara sesja nie uwierzytelnia już użytkownika. |
| `AnonUserManagerHooks.require_admin`: autoryzacja przez stare `session["user"]["is_admin"]` | **Usunięto.** Uprawnienia pochodzą wyłącznie z typed principal. |
| Hooki passkey `get_session_user` / `registration_allowed`: identyfikacja przez stare `session["user"]` | **Usunięto.** Warstwa passkey rozpoznaje zalogowanego użytkownika wyłącznie przez typed principal; stara sesja jest traktowana jak anonimowa. |
| `AuthDatabaseBinding` i proxy store'ów | **Promowane.** To jawna granica lifecycle'u: routery są instalowane raz, a lifespan przełącza je na ponownie otwartą bazę SQLite; pokrywają ją testy tras auth. |
| `_complete_registration`: obsługa `DuplicateUserError` / `DuplicateGrantError` | **Promowane.** To idempotencja powtórzonej ceremonii WebAuthn i równoległego nadania roli pierwszemu użytkownikowi, nie zgodność wsteczna. |
| `Fala` CPython carrier API | **Pozostaje jawną funkcją produktu.** Migracja jednorazowa do nowego host/sdk wymaga osobnego projektu; granica i przypięta rewizja są opisane wyżej. |
| Konwersja PDF bez bezpiecznego dopasowania redakcji | **Promowana.** Fail-closed zastępuje tekst strony, aby nie ujawnić PII; zachowanie należy do `Docxtor` i jest opisane w „Obsługiwane wejście”. |

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
| `ANON_GLINER_ENABLED` | `true` | Włącza GLiNER; `false` jest jawną zgodą na słabszy stos |
| `ANON_GLINER_MODEL` | `urchade/gliner_multi_pii-v1` | Model GLiNER; nie może być pusty, gdy GLiNER jest włączony |
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
