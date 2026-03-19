#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from polymarket_bot.clients.auth import init_clob_client, validate_wallet_inputs
from polymarket_bot.config import load_settings, validate_settings


def main() -> None:
    settings = load_settings()
    validate_settings(settings)
    validate_wallet_inputs(settings)
    if not settings.is_live:
        raise RuntimeError("Set BOT_MODE=live before running bootstrap_magiclink.py")

    _, creds = init_clob_client(settings)
    if creds is None:
        raise RuntimeError("Could not initialize API credentials.")

    out = {
        "apiKey": creds.api_key,
        "secret": creds.secret,
        "passphrase": creds.passphrase,
        "funderAddress": settings.funder_address,
        "signatureType": settings.signature_type,
    }
    target = Path("generated_api_creds.json")
    target.write_text(json.dumps(out, indent=2))
    print(f"Wrote {target}")
    print("Next steps:")
    print("1) Complete one-time approvals: USDC.e->CTF, CTF->CTF_EXCHANGE, CTF->NEG_RISK_CTF_EXCHANGE")
    print("2) Put API_KEY/SECRET/PASSPHRASE into secret manager")
    print("3) Run scripts/smoke_test_live.py with tiny size")


if __name__ == "__main__":
    main()
