# Anonimizator3000

Publiczny portal do lokalnej anonimizacji dokumentów.

## Architektura

Portal obsługuje UI, upload, limity per IP, lokalnego workera i integrację pakietów:

- `github.com/mikolaj92/Docxtor` — odczyt tekstu i zapis dokumentu po anonimizacji.
- `github.com/mikolaj92/Posejdon` — anonimizacja przez Presidio, regex/walidację PL i opcjonalny GLiNER.
- `github.com/mikolaj92/Fala` — pipeline, statusy, claimy workerów i event log.
- `src/anonimizator3000` — kod portalu w FastAPI, HTMX i Basecoat UI.

`pyproject.toml` pinuje źródła Git tagami i rewizjami, nie branchem `main`: aktualny BOM z `COMPAT.md` to `app-factory` `v0.6.22` / `my-auth` `v0.5.4` / `my-usermanager` `v0.6.5`. Osobno przypięto `Docxtor` `v0.14.1` i `posejdon` `v0.2.3`. `uv.lock` utrwala konkretne commity i zależności przechodnie. Ograniczenie `charset-normalizer!=3.4.8` wyklucza wycofane wydanie.

### Przepływ anonimizacji

`pipeline.py` tworzy run Fala i uruchamia graf `DOCUMENT_FLOW`. Każdy węzeł jest adapterem `python_function`. Kontrakty adapterów są typowane w `steps.py`.

| Krok | Wejście z `JobContext` | Wyjście do kontekstu | Metadane Fala |
| --- | --- | --- | --- |
| `convert` | nazwa i MIME | — | zwalidowany typ źródła |
| `load` | nazwa, MIME, bajty, limit znaków | dokument Docxtor | liczba segmentów i znaków |
| `anonymize` | tekst, styl, `SegmentAnonymizer` | zanonimizowane segmenty i findings | liczby findings według kategorii |
| `serialize` | dokument i zanonimizowane segmenty | nazwa, MIME i bajty wyniku | metadane pliku i rozmiar |
| `redact_authors` | bajty wyniku | bajty bez tożsamości autorów | liczba zanonimizowanych autorów |

### Zgodność Fala

`fala-runtime` pozostaje na rewizji `6dd634d18b4812faed04897426bc69322ef59c34`. Pipeline korzysta z CPython carrier API: `fala.models`, `fala.carrier_runtime.FalaRuntime` i `fala.runtime_backend`.

### Klient HTTP

Własny kod i testy używają `import httpx2` oraz typów i wyjątków `httpx2.*`. Nie używają aliasów `as httpx`, shimów `sys.modules` ani fallbacku do `httpx`, `requests` lub `aiohttp`. `fastapi.testclient.TestClient` pozostaje publicznym klientem testowym.

Extra i grupa `dev` deklarują HTTPX2 bezpośrednio. Starsze klienty pozostają zależnościami przechodnimi: Weasel i Hugging Face używają HTTPX, a spaCy i tldextract używają Requests. `spacy-legacy` zapewnia zgodność spaCy, nie transport HTTP.

Bieżące ścieżki zależności z lockfile pokazuje:

```bash
uv run --locked python -m anonimizator3000.dependency_audit
```

## Uruchomienie

`Posejdon` jest prywatnym repozytorium. Instalacja wymaga dozwolonego dostępu Git do `mikolaj92/Posejdon` oraz pozostałych przypiętych źródeł. Blokada pobierania Git uniemożliwia instalację nawet przy zgodnym lockfile.

```bash
git clone https://github.com/mikolaj92/anonimizator3000.git
cd anonimizator3000
uv sync --locked --extra detectors
uv run --locked anonimizator3000
```

Skrypt używa hosta `127.0.0.1`, portu `8000` i automatycznego przeładowania. Nie obsługuje flag `--host`, `--port` ani `--reload`. Otwórz `http://127.0.0.1:8000`.

Inne ustawienia, w tym dostęp w LAN, wymagają bezpośredniego uruchomienia uvicorn:

```bash
uv run --locked uvicorn anonimizator3000.main:app --host 0.0.0.0 --port 8000 --reload
```

## Obsługiwane wejście

Docxtor obsługuje `.pdf` i `.docx`. Wynik zachowuje typ wejścia.

PDF z warstwą tekstową jest modyfikowany przez redakcje na oryginalnych stronach. Liczba stron i grafika zostają zachowane, gdy zmienione fragmenty da się dopasować do tekstu strony. Jeśli fragmentu nie da się bezpiecznie odnaleźć, Docxtor zastępuje tekst tej strony, aby zapobiec wyciekowi. DOCX jest modyfikowany w pamięci; struktura akapitów i tabel zostaje, ale formatowanie wewnątrz akapitu może się uprościć.

## Detekcja

Przypięty `TextAnonymizer` Posejdona (`v0.2.3`) montuje regex i opcjonalny GLiNER. Portal dokłada `PresidioDetector` z pakietu. Brak działającego backendu Presidio zatrzymuje start.

Regex i walidacja obejmują między innymi:

- imiona i nazwiska, także odmiany i kontekst, np. `Jana Kowalskiego`, `Pani Nowak`;
- PESEL, NIP, REGON, polski IBAN/NRB z checksumą;
- dowód osobisty i paszport;
- telefony, e-maile i karty płatnicze z Luhnem;
- datę i miejsce urodzenia z kontekstem;
- adresy uliczne i kody pocztowe;
- szpitale, przychodnie, urzędy, sądy i firmy;
- sygnatury spraw i numery umów;
- IP, MAC, UUID, VIN, JWT oraz bearer/API tokens.

GLiNER jest domyślnie włączony z modelem `urchade/gliner_multi_pii-v1`. Wymaga dodatku `detectors` i cięższych zależności. Wykrywa osoby, organizacje, lokalizacje, adresy, szpitale, urzędy, sprawy i umowy.

Brak modelu lub backendu zatrzymuje start zamiast uruchamiać słabszy stos. Operator może jawnie wyłączyć GLiNER przez `ANON_GLINER_ENABLED=false`.

| Ścieżka | Zachowanie |
| --- | --- |
| Błąd backendu Presidio lub GLiNER | Monitor w `anonymizer.py` wykrywa wyjątek zamieniony przez detektor na pusty wynik i przerywa zadanie. |
| Wybór modelu GLiNER | Portal sprawdza skonfigurowany model; nie wybiera modelu zastępczego. |
| LLM i reviewer | Portal ich nie tworzy; review LLM w `TextAnonymizer` jest wyłączone. |

## Auth (passkeys + usermanager)

Portal używa `my-auth` do passkeys WebAuthn oraz `my-usermanager` do kont, ról, `/account` i `/admin/users`. `install_identity_adapters` montuje chrome, passkey i usermanager raz. Portal zapewnia trwałą bazę, pierwszego admina, invite/RBAC i menu produktu.

Uwierzytelnienie i uprawnienia pochodzą wyłącznie z typed principal `my_usermanager.principal`. Stare `session["user"]` nie uwierzytelnia użytkownika, także w hookach passkey.

`AuthDatabaseBinding` przełącza store'y routerów na ponownie otwartą bazę SQLite podczas lifespan. Powtórzona rejestracja WebAuthn i równoległe nadanie pierwszej roli są obsługiwane idempotentnie.

### Migracja bazy tożsamości

Przed zmianą schematu istniejący `auth.sqlite3` dostaje spójną kopię obok pliku: `auth.sqlite3.pre-migration-<UTC>.sqlite3`. Migracja my-auth i my-usermanager jest jedną transakcją, bez dual-read. Nieznany układ zatrzymuje start.

Rollback: zatrzymaj usługę, zachowaj uszkodzony plik i przywróć nazwę kopii sprzed migracji.

### Polityka rejestracji

Rejestracja jest otwarta przez `/register`. Pierwszy użytkownik dostaje rolę admin. `BOOTSTRAP_ADMIN_ID` / `ANON_BOOTSTRAP_ADMIN_ID` pozwala idempotentnie nadać tę rolę znanemu `user_id` przy starcie.

Przy publicznym wdrożeniu zabezpiecz dostęp przez reverse proxy lub sieć. Portal nie ogranicza samodzielnie publicznej rejestracji.

### Zmienne środowiskowe auth

| Zmienna | Domyślnie | Znaczenie |
| --- | --- | --- |
| `ANON_SESSION_SECRET` | `dev-anon-session-secret-change-me` | Sekret podpisu cookie sesji. **W produkcji ustaw własny, losowy sekret.** |
| `ANON_SESSION_COOKIE` | `anon_session` | Nazwa cookie sesji |
| `ANON_SESSION_COOKIE_SECURE` | `false` | `Secure` na cookie sesji i passkey (`true` za HTTPS) |
| `ANON_SESSION_COOKIE_SAMESITE` | `lax` | `SameSite` cookie sesji; cookies passkey zawsze mają `lax` |
| `ANON_SESSION_MAX_AGE` | `1209600` (14 dni) | Max age sesji w sekundach |
| `ANON_AUTH_DB` | `storage/auth.sqlite3` | Trwała baza SQLite credentials, użytkowników i ról |
| `ANON_PASSKEY_RP_ID` | `localhost` | Domena WebAuthn bez schematu |
| `ANON_PASSKEY_RP_NAME` | `Dokumenty` | Wyświetlana nazwa RP |
| `ANON_PASSKEY_ORIGIN` | `http://localhost:8000` | Dozwolony origin WebAuthn |
| `BOOTSTRAP_ADMIN_ID` / `ANON_BOOTSTRAP_ADMIN_ID` | *(puste)* | Opcjonalny `user_id` admina |

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
| `ANON_MAX_TEXT_CHARS` | `250000` | Maksymalna długość tekstu |
| `ANON_QUEUE_MAX_SIZE` | `20` | Maksymalna liczba zadań w kolejce |
| `ANON_WORKER_COUNT` | `1` | Liczba lokalnych workerów |
| `ANON_MAX_ACTIVE_JOBS_PER_IP` | `2` | Maksymalna liczba aktywnych zadań per IP |
| `ANON_RATE_LIMIT_SUBMISSIONS` | `100` | Liczba uploadów per okno |
| `ANON_RATE_LIMIT_WINDOW_SECONDS` | `600` | Okno limitu per IP |
| `ANON_JOB_TTL_SECONDS` | `900` | Czas trzymania zakończonego wyniku w pamięci |
| `ANON_TRUST_PROXY_HEADERS` | `false` | Czy ufać `X-Forwarded-For` |
| `ANON_GLINER_ENABLED` | `true` | `false` jest jawną zgodą na słabszy stos |
| `ANON_GLINER_MODEL` | `urchade/gliner_multi_pii-v1` | Model; nie może być pusty przy włączonym GLiNER |
| `ANON_GLINER_THRESHOLD` | `0.45` | Próg predykcji z zakresu `(0, 1]` |
| `ANON_REPLACEMENT_STYLE` | `mask` | `mask` daje `****`, `labels` daje np. `[OSOBA_1]` |

UI nie pokazuje selektora stylu. Pole multipart `style` w `POST /jobs` przyjmuje `mask` lub `labels` i nadpisuje ustawienie dla zadania. Brak pola lub niepoprawna wartość oznacza użycie ustawienia środowiskowego.

`ANON_TRUST_PROXY_HEADERS=true` włączaj tylko za reverse proxy, który czyści i ustawia `X-Forwarded-For`.

## Kontrole lokalne

```bash
uv lock --check
uv sync --locked --all-extras --python 3.13.3
uv run --locked pytest
uv run --locked ruff check .
git diff --check
```

`tests/test_dependency_pins.py` sprawdza piny i zgodność ich opisu. `tests/test_http_client_contract.py` pilnuje klienta HTTP we własnym kodzie, testach i manifeście.

## Prywatność

- Bajty i tekst dokumentu są przetwarzane w pamięci procesu w `JobContext`. Oryginał jest usuwany po zakończeniu zadania.
- Wynik pozostaje w pamięci do pobrania lub wygaśnięcia przez `ANON_JOB_TTL_SECONDS`.
- Fala zapisuje metadane workflow w `run.sqlite` w katalogu `anon-run-*` w systemowym katalogu tymczasowym. Nazwa pliku wynikowego i liczby wykryć mogą zawierać dane wrażliwe.
- Katalog workflow jest sprzątany przy normalnym wyjściu z przetwarzania. Usunięcie po awarii nie jest gwarantowane.
- Konta, passkey credentials i role są trwałe w `ANON_AUTH_DB`. To nie jest baza dokumentów.
- Portal odrzuca za duże pliki i ogranicza aktywne zadania oraz liczbę uploadów per IP.
