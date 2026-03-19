from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from py_clob_client.client import ClobClient

from polymarket_bot.config import Settings

LOG = logging.getLogger(__name__)

ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


@dataclass(frozen=True)
class ApiCredentials:
    api_key: str
    secret: str
    passphrase: str

    def as_dict(self) -> dict[str, str]:
        return {
            "api_key": self.api_key,
            "api_secret": self.secret,
            "api_passphrase": self.passphrase,
        }


def _extract_creds(raw: dict) -> ApiCredentials:
    key = raw.get("apiKey") or raw.get("key") or raw.get("api_key")
    secret = raw.get("secret") or raw.get("api_secret")
    passphrase = raw.get("passphrase") or raw.get("api_passphrase")
    if not key or not secret or not passphrase:
        raise ValueError("Could not extract API credentials from create_or_derive_api_creds().")
    return ApiCredentials(api_key=key, secret=secret, passphrase=passphrase)


def validate_wallet_inputs(settings: Settings) -> None:
    if settings.is_live:
        if not ADDRESS_RE.match(settings.funder_address):
            raise ValueError("FUNDER_ADDRESS must be a valid 0x-prefixed Polygon address.")
        if not settings.private_key.startswith("0x"):
            raise ValueError("PRIVATE_KEY must be 0x-prefixed.")


def init_clob_client(settings: Settings) -> tuple[ClobClient | None, ApiCredentials | None]:
    if not settings.is_live:
        return None, None

    validate_wallet_inputs(settings)
    client = ClobClient(
        settings.clob_host,
        key=settings.private_key,
        chain_id=settings.chain_id,
        signature_type=settings.signature_type,
        funder=settings.funder_address,
    )

    if settings.api_key and settings.secret and settings.passphrase:
        creds = ApiCredentials(settings.api_key, settings.secret, settings.passphrase)
    else:
        LOG.info("Deriving L2 credentials from L1 signer")
        creds = _extract_creds(client.create_or_derive_api_creds())

    client.set_api_creds(creds.as_dict())
    return client, creds
