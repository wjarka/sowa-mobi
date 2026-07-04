# SOWA MOBI

Klient API dla systemu bibliotecznego SOWA (Sokrates-software).
Zarządzanie kontem czytelnika: wypożyczenia, prolongaty, rezerwacje.

## Instalacja

```bash
uv sync
```

## Konfiguracja

Plik `~/.sowa.json`:

```json
{
  "base_url": "https://www.kornik-bp.sowa.pl",
  "kat_id": 519,
  "accounts": {
    "Wiktor":  {"email": "wiktor@example.com", "password": "***"},
    "Żona":    {"email": "zonka@example.com",  "password": "***"}
  }
}
```

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

client = SowaOPAC("https://www.kornik-bp.sowa.pl", kat_id=519)
client.login("email", "password")
for loan in client.get_loans():
    print(f"{loan.short_title} — do {loan.due_date}")
```
