# Magic Link Setup Runbook

## 1) Wallet + Auth Inputs
- Export your private key from Polymarket (Magic Link account).
- Set `SIGNATURE_TYPE=1`.
- Set `FUNDER_ADDRESS` to your Polymarket proxy wallet.
- Set `BOT_MODE=live` only when ready to trade.

## 2) Generate/Derive L2 Credentials
- Run: `python scripts/bootstrap_magiclink.py`
- This derives or reuses API creds via L1 signer auth and writes `generated_api_creds.json`.
- Move values into secrets storage (`API_KEY`, `SECRET`, `PASSPHRASE`).

## 3) One-Time Token Approvals (Required)
Before live trading, approve:
- `USDC.e -> CTF`
- `CTF outcome tokens -> CTF_EXCHANGE`
- `CTF outcome tokens -> NEG_RISK_CTF_EXCHANGE`

Use Polymarket relayer client or your existing approval flow.

## 4) Smoke Test
- Run: `python scripts/smoke_test_live.py`
- Verify:
  - order creates successfully
  - order can be canceled

## 5) Production Safety
- Start in paper mode first.
- Keep `MAX_DAILY_DRAWDOWN_PCT` conservative.
- Confirm CloudWatch alerts before enabling live deployment.
