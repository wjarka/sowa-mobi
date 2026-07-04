#!/usr/bin/env python3
"""CLI dla SOWA MOBI."""
import sys
from sowa_mobi.sowa_opac import MultiAccountManager, load_config


def main():
    if len(sys.argv) < 2:
        print("""
SOWA MOBI — zarządzanie kontem bibliotecznym

Użycie:
  sowa-mobi summary     — podsumowanie wszystkich kont
  sowa-mobi loans       — tylko wypożyczenia
  sowa-mobi prolong     — prolonguj wszystkie
  sowa-mobi info        — info o koncie

Konfiguracja: ~/.sowa.json
""")
        sys.exit(0)

    cmd = sys.argv[1]
    config = load_config()

    if not config.get("accounts"):
        print("⚠️  Brak kont w ~/.sowa.json")
        sys.exit(1)

    mgr = MultiAccountManager(config["base_url"], config["kat_id"])
    for name, creds in config["accounts"].items():
        ok = mgr.add_account(name, creds["email"], creds["password"])
        print(f"{'✅' if ok else '❌'} {name}")

    if cmd == "summary":
        print(mgr.summary())
    elif cmd == "loans":
        for name, loans in mgr.get_all_loans().items():
            print(f"\n{name}:")
            for loan in loans:
                print(f"  {loan.title[:80]}")
    elif cmd == "prolong":
        results = mgr.prolong_all_all()
        for name, res in results.items():
            print(f"\n{name}:")
            for title, ok, msg in res:
                print(f"  {'✅' if ok else '❌'} {title[:60]} — {msg}")
    elif cmd == "info":
        for name, client in mgr.accounts.items():
            info = client.get_account_info()
            print(f"\n{name}: {info.name} | {info.email} | {info.debt}")

if __name__ == "__main__":
    main()
