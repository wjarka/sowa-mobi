"""Small authenticated REST facade for the SOWA client."""

from __future__ import annotations

import hmac
import os
from pathlib import Path
from typing import Any, Callable

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from sowa_mobi.sowa_opac import SowaOPAC


ClientFactory = Callable[[str], SowaOPAC]


class HintedResponse(BaseModel):
    """Common agent-facing response contract."""

    hint: list[str]
    model_config = ConfigDict(extra="allow")


def with_hint(payload: dict[str, Any], *hints: str) -> dict[str, Any]:
    return {**payload, "hint": list(hints)}


def load_api_config(path: str | None = None) -> dict[str, Any]:
    """Load API configuration from SOWA_CONFIG or ~/.sowa.json."""
    import json

    config_path = Path(path or os.environ.get("SOWA_CONFIG", Path.home() / ".sowa.json"))
    with config_path.open() as handle:
        return json.load(handle)


def create_app(config: dict[str, Any], client_factory: ClientFactory | None = None) -> FastAPI:
    """Create an API app from a config dictionary.

    Each account must define ``api_token``.  The token identifies the account,
    so API consumers never need SOWA credentials or an account selector.
    """
    accounts = config.get("accounts") or {}
    token_accounts: dict[str, str] = {}
    for account_name, account in accounts.items():
        token = account.get("api_token")
        if not token:
            raise ValueError(f"Account {account_name!r} has no api_token")
        if any(hmac.compare_digest(token, existing) for existing in token_accounts):
            raise ValueError("Duplicate api_token configured")
        token_accounts[token] = account_name

    clients: dict[str, SowaOPAC] = {}
    reservation_options: dict[tuple[str, str], dict[str, str]] = {}
    default_base_url = config.get("base_url")
    default_kat_id = config.get("kat_id")
    if not default_base_url and not client_factory:
        raise ValueError("Configuration requires base_url")
    if default_kat_id is None and not client_factory:
        raise ValueError("Configuration requires kat_id")
    factory = client_factory or (
        lambda account_name: SowaOPAC(
            accounts[account_name].get("base_url", default_base_url),
            int(accounts[account_name].get("kat_id", default_kat_id)),
        )
    )

    app = FastAPI(
        title="SOWA MOBI Agent API",
        version="0.3.0",
        description=(
            "Agent-friendly REST facade for library account operations. "
            "Every JSON response contains a hint array with suggested next actions."
        ),
    )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        suggestions = {
            401: "Provide Authorization: Bearer <account_api_token> and retry the request.",
            404: "The identifier may be expired or unknown; search the catalog or list the resource again.",
            422: "Read detail, correct the request fields, and retry.",
            502: "The library system request failed; retry later and check the library service status.",
        }
        body = {"detail": exc.detail, "hint": [suggestions.get(exc.status_code, "Check detail and choose the next API operation.")]}
        return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"detail": exc.errors(), "hint": ["Correct the request using the OpenAPI schema and retry."]},
        )

    def authenticated_account(authorization: str | None = Header(default=None)) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
        presented = authorization[7:].strip()
        for configured, account_name in token_accounts.items():
            if hmac.compare_digest(presented, configured):
                return account_name
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token")

    def get_client(account_name: str) -> SowaOPAC:
        client = clients.get(account_name)
        if client is None:
            client = factory(account_name)
            clients[account_name] = client
        if not client.logged_in:
            account = accounts[account_name]
            try:
                if not client.login(account["email"], account["password"]):
                    raise RuntimeError("SOWA rejected credentials")
            except Exception as exc:
                raise HTTPException(status_code=502, detail="SOWA login failed") from exc
        return client

    def loan_json(loan: Any) -> dict[str, Any]:
        return {
            "title": getattr(loan, "short_title", "") or getattr(loan, "title", ""),
            "author": getattr(loan, "author", ""),
            "due_date": getattr(loan, "due_date", ""),
            "loan_date": getattr(loan, "loan_date", ""),
            "branch": getattr(loan, "branch", ""),
            "copy_id": getattr(loan, "copy_id", ""),
            "can_prolong": bool(getattr(loan, "can_prolong", False)),
        }

    @app.get("/healthz", response_model=HintedResponse, summary="Check API health", tags=["system"])
    def healthz() -> dict[str, Any]:
        return with_hint(
            {"status": "ok", "revision": os.environ.get("SOWA_REVISION", "unknown")},
            "Use the documented endpoints to access library data.",
        )

    @app.get("/v1/account", response_model=HintedResponse, summary="Get the authenticated account", tags=["account"])
    def account(account_name: str = Depends(authenticated_account)) -> dict[str, Any]:
        client = get_client(account_name)
        try:
            info = client.get_account_info()
        except Exception as exc:
            raise HTTPException(status_code=502, detail="SOWA account request failed") from exc
        return with_hint(
            {
                "account": account_name,
                "name": getattr(info, "name", ""),
                "debt": getattr(info, "debt", ""),
                "loans_count": getattr(info, "loans_count", None),
                "reservations_count": getattr(info, "reservations_count", None),
                "loan_limit": getattr(info, "loan_limit", None),
            },
            "Use loans_count and loan_limit to assess borrowing capacity; null means the library did not publish the limit.",
        )

    @app.get("/v1/account/billing", response_model=HintedResponse, summary="Get account balance and billing", tags=["account"])
    def billing(account_name: str = Depends(authenticated_account)) -> dict[str, Any]:
        client = get_client(account_name)
        try:
            summary = client.get_billing_summary()
        except Exception as exc:
            raise HTTPException(status_code=502, detail="SOWA billing request failed") from exc
        return with_hint(
            {"account": account_name, **summary},
            "Use the balance and billing operations to explain account charges; an empty operations list means no charges were published.",
        )

    @app.get("/v1/loans", response_model=HintedResponse, summary="List current loans", tags=["loans"])
    def loans(account_name: str = Depends(authenticated_account)) -> dict[str, Any]:
        client = get_client(account_name)
        try:
            items = client.get_loans()
        except Exception as exc:
            raise HTTPException(status_code=502, detail="SOWA loans request failed") from exc
        loans_data = [loan_json(item) for item in items]
        hints = ["Use copy_id with POST /v1/loans/{copy_id}/prolong for an eligible loan."] if any(
            item["can_prolong"] for item in loans_data
        ) else ["No current loan is marked as eligible for prolongation."]
        return with_hint({"account": account_name, "loans": loans_data}, *hints)

    @app.get("/v1/reservations", response_model=HintedResponse, summary="List reservations", tags=["reservations"])
    def reservations(account_name: str = Depends(authenticated_account)) -> dict[str, Any]:
        client = get_client(account_name)
        try:
            items = client.get_reservations()
        except Exception as exc:
            raise HTTPException(status_code=502, detail="SOWA reservations request failed") from exc
        payload = {
            "account": account_name,
            "reservations": [
                {
                    "title": getattr(item, "title", ""),
                    "status": getattr(item, "status", ""),
                    "queue_pos": getattr(item, "queue_pos", ""),
                    "expire_date": getattr(item, "expire_date", ""),
                    "reservation_id": getattr(item, "reservation_id", ""),
                    "ready": bool(getattr(item, "ready", False)),
                    "pickup_by": getattr(item, "pickup_by", ""),
                }
                for item in items
            ],
        }
        hint = "Use reservation_id with DELETE /v1/reservations/{reservation_id} to cancel a reservation." if items else "No reservations found; search the catalog to find a book to reserve."
        return with_hint(payload, hint)

    @app.get(
        "/v1/catalog/search",
        response_model=HintedResponse,
        summary="Search the catalog",
        description="Returns public reservation option_id values. SOWA identifiers and CSRF tokens are kept server-side.",
        tags=["catalog"],
    )
    def catalog_search(
        q: str = Query(min_length=2),
        account_name: str = Depends(authenticated_account),
    ) -> dict[str, Any]:
        client = get_client(account_name)
        try:
            results = client.search_catalog(q)
        except Exception as exc:
            raise HTTPException(status_code=502, detail="SOWA catalog search failed") from exc
        public_results = []
        for result in results:
            public_options = []
            for index, option in enumerate(result.get("options", [])):
                option_id = f"{result['record_id']}:{index}"
                reservation_options[(account_name, option_id)] = option
                public_options.append({
                    "option_id": option_id,
                    "branch": option.get("branch", ""),
                    "action": option.get("action", ""),
                })
            public_results.append({
                "record_id": result.get("record_id", ""),
                "title": result.get("title", ""),
                "options": public_options,
            })
        has_options = any(result["options"] for result in public_results)
        hint = "Use option_id to make a reservation." if has_options else "No reservable copy was found; try another query."
        return with_hint({"query": q, "results": public_results}, hint)

    @app.post("/v1/loans/{copy_id}/prolong", response_model=HintedResponse, summary="Prolong a loan", tags=["loans"])
    def prolong(copy_id: str, account_name: str = Depends(authenticated_account)) -> dict[str, Any]:
        client = get_client(account_name)
        try:
            success, message = client.prolong_by_copy_id(copy_id)
        except Exception as exc:
            raise HTTPException(status_code=502, detail="SOWA prolongation request failed") from exc
        hint = "Call GET /v1/loans to verify the new due date." if success else "Check can_prolong and copy_id in GET /v1/loans before retrying."
        return with_hint({"account": account_name, "success": success, "message": message, "copy_id": copy_id}, hint)

    @app.post("/v1/reservations", response_model=HintedResponse, summary="Create a reservation", tags=["reservations"])
    def reserve(
        payload: dict[str, str] = Body(...),
        account_name: str = Depends(authenticated_account),
    ) -> dict[str, Any]:
        option_id = payload.get("option_id")
        if not option_id:
            raise HTTPException(status_code=422, detail="Required field: option_id")
        option = reservation_options.get((account_name, option_id))
        if option is None:
            raise HTTPException(status_code=404, detail="Unknown or expired reservation option")
        client = get_client(account_name)
        try:
            success, message = client.reserve(
                option["idw"], option["agenda"], option["pickup"], option["csrf_token"]
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail="SOWA reservation request failed") from exc
        hint = "Call GET /v1/reservations to verify the reservation." if success else "Check the option status by searching the catalog again before retrying."
        return with_hint({"account": account_name, "success": success, "message": message, "option_id": option_id}, hint)

    @app.delete("/v1/reservations/{reservation_id}", response_model=HintedResponse, summary="Cancel a reservation", tags=["reservations"])
    def cancel_reservation(
        reservation_id: str,
        account_name: str = Depends(authenticated_account),
    ) -> dict[str, Any]:
        client = get_client(account_name)
        try:
            success, message = client.cancel_reservation(reservation_id)
        except Exception as exc:
            raise HTTPException(status_code=502, detail="SOWA cancellation request failed") from exc
        return with_hint({"account": account_name, "success": success, "message": message}, "Call GET /v1/reservations to verify cancellation.")

    return app


def app_from_environment() -> FastAPI:
    return create_app(load_api_config())


app = app_from_environment() if os.environ.get("SOWA_CONFIG") else None
