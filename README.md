# Polymarket BTC 5m Bot (POLY_PROXY)

Fresh rebuild of a Polymarket BTC 5m market-making bot using:
- `POLY_PROXY` auth (`signature_type=1`) for Magic Link accounts
- official `py-clob-client` L1/L2 flow
- two-sided quote engine inspired by `hw-utils-archive/market_maker.py`
- ECS Fargate deployment assets

## Project Layout
- `src/polymarket_bot/main.py` - bot entrypoint
- `src/polymarket_bot/engine/lifecycle.py` - candle lifecycle, quote loop, inventory loop
- `src/polymarket_bot/strategy/avellaneda.py` - quote math
- `src/polymarket_bot/clients/auth.py` - API credential derivation and wallet validation
- `src/polymarket_bot/clients/clob.py` - order placement/cancel wrappers
- `src/polymarket_bot/clients/market_data.py` - Gamma/CLOB market data
- `src/polymarket_bot/risk/controls.py` - drawdown and circuit breaker logic
- `scripts/bootstrap_magiclink.py` - derive creds and generate local bootstrap artifact
- `scripts/smoke_test_live.py` - tiny live order/cancel smoke test
- `infra/aws/` - Fargate deployment docs and templates

## Local Setup
1. `python -m venv .venv && source .venv/bin/activate`
2. `pip install -r requirements.txt`
3. `cp .env.example .env` and fill secrets

Run paper mode:
- `BOT_MODE=paper python -m polymarket_bot.main`

Run live mode:
- `BOT_MODE=live python -m polymarket_bot.main`

## Magic Link Auth Notes
- Export your key from Polymarket and set `PRIVATE_KEY`.
- Set `FUNDER_ADDRESS` to your Polymarket proxy wallet.
- Keep `SIGNATURE_TYPE=1`.
- Use `scripts/bootstrap_magiclink.py` once to derive creds.
- Complete onchain approvals before placing real orders.

## Safety Defaults
- Stale data auto-pause
- Consecutive post-failure circuit breaker
- Daily drawdown guard
- Cancel-all on shutdown and near candle deadline
