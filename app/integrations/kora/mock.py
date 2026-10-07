"""Deterministic in-process Kora simulator (default mode: no API keys).

Behaviour mirrors the real API contracts documented at
developers.korapay.com, including Kora's published sandbox test data:

* Payout success scenario — bank ``033`` + account ``0000000000``
* Payout failure scenario — bank ``035`` + account ``0000000000``
* Invalid account scenario — bank ``011`` + account ``9999999999``
* Bank resolve test account — ``0123456789`` (basic verification)
"""

from __future__ import annotations

import hashlib

from app.core.config import Settings
from app.integrations.kora.client import KoraAPIError

# Nigerian banks (codes verified against Kora docs — NGN VBA + payout test data)
NIGERIAN_BANKS = [
    {"name": "Access Bank", "code": "044"},
    {"name": "First Bank of Nigeria", "code": "011"},
    {"name": "FCMB", "code": "214"},
    {"name": "Fidelity Bank", "code": "070"},
    {"name": "Globus Bank", "code": "103"},
    {"name": "Guaranty Trust Bank (GTB)", "code": "058"},
    {"name": "Moniepoint", "code": "090405"},
    {"name": "Optimus Bank", "code": "107"},
    {"name": "Parallex Bank", "code": "104"},
    {"name": "Sterling Bank", "code": "001"},
    {"name": "UBA", "code": "033"},
    {"name": "Wema Bank", "code": "035"},
    {"name": "Zenith Bank", "code": "057"},
]

_MOCK_NAMES = [
    "CHINELU ADAEZE OKONKWO",
    "EMEKA NWOSU IBRAHIM",
    "AMINA YUSUF BELLO",
    "TOCHUKWU OSEMEKA DIKE",
    "BLESSED ADEWALE COLE",
    "NGOZI EZEANYEKA OBI",
    "MUSTAQA IBRAHIM GARBA",
    "UCHENNA NNAMDI EZE",
]


def _bank_name(code: str) -> str:
    for bank in NIGERIAN_BANKS:
        if bank["code"] == code:
            return bank["name"]
    return "Mock Bank"


class MockProvider:
    """Stateful fake Kora. State is per-process on the client singleton."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.accounts: dict[str, dict] = {}
        self.charges: dict[str, dict] = {}
        self.transfers: dict[str, dict] = {}

    # ----------------------------------------------------- virtual accounts
    def create_virtual_account(
        self,
        *,
        account_name: str,
        account_reference: str,
        customer_name: str,
        customer_email: str | None = None,
        bank_code: str = "000",
    ) -> dict:
        digest = hashlib.sha256(account_reference.encode()).hexdigest()
        # Deterministic 10-digit account number that never starts with 0.
        number = str(100_000_000 + int(digest[:9], 16) % 899_999_999)
        data = {
            "account_name": account_name[:64],
            "account_number": number,
            "bank_code": "000",
            "bank_name": "Kora Sandbox Bank",
            "account_reference": account_reference,
            "unique_id": f"kora-{digest[:12]}",
            "account_status": "active",
            "currency": "NGN",
            "customer": {"name": customer_name, "email": customer_email},
        }
        self.accounts[account_reference] = data
        return data

    def get_virtual_account(self, account_reference: str) -> dict:
        if account_reference not in self.accounts:
            raise KoraAPIError("Virtual bank account not found", status_code=404)
        return self.accounts[account_reference]

    def get_virtual_account_transactions(self, account_number: str) -> dict:
        return {
            "total_amount_recieved": 0,
            "account_number": account_number,
            "currency": "NGN",
            "transactions": [],
            "pagination": {"page": 1, "total": 0, "pageCount": 1, "totalPages": 1},
        }

    # ------------------------------------------------------------ payments
    def register_charge(
        self, reference: str, amount: float, currency: str = "NGN", status: str = "success"
    ) -> None:
        self.charges[reference] = {
            "reference": reference,
            "status": status,
            "amount": round(float(amount), 2),
            "amount_paid": round(float(amount), 2),
            "fee": 0.0,
            "currency": currency,
            "description": "FundFlow contribution",
        }

    def verify_transaction(self, reference: str) -> dict:
        charge = self.charges.get(reference)
        if charge is None:
            raise KoraAPIError("Transaction not found", status_code=404)
        return charge

    # --------------------------------------------------------- verification
    def resolve_bank_account(
        self, bank_code: str, account_number: str, currency: str = "NGN"
    ) -> dict:
        # Kora's documented invalid-account test scenario.
        if account_number == "9999999999" or (
            bank_code == "011" and account_number == "0000000000"
        ):
            raise KoraAPIError("Account number could not be resolved", status_code=404)

        known = {
            "1234567890": "JOHN OKAFOR",  # PRODUCT.md §14 example
            "0123456789": "JOHN MICHAEL DOE",  # Kora basic-verification test data
            "0000000000": "TEST ACCOUNT HOLDER",
        }
        name = known.get(account_number)
        if name is None:
            digest = int(
                hashlib.sha256(f"{bank_code}{account_number}".encode()).hexdigest(), 16
            )
            name = _MOCK_NAMES[digest % len(_MOCK_NAMES)]

        return {
            "bank_name": _bank_name(bank_code),
            "bank_code": bank_code,
            "account_number": account_number,
            "account_name": name,
        }

    def list_banks(self, country_code: str = "NG") -> list[dict]:
        return list(NIGERIAN_BANKS)

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
        if account_number == "9999999999":
            # Rejected up-front (invalid account) — payout definitively failed.
            raise KoraAPIError(
                "Destination account is invalid", status_code=400, retryable=False
            )

        # Sandbox outcomes: 033/0000000000 succeeds, 035/0000000000 fails.
        outcome = (
            "failed" if (bank_code == "035" and account_number == "0000000000")
            else "success"
        )

        self.transfers[reference] = {
            "reference": reference,
            "status": "processing",
            "outcome": outcome,
            "amount": round(float(amount), 2),
            "fee": 0.0,
            "currency": currency,
            "narration": narration,
        }
        return {
            "amount": f"{float(amount):.2f}",
            "fee": "0.00",
            "currency": currency,
            "status": "processing",
            "reference": reference,
            "narration": narration,
            "message": "Payout processing",
            "customer": {"name": customer_name, "email": customer_email, "phone": None},
        }

    def verify_payout(self, reference: str) -> dict:
        transfer = self.transfers.get(reference)
        if transfer is None:
            raise KoraAPIError("Transfer not found", status_code=404)
        final = "success" if transfer["outcome"] == "success" else "failed"
        transfer["status"] = final
        return {
            "reference": reference,
            "status": final,
            "amount": transfer["amount"],
            "fee": transfer["fee"],
            "currency": transfer["currency"],
            "narration": transfer["narration"],
            "message": "Payout successful" if final == "success" else "Payout failed",
        }

    def get_balance(self, currency: str = "NGN") -> dict:
        return {
            currency: {
                "available_balance": float(self.settings.mock_merchant_balance),
                "pending_balance": 0.0,
            }
        }

    def get_payout_history(self) -> list[dict]:
        return list(self.transfers.values())

    # ------------------------------------------------------------- helpers
    def payout_outcome(self, reference: str) -> str:
        transfer = self.transfers.get(reference)
        return transfer["outcome"] if transfer else "success"

