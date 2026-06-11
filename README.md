# Anonimizator3000

Publiczny portal do lokalnej anonimizacji dokumentów.

Upload trafia wyłącznie do pamięci procesu. Aplikacja nie zapisuje oryginalnych dokumentów na dysku. Oryginał jest usuwany z pamięci zaraz po zakończeniu zadania. Wynik anonimizacji jest trzymany krótko w pamięci, żeby użytkownik mógł go pobrać.

## Architektura

Ten projekt jest tylko portalem/orchestratorem. Nie zawiera własnego ekstraktora
dokumentów, silnika anonimizacji ani runtime'u workflow.

Logika jest w osobnych pakietach pobieranych z GitHub przez `uv`:

- `github.com/mikolaj92/DocToText` - odczyt tekstu z dokumentów i zapis podmienionego tekstu z powrotem do dokumentu.
- `github.com/mikolaj92/Posejdon` - anonimizacja tekstu przez Presidio, regex/walidację PL i opcjonalny GLiNER.
- `github.com/mikolaj92/Fala` - runtime procesu: pipeline, statusy, claimy workerów i event log przetwarzania.
- `src/anonimizator3000` - UI, upload, limity per IP, lokalny worker i integracja trzech pakietów.

`pyproject.toml` wskazuje branche `main`, a `uv.lock` przypina konkretne commity.

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

## CI

GitHub Actions używa tych samych GitHub dependencies co lokalne środowisko.
Ponieważ `Posejdon` jest prywatny, repo `anonimizator3000` musi mieć secret:

- `POSEJDON_READ_TOKEN` - token z read-only dostępem do `mikolaj92/Posejdon`

Jeśli `Posejdon` stanie się publiczny, secret i krok konfiguracji auth w CI można usunąć.

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

## Limity

Domyślne limity można zmienić przez zmienne środowiskowe:

| Zmienna | Domyślnie | Znaczenie |
| --- | ---: | --- |
| `ANON_MAX_FILE_BYTES` | `5000000` | Maksymalny rozmiar uploadu |
| `ANON_MAX_TEXT_CHARS` | `250000` | Maksymalna długość wyciągniętego tekstu |
| `ANON_QUEUE_MAX_SIZE` | `20` | Maksymalna liczba zadań w kolejce |
| `ANON_WORKER_COUNT` | `1` | Liczba lokalnych workerów |
| `ANON_MAX_ACTIVE_JOBS_PER_IP` | `2` | Maksymalna liczba aktywnych zadań per IP |
| `ANON_RATE_LIMIT_SUBMISSIONS` | `6` | Liczba uploadów per okno czasowe |
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

- brak zapisu uploadów na dysku
- brak bazy danych
- workflow Fali działa na lokalnym store w pamięci procesu
- wynik wygasa po TTL
- odrzucanie za dużych plików przed anonimizacją
- limit aktywnych zadań i rate limit per IP
