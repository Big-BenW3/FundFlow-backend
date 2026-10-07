"""Single entry point for every Kora API call (PRODUCT.md §41)."""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import Settings, get_settings


class KoraAPIError(Exception):
    """Raised when a Kora API call fails.

    ``retryable`` marks transport/5xx failures where the outcome is UNKNOWN —
    per Kora's guidance these must be resolved with a verification call and
    never treated as automatic failures (PRODUCT.md §26).
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        body: Any = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.body = body
        self.retryable = retryable


class KoraClient:
    """Mode-aware Kora client (mock by default, live when KORA_MODE=live)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.is_mock = self.settings.is_mock
        self._mock = None
        if self.is_mock:
            from app.integrations.kora.mock import MockProvider

            self._mock = MockProvider(self.settings)
        self._http = httpx.Client(
            base_url=self.settings.kora_base_url,
            headers={
                # Kora authenticates merchant requests with the secret key.
                "Authorization": f"Bearer {self.settings.kora_secret_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=20.0,
        )

    # ------------------------------------------------------------ http core
    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        if not self.settings.kora_secret_key:
            raise KoraAPIError("KORA_SECRET_KEY is not configured", retryable=False)
        try:
            response = self._http.request(method, path, json=json, params=params)
        except httpx.TimeoutException as exc:
            raise KoraAPIError(f"Kora request timed out: {exc}", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise KoraAPIError(f"Kora request failed: {exc}", retryable=True) from exc

        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text}

        if response.status_code >= 500:
            raise KoraAPIError(
                f"Kora server error ({response.status_code})",
                status_code=response.status_code,
                body=body,
                retryable=True,
            )
        if response.status_code >= 400 or (
            isinstance(body, dict) and body.get("status") is False
        ):
            message = f"Kora error {response.status_code}"
            if isinstance(body, dict) and body.get("message"):
                message = str(body["message"])
            raise KoraAPIError(
                message,
                status_code=response.status_code,
                body=body,
                retryable=False,
            )
        return body if isinstance(body, dict) else {"data": body}

    # ----------------------------------------------------- virtual accounts
    def create_virtual_account(
        self,
        *,
        account_name: str,
        account_reference: str,
        customer_name: str,
        customer_email: str | None = None,
        bank_code: str = "000",
        bvn: str | None = None,
    ) -> dict:
        if self._mock:
            return self._mock.create_virtual_account(
                account_name=account_name,
                account_reference=account_reference,
                customer_name=customer_name,
                customer_email=customer_email,
                bank_code=bank_code,
            )
        from app.integrations.kora.virtual_accounts import live_create_virtual_account

        return live_create_virtual_account(
            self,
            account_name=account_name,
            account_reference=account_reference,
            customer_name=customer_name,
            customer_email=customer_email,
            bank_code=bank_code,
            bvn=bvn,
        )

    def get_virtual_account(self, account_reference: str) -> dict:
        if self._mock:
            return self._mock.get_virtual_account(account_reference)
        from app.integrations.kora.virtual_accounts import live_get_virtual_account

        return live_get_virtual_account(self, account_reference)

    def get_virtual_account_transactions(
        self, account_number: str, page: int = 1, limit: int = 100
    ) -> dict:
        if self._mock:
            return self._mock.get_virtual_account_transactions(account_number)
        from app.integrations.kora.virtual_accounts import (
            live_get_virtual_account_transactions,
        )

        return live_get_virtual_account_transactions(self, account_number, page, limit)

    # ------------------------------------------------------------ payments
    def register_charge(
        self, reference: str, amount: float, currency: str = "NGN", status: str = "success"
    ) -> None:
        """Mock-only: simulate an inbound payment reaching Kora."""
        if self._mock:
            self._mock.register_charge(reference, amount, currency, status)

    def verify_transaction(self, reference: str) -> dict:
        """Charge Query API — GET /charges/:reference (pay-in verification)."""
        if self._mock:
            return self._mock.verify_transaction(reference)
        from app.integrations.kora.payments import live_verify_transaction

        return live_verify_transaction(self, reference)

    # --------------------------------------------------------- verification
    def resolve_bank_account(
        self, bank_code: str, account_number: str, currency: str = "NGN"
    ) -> dict:
        """Bank Account Resolve — POST /misc/banks/resolve."""
        if self._mock:
            return self._mock.resolve_bank_account(bank_code, account_number, currency)
        from app.integrations.kora.verification import live_resolve_bank_account

        return live_resolve_bank_account(self, bank_code, account_number, currency)

    def list_banks(self, country_code: str = "NG") -> list[dict]:
        if self._mock:
            return self._mock.list_banks(country_code)
        from app.integrations.kora.verification import live_list_banks

        return live_list_banks(self, country_code)

    # -------------------------------------------------------------- payouts
    def create_payout(
        self,
        *,
        reference: str,
        amount: float,
        currency: str,
        bank_code: str,
        account_number: str,
        narration: str,
        customer_name: str | None = None,
        customer_email: str = "payout@fundflow.app",
    ) -> dict:
        """Payout API — POST /transactions/disburse."""
        if self._mock:
            return self._mock.create_payout(
                reference=reference,
                amount=amount,
                currency=currency,
                bank_code=bank_code,
                account_number=account_number,
                narration=narration,
                customer_name=customer_name,
                customer_email=customer_email,
            )
        from app.integrations.kora.payouts import live_create_payout

        return live_create_payout(
            self,
            reference=reference,
            amount=amount,
            currency=currency,
            bank_code=bank_code,
            account_number=account_number,
            narration=narration,
            customer_name=customer_name,
            customer_email=customer_email,
        )

    def verify_payout(self, reference: str) -> dict:
        """Payout verification — resolves UNKNOWN outcomes after 5xx/timeouts."""
        if self._mock:
            return self._mock.verify_payout(reference)
        from app.integrations.kora.payouts import live_verify_payout

        return live_verify_payout(self, reference)

    def get_balance(self, currency: str = "NGN") -> dict:
        """Balance API — GET /balances."""
        if self._mock:
            return self._mock.get_balance(currency)
        from app.integrations.kora.payouts import live_get_balance

        return live_get_balance(self, currency)

    def get_payout_history(self, currency: str | None = "NGN", limit: int = 20) -> list:
        if self._mock:
            return self._mock.get_payout_history()
        from app.integrations.kora.payouts import live_get_payout_history

        return live_get_payout_history(self, currency, limit)


_client: KoraClient | None = None


def get_kora_client(settings: Settings | None = None) -> KoraClient:
    """Process-wide singleton (mock state lives here for the demo)."""
    global _client
    if _client is None:
        _client = KoraClient(settings)
    return _client


def reset_kora_client() -> None:
    """Test hook — drop the singleton so settings changes take effect."""
    global _client
    if _client is not None:
        _client._http.close()
    _client = None

