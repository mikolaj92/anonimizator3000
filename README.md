# Anonimizator3000

Publiczny portal do lokalnej anonimizacji dokumentów.

Bajty i tekst dokumentu są przetwarzane w pamięci procesu w `JobContext`. Fala zapisuje metadane workflow w tymczasowym pliku SQLite `run.sqlite` na dysku. Plik leży w katalogu `anon-run-*` w systemowym katalogu tymczasowym. Katalog jest sprzątany przy normalnym wyjściu z przetwarzania; po awarii usunięcie nie jest gwarantowane. Oryginał jest usuwany z pamięci zaraz po zakończeniu zadania. Wynik anonimizacji jest trzymany krótko w pamięci, żeby użytkownik mógł go pobrać.

Osobno od dokumentów działa trwała baza SQLite platformy auth (passkey / konta użytkowników) — patrz sekcja Auth i zmienna `ANON_AUTH_DB`.

## Architektura

Ten projekt jest tylko portalem/orchestratorem. Nie zawiera własnego ekstraktora
dokumentów, silnika anonimizacji ani runtime'u workflow.

Logika jest w osobnych pakietach pobieranych z GitHub przez `uv`:

- `github.com/mikolaj92/Docxtor` - odczyt tekstu z dokumentów i zapis podmienionego tekstu z powrotem do dokumentu.
- `github.com/mikolaj92/Posejdon` - anonimizacja tekstu przez Presidio, regex/walidację PL i opcjonalny GLiNER.
- `github.com/mikolaj92/Fala` - runtime procesu: pipeline, statusy, claimy workerów i event log przetwarzania.
- `src/anonimizator3000` - UI, upload, limity per IP, lokalny worker i integracja trzech pakietów.

`pyproject.toml` pinuje źródła Git tagami i rewizjami, nie branchem `main`: aktualny BOM z `COMPAT.md` to jedna niemienna generacja `app-factory` `v0.6.22` / `my-auth` `v0.5.4` / `my-usermanager` `v0.6.5`. Osobno `Docxtor` `v0.4.3`, `posejdon` `v0.1.5`, a `fala-runtime` po `rev`. `uv.lock` przypina konkretne commity. Poprzedni zestaw `app-factory` `v0.6.16` / `my-auth` `v0.4.8` / `my-usermanager` `v0.5.11` również odpowiada wierszowi `COMPAT.md` na tagu `app-factory@v0.6.16`.

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

Bajty źródła, tekst dokumentu i bajty wyniku pozostają w pamięci procesu
w `JobContext`; kontekst jest usuwany po zakończeniu zadania. Fala zapisuje
metadane JSON w `run.sqlite` w katalogu `anon-run-*` w systemowym katalogu
tymczasowym. Obejmują one m.in. nazwę pliku wynikowego i liczby wykryć;
mały rozmiar nie oznacza braku danych wrażliwych. Katalog jest sprzątany przy
normalnym wyjściu z przetwarzania, bez gwarancji usunięcia po awarii.
Kontrakty adapterów są typowane w `steps.py`. Nie znaleziono innych przepływów
AI ani luk wymagających osobnych zadań.

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

### Klient HTTP

Własny kod, testy i instrukcje używają bezpośrednio `import httpx2` oraz typów
i wyjątków `httpx2.*`. Nie dodajemy aliasów `as httpx`, shimów `sys.modules`
ani fallbacku importu do legacy `httpx` / `requests` / `aiohttp`.
`fastapi.testclient.TestClient` pozostaje publicznym klientem testowym i nie
wymaga przywracania starego klienta. Extra `dev` deklaruje HTTPX2 bezpośrednio,
ale instaluje także pośredni `httpx`.
Regresje łapie `tests/test_http_client_contract.py`. Legacy `httpx` może zostać
**tranzytywnie** w locku przez biblioteki zewnętrzne; to nie jest przywracanie
klienta we własnym kodzie. Rzeczywiste ścieżki opisuje audyt poniżej.

### Audyt zależności pośrednich

SHA wejściowy audytu: `2e38b0b3634f92acaf06359bc80a91eb1cf27c14`, gałąź
`lokay/59`. Checkout był czysty. Liczniki: **134 pakiety w lockfile,
5 sprawdzonych pakietów starszego stosu, 5 pozostających, 0 usuniętych**.
„Starszy stos” oznacza tu klientów HTTP i warstwę zgodności spaCy, nie ocenę
bezpieczeństwa ani wieku wszystkich 134 pakietów. `aiohttp` nie występuje.

`A` oznacza aplikację. Wszystkie wersje w ścieżkach pochodzą z `uv.lock`:
Posejdon `0.1.1` (tag `v0.1.5`), Presidio `2.2.364`, spaCy `3.8.15`,
Weasel `1.0.0`, GLiNER `0.2.27`, Transformers `5.6.2`, tokenizers `0.22.2`,
huggingface-hub `1.22.0`, tldextract `5.3.2`, requests-file `3.0.1`.

| Pakiet i wersja | Źródło | Pełne ścieżki nadrzędne | Aktywne warianty | Decyzja | Następny krok i warunek zakończenia |
| --- | --- | --- | --- | --- | --- |
| `httpx 0.28.1` | [PyPI](https://pypi.org/pypi/httpx/0.28.1/json) | A → Posejdon → httpx; A → Presidio → spaCy → Weasel → httpx; A → Posejdon[detectors] → GLiNER → huggingface-hub → httpx; A → Posejdon[detectors] → GLiNER → Transformers → huggingface-hub → httpx; A → Posejdon[detectors] → GLiNER → Transformers → tokenizers → huggingface-hub → httpx | Pierwsze dwie: wszystkie. Ostatnie trzy: extra dev i detectors. | KEEP; usunąć bezpośrednią krawędź Posejdona, nie deklarować usunięcia pakietu. | Właściciele: mikolaj92/Posejdon, Explosion/Weasel, Hugging Face/huggingface-hub. Zweryfikowany kandydat Posejdon v0.2.3; pozostałe migracje wymagają wydań rodziców bez HTTPX. Bramka: regeneracja locka, cały pytest i ruff; brak każdej krawędzi w ponownym audycie. |
| `httpcore 1.0.9` | [PyPI](https://pypi.org/pypi/httpcore/1.0.9/json) | Każda powyższa ścieżka HTTPX → httpcore | Wszystkie; ścieżki Hugging Face tylko z extra dev/detectors. | KEEP; nie zastępować przez override nazwą HTTPX2. | Właściciel: Encode/HTTPX. Usunąć po migracji wszystkich trzech rodziców HTTPX. Bramka: brak httpx i httpcore w aktywnych eksportach oraz zielony transport, błędy backendów i pełny suite. |
| `requests 2.34.2` | [PyPI](https://pypi.org/pypi/requests/2.34.2/json) | A → Presidio → spaCy → requests; A → Presidio → tldextract → requests; A → Presidio → tldextract → requests-file → requests | Wszystkie | KEEP; zachować Presidio i jego detekcję. | Właściciele rodziców: Explosion/spaCy, john-kurkowski/tldextract, dashea/requests-file; klient: PSF/Requests. Migrować spaCy i tldextract razem do wydań bez Requests, także adaptera requests-file. Bramka: brak trzech krawędzi, poprawne rozpoznawanie domen, Presidio i dokumenty wynikowe w pełnym suite. |
| `urllib3 2.7.0` | [PyPI](https://pypi.org/pypi/urllib3/2.7.0/json) | Każda powyższa ścieżka Requests → urllib3 | Wszystkie | KEEP; obecne 2.x nie oznacza starego pinu 1.x. | Właściciel: urllib3; rodzic: PSF/Requests. Usunąć dopiero po migracji wszystkich ścieżek Requests. Bramka: brak urllib3 w aktywnych eksportach i pełny suite, bez zmiany transportu na niezgodny pakiet. |
| `spacy-legacy 3.0.12` | [PyPI](https://pypi.org/pypi/spacy-legacy/3.0.12/json) | A → Presidio → spaCy → spacy-legacy | Wszystkie | KEEP; biblioteka zgodności modelu, nie klient HTTP. | Właściciel: Explosion/spaCy. Aktualizować spaCy wraz z kompatybilnym en-core-web-lg dopiero gdy rodzic nie wymaga spacy-legacy. Bramka: ładowanie modelu, Presidio, detekcja PII i pełny suite dokumentów; brak krawędzi. |

Nie potwierdzono wydań eliminujących krawędzie Weasel, Hugging Face, spaCy
ani tldextract. Nie przypisujemy im wymyślonych tagów ani terminów.
Fala pozostaje na dotychczasowej rewizji i nie jest rodzicem tych pakietów.

Metadane przypiętych, zainstalowanych dystrybucji (`Requires-Dist`) potwierdzają:
Posejdon `httpx>=0.28.1`; Weasel `httpx>=0.24.0`;
huggingface-hub `httpx>=0.23.0,<1`; HTTPX `httpcore==1.*`;
spaCy `requests>=2.13.0,<3` i `spacy-legacy>=3.0.11,<3.1.0`;
tldextract `requests>=2.1.0`, `requests-file>=1.4`;
requests-file `requests>=1.0.0`; Requests `urllib3>=1.26,<3`.
Te krawędzie nie mają markerów platformy. GLiNER aktywuje wyłącznie extra
`posejdon[detectors]`; ten extra włącza zarówno extra aplikacji `dev`, jak
`detectors`, ale nie grupa `dev`. Opcjonalne zależności testowe Hugging Face
nie są aktywne. Marker architektury `hf-xet` nie zmienia ścieżek HTTPX.
Model `en-core-web-lg 3.8.0` w tym przypiętym wheelu nie deklaruje spaCy;
ścieżka do spaCy prowadzi przez Presidio, nie przez ten model.

Zweryfikowano zdalne tagi Posejdona bez odczytu tokenów: `v0.1.5` rozwiązuje
się do `714301d3e55cd5459b1c7a06b25aa37cedb8583a`, zgodnie z lockfile.
Kandydat `v0.2.3` wskazuje `6aefba61679d5dc5028c7a48eaf741acede4db00`.
Jego manifest usuwa bezpośredni HTTPX i zachowuje opcjonalny GLiNER;
źródła zachowują `TextAnonymizer` oraz `PresidioDetector`.
Próba `uv lock` dla tego taga przekroczyła limit czasu podczas aktualizacji
źródeł Git. To **blokada aktualizacji**, nie dowód niezgodności ani sukces.
Zachowano produkcyjny pin v0.1.5 i niezmieniony lockfile.

#### Odtworzenie i bramki

Audyt wykonano na macOS arm64, Python 3.13.3, uv 0.11.20.
Ta wersja `uv tree` nie obsługuje `--extra`; użyto `uv export --locked`.
Eksport jest uniwersalny: markery trzeba stosować do środowiska docelowego.
Liczniki poniżej liczą wpisy registry/wheel `nazwa==wersja`, nie źródła Git
ani projekt editable; nie są liczbą zainstalowanych pakietów na każdej platformie.

| Wariant | Polecenie eksportu | Wpisy registry/wheel | Starszy stos |
| --- | --- | ---: | --- |
| Podstawowy | `uv export --locked --no-dev --no-hashes` | 82 | 5/5; bez huggingface-hub i HTTPX2 |
| Grupa dev (domyślna) | `uv export --locked --group dev --no-hashes` | 91 | 5/5; HTTPX2 obok HTTPX |
| Extra dev + domyślna grupa dev | `uv export --locked --extra dev --no-hashes` | 126 | 5/5; także ścieżki Hugging Face |
| Extra detectors + domyślna grupa dev | `uv export --locked --extra detectors --no-hashes` | 125 | 5/5; także ścieżki Hugging Face |

`uv tree --locked`, `--no-dev`, `--group dev` i odwrócone drzewa
`--invert --package httpx/requests/urllib3/spacy-legacy --universal`
potwierdziły rodziców. Poniższe polecenie czyta bieżący lockfile i zwraca
JSON wszystkich ścieżek, także alternatywnych wejść przez dodatki i grupy.
Zachowuje markery i nie udaje instalacji opcjonalnych zależności:

```bash
uv run --locked python -m anonimizator3000.dependency_audit
```

Przed zmianą: `uv lock --check` OK, `uv run --locked pytest` **91 passed**,
`uv run --locked ruff check .` OK. Instalacje `uv sync --locked --group dev`,
`--extra dev` oraz `--extra detectors` przeszły dla zachowanego pinu;
prywatny Posejdon był dostępny. Po przełączeniu z extra dev na detectors
pełny pytest dał **90 passed, 1 failed**: Posejdon wymaga obecności `pip`
przy inicjalizacji Presidio, a pip jest tylko w extra dev. To znany podczas
audytu problem wariantu podstawowego/detectors, nie sukces runtime.
Najmniejsza następna naprawa: przenieść `pip>=25.0` do zależności podstawowych
lub usunąć bramkę pip w Posejdonie; wymaga regeneracji locka, która tutaj
pozostała zablokowana aktualizacją źródeł Git. Nie osłabiono detektora.
Po końcowym `uv sync --locked --extra dev`: kontrola locka OK,
testy pinów **7 passed**, pełny pytest **91 passed**, ruff OK.
Testy pinów pozostają bez zmian,
ponieważ wybrane piny się nie zmieniły. Nie dodano testów jednostkowych.

Checkout nie zawiera `.github/workflows` ani innego śledzonego pliku CI.
Publiczne API GitHub zwróciło 0 check runs dla wejściowego SHA;
odczyt ochrony gałęzi zwrócił HTTP 401. **Wymagane kontrole PR i CI końcowego
SHA pozostają niezweryfikowane.** Wynik lokalny nie zastępuje CI.

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
uv run anonimizator3000
```

Skrypt używa stałych ustawień: host `127.0.0.1`, port `8000` i włączone automatyczne przeładowanie.
Skrypt `anonimizator3000` nie obsługuje flag `--host`, `--port` ani `--reload`; inne ustawienia wymagają bezpośredniego uruchomienia uvicorn.

Potem otwórz `http://127.0.0.1:8000`.

Żeby wystawić aplikację w LAN, uruchom bezpośrednio uvicorn:

```bash
uv run uvicorn anonimizator3000.main:app --host 0.0.0.0 --port 8000 --reload
```

## Obsługiwane wejście

- `.pdf` przez `Docxtor`
- `.docx` przez `Docxtor`

Wynik zachowuje typ wejścia:

- PDF -> PDF
- DOCX -> DOCX

PDF z warstwą tekstową jest modyfikowany przez redakcje na oryginalnych stronach, więc liczba stron i grafika dokumentu zostają zachowane, gdy zmienione fragmenty da się dopasować do tekstu strony. Jeśli fragmentu nie da się bezpiecznie odnaleźć, `Docxtor` zamyka ryzyko wycieku przez zastąpienie tekstu tej strony. DOCX jest modyfikowany w pamięci przez `Docxtor`; struktura akapitów i tabel zostaje, ale formatowanie w ramach jednego akapitu może się uprościć.

## Detekcja

Warstwy są w `Posejdon`:

1. Presidio jako pipeline wykrywania i anonimizacji. Przypięty `TextAnonymizer` (`v0.1.5`) montuje regex i opcjonalny GLiNER; host sam dokłada `PresidioDetector` z pakietu, bo kontrakt portalu nadal go wymaga. Brak działającego backendu Presidio zatrzymuje start.
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
| Inicjalizacja detektorów Posejdon | Przypięty `TextAnonymizer` nie montuje Presidio; host dokłada `PresidioDetector` i przerywa start, gdy detektor albo backend nie są dostępne. GLiNER nadal jest wymagany tylko gdy włączony. |
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

Portal używa wspólnego stacku platformy: `my-auth` (passkeys WebAuthn) i `my-usermanager` (konta, role, `/account`, `/admin/users`). Chrome, passkey i usermanager montuje `install_identity_adapters` raz; host zostawia persistence, first-user admin, invite/RBAC i menu produktu.

### Polityka rejestracji

Rejestracja jest **otwarta** — każdy może założyć konto passkey przez `/register`. Pierwszy zarejestrowany użytkownik dostaje rolę admin. Dodatkowo można wymusić admina dla znanego `user_id` przez `BOOTSTRAP_ADMIN_ID` / `ANON_BOOTSTRAP_ADMIN_ID` (idempotentnie przy starcie).

To celowa polityka produktu (lokalny / self-hosted portal), nie bug. Jeśli wdrażasz publicznie, zabezpiecz reverse proxy / sieć albo wyłącz publiczną rejestrację w przyszłej konfiguracji.

### Zmienne środowiskowe auth

| Zmienna | Domyślnie | Znaczenie |
| --- | --- | --- |
| `ANON_SESSION_SECRET` | `dev-anon-session-secret-change-me` | Sekret podpisu cookie sesji (Starlette). **W produkcji ustaw własny, losowy sekret.** |
| `ANON_SESSION_COOKIE` | `anon_session` | Nazwa cookie sesji |
| `ANON_SESSION_COOKIE_SECURE` | `false` | `Secure` na cookie sesji i passkey cookies (`true` za HTTPS) |
| `ANON_SESSION_COOKIE_SAMESITE` | `lax` | `SameSite` wyłącznie cookie sesji; cookies passkey zawsze mają `SameSite=lax`, niezależnie od tej zmiennej |
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
| `ANON_GLINER_THRESHOLD` | `0.45` | Próg predykcji GLiNER z zakresu `(0, 1]` |
| `ANON_REPLACEMENT_STYLE` | `mask` | Styl anonimizacji: `mask` daje `****`, a `labels` etykiety, np. `[OSOBA_1]` |

UI nie pokazuje selektora stylu. Pole multipart `style` w `POST /jobs` przyjmuje `mask` lub `labels` i nadpisuje ustawienie środowiskowe dla danego zadania. Brak pola lub niepoprawna wartość oznacza użycie ustawienia środowiskowego.

`ANON_TRUST_PROXY_HEADERS=true` włączaj tylko za reverse proxy, który czyści i ustawia `X-Forwarded-For`.

## Testy

```bash
uv run --extra dev pytest
uv run --extra dev ruff check .
```

`uv run --extra dev pytest tests/test_http_client_contract.py` pilnuje, żeby
kod, testy i manifest nie wróciły do legacy klienta HTTP.

## Prywatność

- bajty i tekst dokumentu są przetwarzane w pamięci procesu w `JobContext`; wynik pozostaje w pamięci do pobrania lub wygaśnięcia
- metadane workflow Fali, w tym nazwa pliku wynikowego i liczby wykryć, trafiają na dysk do `run.sqlite` w katalogu `anon-run-*` w systemowym katalogu tymczasowym; mogą zawierać dane wrażliwe
- katalog workflow jest sprzątany przy normalnym wyjściu z przetwarzania; usunięcie po awarii nie jest gwarantowane
- wynik anonimizacji wygasa po TTL (`ANON_JOB_TTL_SECONDS`)
- **auth jest trwały**: konta, passkey credentials i role leżą w SQLite (`ANON_AUTH_DB`, domyślnie `storage/auth.sqlite3`) — to nie jest baza dokumentów
- odrzucanie za dużych plików przed anonimizacją
- limit aktywnych zadań i rate limit per IP
