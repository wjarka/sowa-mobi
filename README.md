# SOWA MOBI

Klient API dla systemu bibliotecznego SOWA (Sokrates-software).
Zarządzanie kontem czytelnika: wypożyczenia, prolongaty, rezerwacje.

## Instalacja

```bash
uv sync
```

## Konfiguracja

Plik `~/.sowa.json` (lub plik wskazany przez `SOWA_CONFIG`):

```json
{
  "base_url": "https://library.example.org",
  "kat_id": 0,
  "accounts": {
    "account_a": {
      "email": "reader@example.org",
      "password": "haslo-do-SOWA",
      "api_token": "dlugi-losowy-token-dla-agenta"
    }
  }
}
```

`base_url` to adres katalogu SOWA, a `kat_id` to identyfikator katalogu
biblioteki używany przez SOWA. Obie wartości są konfigurowalne i nie są
zakodowane w aplikacji.

### Skąd wziąć `base_url` i `kat_id`?

1. Otwórz publiczny katalog swojej biblioteki SOWA.
2. `base_url` ustaw jako sam adres katalogu, bez `/index.php` i bez parametrów,
   na przykład:

   ```text
   https://library.example.org
   ```

3. `kat_id` znajdziesz najczęściej jako parametr `KatID` w adresie strony,
   na przykład:

   ```text
   https://library.example.org/index.php?KatID=123&typ=repl
                                                ^^^
   ```

4. Jeśli adres nie zawiera `KatID`, otwórz stronę logowania katalogu i sprawdź
   kod HTML formularza. SOWA zwykle zawiera tam ukryte pole:

   ```html
   <input type="hidden" name="KatID" value="123">
   ```

   Wartość `123` należy wpisać jako liczbę w konfiguracji.

   ```json
   {
     "base_url": "https://library.example.org",
     "kat_id": 123
   }
   ```

   Niektóre instalacje SOWA po migracji emitują `KatID` dynamicznie na stronie
   logowania. Klient odczytuje tę wartość z formularza i używa jej podczas
   logowania; skonfigurowane `kat_id` pozostaje wartością zapasową.

Konto może opcjonalnie nadpisać bibliotekę własnymi wartościami `base_url` i
`kat_id` — jest to przydatne, gdy konta należą do różnych bibliotek:

```json
"account_b": {
  "email": "other@example.org",
  "password": "haslo-do-SOWA",
  "api_token": "inny-losowy-token",
  "base_url": "https://other-library.example.org",
  "kat_id": 123
}
```

Konfiguracja z `config/sowa.example.json` jest szablonem. Prawdziwy plik
`config/sowa.json` jest ignorowany przez Git i nie powinien być publikowany.


## Użycie

```bash
uv run sowa-mobi summary
uv run sowa-mobi loans
uv run sowa-mobi prolong
uv run sowa-mobi info
```

## Biblioteka

```python
from sowa_mobi import SowaOPAC

client = SowaOPAC("https://library.example.org", kat_id=0)
client.login("email", "password")
for loan in client.get_loans():
    print(f"{loan.short_title} — do {loan.due_date}")
```

## REST API i Docker

### Optional deployment

CI tests and builds the app without any deployment configuration. Deployment
is **disabled by default**, including in forks. Image publication uses
`ghcr.io/<repository-owner>/<repository-name>`; PRs never publish or deploy.
Only a successful default-branch release can deploy, and only when the
repository variable `DEPLOY_ENABLED` is exactly `true`.

Configure these under GitHub **Settings → Secrets and variables → Actions**:

| Setting | Kind | Value / default |
|---|---|---|
| `DEPLOY_ENABLED` | Variable | `true` to opt in; otherwise skipped |
| `DEPLOY_REPOSITORY` | Secret | Target GitOps repository, `owner/repository` |
| `DEPLOY_KEY` | Secret | SSH deploy key with write access to that repository |
| `DEPLOY_BRANCH` | Variable | Target branch; default `main` |
| `DEPLOY_COMPOSE_PATH` | Variable | Relative path; default `stacks/sowa-mobi/compose.yaml` |
| `DEPLOY_RECEIPT_BRANCH` | Variable | Health receipt branch; default `deployment-status/sowa-mobi` |

Existing installations can keep `HOMELAB_DEPLOY_KEY` instead of `DEPLOY_KEY`.
`DEPLOY_REPOSITORY` is a secret so its value is masked in Actions logs; the
workflow and variable names remain public. Deploy keys grant write access
to the entire target repository. No deployment secrets are needed for CI.
Set `DEPLOY_ENABLED=false` to disable deployment without disabling tests or
image publication. Enabling deployment with missing secrets fails clearly.

The target Compose file must contain the app's service names and an image
from this source repository: `sowa-mobi`.
The updater changes only their digest pins and source comments; runtime
settings are preserved. The GitOps controller must deploy changes on the
configured branch, then write `status.json` to the configured receipt branch:

```json
{"status":"healthy","revision":"<full source commit>","image":"ghcr.io/owner/sowa-mobi@sha256:<digest>","verified_at":"<UTC timestamp>"}
```

CI waits up to ten minutes for the exact image digest and revision. The
controller should verify the running containers before publishing a receipt.
The receipt branch must not trigger another deployment. Releases are
serialized, outdated source commits skip promotion, and concurrent target
commits are retried without force-pushing. Rollback by reverting the image
update in the target repository; retain old images and check database
compatibility where applicable.

### Konfiguracja lokalna

API używa osobnego Bearer tokenu dla każdego konta. Token identyfikuje konto,
więc agent nie otrzymuje loginu ani hasła do SOWA i nie może wybrać innego
konta przez parametr URL.

Przygotuj konfigurację:

```bash
mkdir -p config
cp config/sowa.example.json config/sowa.json
openssl rand -hex 32   # wpisz wynik jako api_token pierwszego konta
openssl rand -hex 32   # wpisz wynik jako api_token drugiego konta
chmod 600 config/sowa.json
```

Każde konto w `config/sowa.json` ma postać:

```json
{
  "email": "reader@example.com",
  "password": "haslo-do-SOWA",
  "api_token": "dlugi-losowy-token-dla-agenta"
}
```

Uruchomienie:

```bash
docker compose up -d --build
```

Publiczny healthcheck:

```bash
curl http://localhost:8000/healthz
```

Endpointy wymagające `Authorization: Bearer TOKEN` oraz dokumentacja OpenAPI:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- surowy kontrakt dla agentów: `http://localhost:8000/openapi.json`

Każda odpowiedź JSON zawiera pole `hint`, będące tablicą anglojęzycznych
podpowiedzi dotyczących kolejnego kroku. Dotyczy to również błędów — np. brak
tokenu sugeruje użycie nagłówka Bearer, a nieznany `option_id` sugeruje ponowne
wyszukanie książki.

```bash
TOKEN='token-dla-konkretnego-konta'
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/v1/account
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/v1/loans
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/v1/reservations
```

Wyszukiwanie książek wraz z opcjami rezerwacji:

```bash
curl -G -H "Authorization: Bearer $TOKEN" \
  --data-urlencode 'q=argumentacji' \
  http://localhost:8000/v1/catalog/search
```

Wyszukiwanie jest zawsze wykonywane w katalogu przypisanym do uwierzytelnionego
konta. Endpoint nie przyjmuje `base_url` ani `kat_id` z requestu, więc agent nie
może przełączyć się na inną bibliotekę. Jeśli konto ma własne `base_url` i
`kat_id`, używana jest właśnie ta konfiguracja; w przeciwnym razie używane są
wartości globalne.

Rezerwacje zawierają stabilne pole `state`:

- `queued` — pozycja czeka na zwrot; `queue_pos` jest wypełnione,
- `preparing` — rezerwacja istnieje, ale nie jest jeszcze gotowa do odbioru,
- `ready` — pozycja jest gotowa do odbioru; `ready` ma wartość `true`, a
  `pickup_by` zawiera termin odbioru, jeśli biblioteka go publikuje.

Surowe pole `status` pozostaje w odpowiedzi jako informacja diagnostyczna.

Rezerwacja działa dwuetapowo. Najpierw agent wyszukuje książkę:

```bash
SEARCH=$(curl -fsS -G -H "Authorization: Bearer $TOKEN" \
  --data-urlencode 'q=argumentacji' \
  http://localhost:8000/v1/catalog/search)
```

Wynik zawiera czytelny opis książki, oddział oraz nieprzejrzysty `option_id`.
Agent nie widzi i nie musi znać `idw`, `agenda`, `pickup` ani tokenu CSRF.
Następnie przekazuje wyłącznie `option_id`:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"option_id":"KOR G14042185:0"}' \
  http://localhost:8000/v1/reservations
```

Opcje rezerwacji są przechowywane w pamięci serwisu i wygasają po restarcie
kontenera — w takim przypadku agent wykonuje wyszukiwanie ponownie.

Prolongata konkretnego egzemplarza:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/v1/loans/100062775000/prolong
```

Anulowanie rezerwacji używa `reservation_id` zwróconego przez `/v1/reservations`:

```bash
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/v1/reservations/RESERVATION_ID
```
