"""Kora payment infrastructure integration (PRODUCT.md §41).

All Kora API access goes through a single ``KoraClient`` facade:

    KoraClient
    ├── create_virtual_account()
    ├── get_virtual_account()
    ├── get_virtual_account_transactions()
    ├── verify_transaction()
    ├── resolve_bank_account()
    ├── list_banks()
    ├── create_payout()
    ├── verify_payout()
    ├── get_balance()
    └── get_payout_history()

The facade delegates to either:

* ``MockProvider`` — deterministic in-process simulation (default, no keys)
* live HTTP implementations in ``virtual_accounts.py`` / ``payments.py`` /
  ``payouts.py`` / ``verification.py`` (used when ``KORA_MODE=live``)
"""

from app.integrations.kora.client import KoraAPIError, KoraClient, get_kora_client

__all__ = ["KoraAPIError", "KoraClient", "get_kora_client"]
