#!/usr/bin/env python3
"""
SOWA OPAC Client — zarządzanie kontem bibliotecznym przez web OPAC.
Działa z każdą biblioteką używającą systemu SOWA (Sokrates-software).

Author: Hermes Agent dla Wiktora
Repository: this project

Usage:
    from sowa_opac import SowaOPAC, MultiAccountManager

    # Single account
    client = SowaOPAC("https://library.example.org", kat_id=0)
    client.login("reader@example.org", "password")
    for loan in client.get_loans():
        print(f"{loan.title} — do {loan.due_date}")
    client.prolong(loan)

    # Wiele kont
    mgr = MultiAccountManager("https://library.example.org", 0)
    mgr.add_account("account_a", "reader@example.org", "password")
    mgr.add_account("account_b", "other@example.org", "password")
    print(mgr.summary())
"""

import re
import json
import os
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


# =============================================================================
# Data classes
# =============================================================================

@dataclass
class Loan:
    """Wypożyczona książka"""
    title: str = ""              # tytuł + autor + opis bibliograficzny
    author: str = ""
    due_date: str = ""           # termin zwrotu (DD.MM.YYYY)
    loan_date: str = ""          # data wypożyczenia
    branch: str = ""             # oddział (np. "Wyp. w Bninie")
    copy_id: str = ""            # nr egzemplarza (sn — do prolongaty)
    rec_no: str = ""             # id rekordu w systemie
    # Prolongata:
    _prolong_csrf: str = ""
    _prolong_url: str = ""
    can_prolong: bool = False

    @property
    def short_title(self) -> str:
        """Pierwsza linia opisu = tytuł/autor"""
        return self.title.split("/")[0].strip() if self.title else ""


@dataclass
class Reservation:
    """Rezerwacja / zamówienie oczekujące"""
    title: str = ""
    status: str = ""
    queue_pos: str = ""
    expire_date: str = ""
    reservation_id: str = ""
    _cancel_url: str = ""
    _cancel_data: dict = field(default_factory=dict)


@dataclass
class AccountInfo:
    """Informacje o koncie czytelnika"""
    name: str = ""
    card_number: str = ""
    email: str = ""
    phone: str = ""
    debt: str = ""
    address: str = ""
    birth_date: str = ""


# =============================================================================
# Main client
# =============================================================================

class SowaOPAC:
    """
    Klient web OPAC dla systemu SOWA (Sokrates-software).
    
    Obsługuje: logowanie, wypożyczenia, prolongaty, rezerwacje, 
    historia zwrotów, rozliczenia, info konta.
    """

    def __init__(self, base_url: str, kat_id: int):
        """
        Args:
            base_url: URL katalogu SOWA, np. "https://library.example.org"
            kat_id: ID katalogu (KatID w URLach). Znajdziesz w URL na stronie biblioteki.
                    Wartość zależy od konfiguracji biblioteki.
        """
        self.base_url = base_url.rstrip("/")
        self.kat_id = kat_id
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        self.logged_in = False
        self._csrf_token = None
        self._info = None

    def _full_url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return urljoin(self.base_url + "/", path.lstrip("/"))

    def _get(self, path: str, **kw) -> requests.Response:
        url = self._full_url(path)
        r = self.session.get(url, **kw)
        r.raise_for_status()
        return r

    def _post(self, path: str, **kw) -> requests.Response:
        url = self._full_url(path)
        r = self.session.post(url, **kw)
        r.raise_for_status()
        return r

    # -------------------------------------------------------------------------
    # Login / Logout
    # -------------------------------------------------------------------------

    def _get_csrf_token(self, html: str) -> Optional[str]:
        """Wyciąga token fwrqpid (CSRF) ze strony logowania"""
        match = re.search(r'name="fwrqpid"\s+value="([^"]+)"', html)
        return match.group(1) if match else None

    def login(self, email: str, password: str) -> bool:
        """
        Loguje się do konta czytelnika.
        
        Returns: True jeśli zalogowano pomyślnie
        """
        # SOWA currently serves the login form under the explicit login route.
        # The older KatID+typ=acc URL is redirected to a malformed query
        # (e.g. index.php?0&acc) and therefore contains no CSRF form token.
        login_page = "index.php?typ=acc&id=login"
        r = self._get(login_page)
        csrf = self._get_csrf_token(r.text)
        if not csrf:
            raise RuntimeError("Nie udało się pobrać tokenu CSRF (fwrqpid)")

        # Use the KatID emitted by the current login form.  The configured
        # catalog ID may be stale after a SOWA migration (Kórnik now emits 0).
        soup = BeautifulSoup(r.text, "html.parser")
        kat_input = soup.find("input", {"name": "KatID"})
        active_kat_id = kat_input.get("value", str(self.kat_id)) if kat_input else str(self.kat_id)
        data = {
            "KatID": active_kat_id,
            "swww_user": email,
            "swww_pass": password,
            "swww_stay": "Y",
            "login_attempt": "1",
            "sv": "1",
            "last_visit_uri": "",
            "fwrqpid": csrf,
        }
        r = self._post("index.php?typ=acc", data=data)
        self._active_kat_id = active_kat_id

        # Sprawdź czy zalogowano: brak formularza logowania + brak error-msg
        soup = BeautifulSoup(r.text, "html.parser")
        login_form = soup.find("form", {"name": "log"})
        error = soup.find(class_="error-msg")
        if login_form or error:
            self.logged_in = False
            return False

        self.logged_in = True
        return True

    def logout(self):
        """Wylogowuje"""
        try:
            self._get(f"index.php?KatID={self.kat_id}&typ=acc&id=logout")
        except Exception:
            pass
        self.logged_in = False
        self.session.cookies.clear()

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _fetch_tab(self, tab: str) -> BeautifulSoup:
        """
        Pobiera zawartość zakładki konta.
        tab: 'onloan', 'reserved', 'returned', 'billing', 'queries', 'info'
        """
        if not self.logged_in:
            raise RuntimeError("Nie jesteś zalogowany. Wywołaj login() najpierw.")
        # Account pages use the authenticated session's active KatID.  Passing
        # the old configured KatID can redirect to a public catalog page.
        r = self._get(f"index.php?typ=acc&id={tab}")
        return BeautifulSoup(r.text, "html.parser")

    @staticmethod
    def _parse_date(text: str, prefix: str) -> str:
        """Wyciąga datę z tekstu np. 'Termin zwrotu:05.08.2026.' -> '05.08.2026'"""
        match = re.search(rf'{prefix}\s*:?\s*(\d{{2}}\.\d{{2}}\.\d{{4}})', text)
        return match.group(1) if match else ""

    # -------------------------------------------------------------------------
    # Loans
    # -------------------------------------------------------------------------

    def get_loans(self) -> list[Loan]:
        """Zwraca listę aktualnie wypożyczonych książek."""
        soup = self._fetch_tab("onloan")
        loans = []

        results = soup.find("div", id="results-panel")
        if not results:
            return loans

        for record in results.find_all("div", class_="record-details"):
            loan = Loan()

            # Opis bibliograficzny
            meta = record.find("div", class_="record-meta")
            if meta:
                loan.title = meta.get_text(separator=" ", strip=True)
                # Autor = część po /
                if "/" in loan.title:
                    parts = loan.title.split("/")
                    loan.short = parts[0].strip()
                    loan.author = parts[1].split(";")[0].strip() if len(parts) > 1 else ""

            # Info o wypożyczeniu (oddział, daty, nr egzemplarza)
            info = record.find("div", class_="record-info-meta")
            if info:
                info_text = info.get_text(separator=" ", strip=True)
                # Branch: [Wyp. w Bninie]
                branch_match = re.search(r'\[([^\]]+)\]', info_text)
                if branch_match:
                    loan.branch = branch_match.group(1)
                loan.loan_date = self._parse_date(info_text, "Wypożyczono")
                loan.due_date = self._parse_date(info_text, "Termin zwrotu")
                # Egzemplarz: 110-052539-00-0
                copy_match = re.search(r'Egzemplarz\s*:?\s*([\d-]+)', info_text)
                if copy_match:
                    loan.copy_id = copy_match.group(1)

            # Formularz prolongaty
            buttons = record.find("div", class_="record-buttons")
            if buttons:
                form = buttons.find("form")
                if form:
                    csrf = form.find("input", {"name": "csrf_token"})
                    sn = form.find("input", {"name": "sn"})
                    lendop = form.find("input", {"name": "lendop"})
                    if csrf and sn:
                        loan._prolong_csrf = csrf.get("value", "")
                        loan._prolong_url = form.get("action", "")
                        loan.copy_id = sn.get("value", "")
                        loan.can_prolong = True

            loans.append(loan)

        return loans

    def prolong(self, loan: Loan) -> tuple[bool, str]:
        """
        Prolonguje (przedłuża) wypożyczenie.
        
        Returns: (sukces, komunikat)
        """
        if not loan.can_prolong:
            return False, "Ta pozycja nie może być prolongowana"

        url = self._full_url(loan._prolong_url) if loan._prolong_url else \
              f"index.php?KatID={self.kat_id}&typ=acc&id=onloan"

        data = {
            "csrf_token": loan._prolong_csrf,
            "lendop": "prolong-lending",
            "sn": loan.copy_id,
        }
        r = self._post(url, data=data)
        
        # Parsuj odpowiedź
        soup = BeautifulSoup(r.text, "html.parser")
        
        # Szukaj komunikatu
        msg_div = soup.find(class_=re.compile(r"message|info|success|error|alert", re.I))
        if msg_div:
            msg = msg_div.get_text(strip=True)
            ok = "błąd" not in msg.lower() and "nie" not in msg.lower()[:10]
            return ok, msg

        # Jeśli brak formularza prolongaty = sukces (już prolongowane)
        return True, "Prolongata wykonana"

    def prolong_all(self) -> list[tuple[str, bool, str]]:
        """
        Prolonguje wszystkie możliwe wypożyczenia.
        
        Returns: lista (tytuł, sukces, komunikat)
        """
        results = []
        for loan in self.get_loans():
            if loan.can_prolong:
                ok, msg = self.prolong(loan)
                title = loan.short if hasattr(loan, 'short') else loan.title[:50]
                results.append((title, ok, msg))
        return results

    def prolong_by_copy_id(self, copy_id: str) -> tuple[bool, str]:
        """Prolong the loan identified by its copy number."""
        for loan in self.get_loans():
            if loan.copy_id == copy_id:
                return self.prolong(loan)
        return False, "Nie znaleziono wypożyczenia o podanym numerze egzemplarza"

    # -------------------------------------------------------------------------
    # Reservations
    # -------------------------------------------------------------------------

    def get_reservations(self) -> list[Reservation]:
        """Zwraca listę oczekujących rezerwacji / zamówień."""
        soup = self._fetch_tab("reserved")
        reservations = []

        results = soup.find("div", id="results-panel")
        if not results:
            return reservations

        for record in results.find_all("div", class_="record-details"):
            res = Reservation()
            meta = record.find("div", class_="record-meta")
            if meta:
                res.title = meta.get_text(separator=" ", strip=True)
            info = record.find("div", class_="record-info-meta")
            if info:
                res.status = info.get_text(separator=" ", strip=True)[:200]
            form = record.find("form")
            if form:
                res._cancel_url = form.get("action", "")
                for field_name in ("sn", "id"):
                    field = form.find("input", {"name": field_name})
                    if field and field.get("value"):
                        res.reservation_id = field.get("value")
                        break
                res._cancel_data = {
                    field.get("name"): field.get("value", "")
                    for field in form.find_all("input")
                    if field.get("name")
                }
            reservations.append(res)

        return reservations

    def reserve(self, idw: str, agenda: str, pickup: str, csrf_token: str) -> tuple[bool, str]:
        """Place a reservation using values from a SOWA catalog result form."""
        data = {
            "id": "reserved",
            "csrf_token": csrf_token,
            "lendop": "order",
            "idw": idw,
            "agenda": agenda,
            "pickups": pickup,
        }
        kat_id = getattr(self, "_active_kat_id", str(self.kat_id))
        response = self._post(f"index.php?KatID={kat_id}&typ=acc", data=data)
        return self._action_result(response.text, "Rezerwacja wykonana")

    def search_catalog(self, query: str) -> list[dict]:
        """Search public catalog and return reservation options for each hit."""
        response = self.session.post(
            self._full_url("index.php"),
            data={"KatID": "0", "typ": "repl", "search_way": "ss", "ss_phrase": query},
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        results = []
        for record in soup.find_all("div", class_="record-details"):
            title_link = record.find("a", attrs={"data-001": True})
            if not title_link:
                continue
            options = []
            for form in record.find_all("form"):
                values = {
                    field.get("name"): field.get("value", "")
                    for field in form.find_all("input")
                    if field.get("name")
                }
                if values.get("lendop") in {"order", "book"} and values.get("idw"):
                    options.append({
                        "idw": values["idw"],
                        "agenda": values.get("agenda", ""),
                        "pickup": values.get("pickups", ""),
                        "csrf_token": values.get("csrf_token", ""),
                        "action": values.get("lendop"),
                        "branch": re.split(
                            r"Są egzemplarze|Wszystkie egzemplarze",
                            form.get_text(" ", strip=True),
                            maxsplit=1,
                        )[0].strip(),
                    })
            results.append({
                "record_id": title_link.get("data-001"),
                "title": title_link.get_text(" ", strip=True),
                "options": options,
            })
        return results

    def cancel_reservation(self, reservation_id: str) -> tuple[bool, str]:
        """Cancel a reservation by its SOWA reservation/copy identifier."""
        reservation = next(
            (item for item in self.get_reservations() if item.reservation_id == reservation_id),
            None,
        )
        if reservation is None:
            return False, "Nie znaleziono rezerwacji o podanym identyfikatorze"
        data = dict(reservation._cancel_data)
        data.update({"id": "reserved", "sv": "1", "orderop": "cancel", "sn": reservation_id})
        kat_id = getattr(self, "_active_kat_id", str(self.kat_id))
        url = reservation._cancel_url or f"index.php?KatID={kat_id}&typ=acc"
        response = self._post(url, data=data)
        return self._action_result(response.text, "Rezerwacja anulowana")

    @staticmethod
    def _action_result(html: str, default_success: str) -> tuple[bool, str]:
        soup = BeautifulSoup(html, "html.parser")
        msg_div = soup.find(class_=re.compile(r"message|info|success|error|alert", re.I))
        if msg_div:
            msg = msg_div.get_text(" ", strip=True)
            failed = any(word in msg.lower() for word in ("błąd", "nie uda", "error"))
            return not failed, msg
        return True, default_success

    # -------------------------------------------------------------------------
    # History
    # -------------------------------------------------------------------------

    def get_history(self) -> list[Loan]:
        """Zwraca historię zwróconych książek."""
        soup = self._fetch_tab("returned")
        history = []

        results = soup.find("div", id="results-panel")
        if not results:
            return history

        for record in results.find_all("div", class_="record-details"):
            loan = Loan()
            meta = record.find("div", class_="record-meta")
            if meta:
                loan.title = meta.get_text(separator=" ", strip=True)
            info = record.find("div", class_="record-info-meta")
            if info:
                info_text = info.get_text(separator=" ", strip=True)
                loan.return_date = self._parse_date(info_text, "Zwrócono") if hasattr(loan, 'return_date') else ""
            history.append(loan)

        return history

    # -------------------------------------------------------------------------
    # Account info
    # -------------------------------------------------------------------------

    def get_account_info(self) -> AccountInfo:
        """Zwraca informacje o koncie czytelnika."""
        soup = self._fetch_tab("info")
        info = AccountInfo()
        text = soup.get_text(separator=" ", strip=True)

        # Imię i nazwisko
        name_div = soup.find("div", class_="acc-info-protected")
        if name_div:
            name_text = name_div.get_text(strip=True)
            # Pierwsza linia to imię nazwisko
            info.name = name_text.split("\n")[0].strip()

        # Email
        email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', text)
        if email_match:
            info.email = email_match.group(0)

        # Saldo / zadłużenie
        debt_div = soup.find(class_=re.compile(r"acc-info-summary", re.I))
        if debt_div:
            info.debt = debt_div.get_text(strip=True)

        # Data urodzenia (DD.MM.YYYY lub YYYY-MM-DD)
        birth_match = re.search(r'(\d{4}-\d{2}-\d{2})', text)
        if birth_match:
            info.birth_date = birth_match.group(1)

        # Telefon
        phone_match = re.search(r'(\d{3}\s*\d{3}\s*\d{3})', text)
        if phone_match:
            info.phone = phone_match.group(1).strip()

        self._info = info
        return info


# =============================================================================
# Multi-account manager
# =============================================================================

class MultiAccountManager:
    """
    Zarządza wieloma kontami czytelników (np. Twoje, żony, dziecka).
    Wszystkie konta w tej samej bibliotece.
    """

    def __init__(self, base_url: str, kat_id: int):
        self.base_url = base_url
        self.kat_id = kat_id
        self.accounts: dict[str, SowaOPAC] = {}

    def add_account(self, name: str, email: str, password: str) -> bool:
        """Dodaje i loguje konto. name = dowolna etykieta (np. 'ja', 'żona')."""
        client = SowaOPAC(self.base_url, self.kat_id)
        try:
            ok = client.login(email, password)
        except Exception as e:
            print(f"⚠️  Błąd logowania {name}: {e}")
            return False
        if ok:
            self.accounts[name] = client
        return ok

    def get_all_loans(self) -> dict[str, list[Loan]]:
        """Zwraca wypożyczenia ze wszystkich kont."""
        return {name: c.get_loans() for name, c in self.accounts.items()}

    def prolong_all_all(self) -> dict[str, list[tuple[str, bool, str]]]:
        """Prolonguje wszystkie wypożyczenia na wszystkich kontach."""
        return {name: c.prolong_all() for name, c in self.accounts.items()}

    def summary(self) -> str:
        """Zwraca tekstowe podsumowanie wszystkich kont."""
        lines = []
        for name, client in self.accounts.items():
            try:
                info = client.get_account_info()
                loans = client.get_loans()
                lines.append(f"\n{'='*60}")
                lines.append(f"📖 {name.upper()} ({info.name or '—'})")
                lines.append(f"{'='*60}")
                if info.email:
                    lines.append(f"  📧 {info.email}")
                if info.debt:
                    lines.append(f"  💰 {info.debt}")
                lines.append(f"  📚 Wypożyczenia ({len(loans)}):")
                for loan in loans:
                    due = f" ← {loan.due_date}" if loan.due_date else ""
                    branch = f" [{loan.branch}]" if loan.branch else ""
                    title = loan.short if hasattr(loan, 'short') else loan.title[:60]
                    lines.append(f"    • {title}{due}{branch}")
                if not loans:
                    lines.append("    (brak)")
            except Exception as e:
                lines.append(f"\n⚠️  Błąd {name}: {e}")
        return "\n".join(lines)


# =============================================================================
# Config & CLI
# =============================================================================

DEFAULT_CONFIG = {
    "base_url": "https://library.example.org",
    "kat_id": 0,
    "accounts": {}
}

def load_config(path: str = None) -> dict:
    """Ładuje konfigurację z pliku JSON."""
    path = path or os.path.expanduser("~/.sowa.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return DEFAULT_CONFIG.copy()


def save_config(config: dict, path: str = None):
    """Zapisuje konfigurację do pliku JSON."""
    path = path or os.path.expanduser("~/.sowa.json")
    with open(path, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    os.chmod(path, 0o600)
    print(f"✅ Konfiguracja zapisana: {path}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("""
Użycie:
  python sowa_opac.py summary     — podsumowanie wszystkich kont
  python sowa_opac.py loans       — tylko wypożyczenia
  python sowa_opac.py prolong     — prolonguj wszystkie
  python sowa_opac.py info        — info o koncie

Konfiguracja: ~/.sowa.json
{
  "base_url": "https://library.example.org",
  "kat_id": 0,
  "accounts": {
    "account_a": {"email": "reader@example.org", "password": "password"},
    "account_b": {"email": "other@example.org", "password": "password"}
  }
}
""")
        sys.exit(0)

    cmd = sys.argv[1]
    config = load_config()

    if not config.get("accounts"):
        print("⚠️  Brak kont w ~/.sowa.json. Utwórz plik z konfiguracją.")
        sys.exit(1)

    mgr = MultiAccountManager(config["base_url"], config["kat_id"])
    for name, creds in config["accounts"].items():
        ok = mgr.add_account(name, creds["email"], creds["password"])
        status = "✅" if ok else "❌"
        print(f"{status} {name}")

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
