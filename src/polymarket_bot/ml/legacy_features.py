from __future__ import annotations

import numpy as np
import pandas as pd

SEGMENT_MINUTES = 30
LOOKBACKS = [1, 2, 3, 6, 12, 24, 48]


def frac_diff(series, d=0.4, threshold=1e-4, max_window=50):
    weights = [1.0]
    for k in range(1, min(len(series), max_window)):
        weight = -weights[-1] * (d - k + 1) / k
        if abs(weight) < threshold:
            break
        weights.append(weight)
    weights = np.array(weights)
    result = np.full(len(series), np.nan)
    for i in range(len(weights) - 1, len(series)):
        result[i] = np.dot(weights, series[i - len(weights) + 1 : i + 1])
    return result


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    has_taker = "taker_buy_base" in df.columns

    for lb in LOOKBACKS:
        df[f"ret_{lb}"] = df["close"].pct_change(lb)
    df["log_ret_1"] = np.log(df["close"] / df["close"].shift(1))
    for w in [6, 12, 24, 48]:
        df[f"vol_{w}"] = df["log_ret_1"].rolling(w).std()
    df["vol_ratio_6_24"] = df["vol_6"] / df["vol_24"].replace(0, 1)
    df["vol_ratio_12_48"] = df["vol_12"] / df["vol_48"].replace(0, 1)

    for w in [6, 12, 24, 48]:
        hi = df["high"].rolling(w).max()
        lo = df["low"].rolling(w).min()
        rng = (hi - lo).replace(0, 1)
        df[f"range_pos_{w}"] = (df["close"] - lo) / rng

    for w in [6, 12, 24]:
        df[f"vol_sma_{w}"] = df["volume"].rolling(w).mean()
        df[f"vol_ratio_{w}"] = df["volume"] / df[f"vol_sma_{w}"].replace(0, 1)

    body = (df["close"] - df["open"]).abs()
    full_range = (df["high"] - df["low"]).replace(0, 1)
    df["body_ratio"] = body / full_range
    df["upper_wick"] = (df["high"] - df[["open", "close"]].max(axis=1)) / full_range
    df["lower_wick"] = (df[["open", "close"]].min(axis=1) - df["low"]) / full_range
    df["signed_body"] = (df["close"] - df["open"]) / full_range

    for w in [6, 12, 14, 24]:
        delta = df["close"].diff()
        gain = delta.clip(lower=0).rolling(w).mean()
        loss = (-delta.clip(upper=0)).rolling(w).mean()
        rs = gain / loss.replace(0, 1)
        df[f"rsi_{w}"] = 100 - (100 / (1 + rs))

    for fast, slow in [(3, 12), (6, 24), (12, 48)]:
        ema_f = df["close"].ewm(span=fast).mean()
        ema_s = df["close"].ewm(span=slow).mean()
        df[f"ema_spread_{fast}_{slow}"] = (ema_f - ema_s) / ema_s * 100

    ema12 = df["close"].ewm(span=12).mean()
    ema26 = df["close"].ewm(span=26).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    df["macd_hist_diff"] = df["macd_hist"].diff()

    for w in [12, 24]:
        mid = df["close"].rolling(w).mean()
        std = df["close"].rolling(w).std()
        df[f"bb_pos_{w}"] = (df["close"] - (mid - 2 * std)) / (4 * std).replace(0, 1)

    up = (df["close"] > df["open"]).astype(int)
    df["consec_up"] = up.groupby((up != up.shift()).cumsum()).cumcount() + 1
    df["consec_up"] = df["consec_up"] * up
    down = (df["close"] < df["open"]).astype(int)
    df["consec_down"] = down.groupby((down != down.shift()).cumsum()).cumcount() + 1
    df["consec_down"] = df["consec_down"] * down

    df["hl_pct"] = (df["high"] - df["low"]) / df["close"] * 100
    for w in [6, 12, 24]:
        df[f"hl_pct_sma_{w}"] = df["hl_pct"].rolling(w).mean()
        df[f"hl_pct_ratio_{w}"] = df["hl_pct"] / df[f"hl_pct_sma_{w}"].replace(0, 1)

    for w in [12, 24]:
        df[f"ret_skew_{w}"] = df["log_ret_1"].rolling(w).skew()
        df[f"ret_kurt_{w}"] = df["log_ret_1"].rolling(w).kurt()

    if has_taker:
        df["taker_buy_ratio"] = df["taker_buy_base"] / df["volume"].replace(0, 1)
        for w in [3, 6, 12, 24]:
            df[f"buy_ratio_sma_{w}"] = df["taker_buy_ratio"].rolling(w).mean()
        df["net_flow"] = df["taker_buy_base"] - (df["volume"] - df["taker_buy_base"])
        for w in [3, 6, 12, 24]:
            df[f"cum_flow_{w}"] = df["net_flow"].rolling(w).sum()
        df["flow_mom_3"] = df["net_flow"].rolling(3).mean() - df["net_flow"].rolling(12).mean()
        buy_vol = df["taker_buy_base"]
        sell_vol = df["volume"] - df["taker_buy_base"]
        order_imbalance = (buy_vol - sell_vol).abs()
        for w in [6, 12, 24]:
            df[f"vpin_{w}"] = order_imbalance.rolling(w).mean() / df["volume"].rolling(w).mean().replace(0, 1)
        if "trades" in df.columns and "quote_volume" in df.columns:
            df["avg_trade_size"] = df["quote_volume"] / df["trades"].replace(0, 1)
            for w in [6, 12]:
                df[f"trade_size_ratio_{w}"] = df["avg_trade_size"] / df["avg_trade_size"].rolling(w).mean().replace(0, 1)

    df["day_of_week"] = df.index.dayofweek
    df["is_weekend"] = (df["day_of_week"] >= 5).astype(int)
    df["hour"] = df.index.hour
    df["is_asia"] = ((df["hour"] >= 0) & (df["hour"] < 8)).astype(int)
    df["is_london"] = ((df["hour"] >= 8) & (df["hour"] < 14)).astype(int)
    df["is_us"] = ((df["hour"] >= 14) & (df["hour"] < 22)).astype(int)

    df["frac_diff_close"] = frac_diff(df["close"].values, d=0.4)
    df["price_momentum_6"] = df["close"].pct_change(6).rolling(6).mean()
    df["price_momentum_12"] = df["close"].pct_change(12).rolling(12).mean()

    if has_taker:
        buy_vol_sf = df["taker_buy_base"]
        sell_vol_sf = df["volume"] - df["taker_buy_base"]
        signed_flow = buy_vol_sf - sell_vol_sf
        for w in [3, 6, 12, 24]:
            df[f"signed_flow_ma_{w}"] = signed_flow.rolling(w).mean()
            df[f"signed_flow_std_{w}"] = signed_flow.rolling(w).std()
        df["flow_zscore_12"] = (signed_flow - signed_flow.rolling(12).mean()) / signed_flow.rolling(12).std().replace(0, 1)
        df["flow_zscore_24"] = (signed_flow - signed_flow.rolling(24).mean()) / signed_flow.rolling(24).std().replace(0, 1)

    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"] - df["close"].shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    for w in [6, 12, 24]:
        df[f"atr_{w}"] = tr.rolling(w).mean()
        df[f"atr_ratio_{w}"] = tr / df[f"atr_{w}"].replace(0, 1)

    return df


def get_segment_id(ts) -> int:
    minute_of_day = ts.hour * 60 + ts.minute
    return minute_of_day // SEGMENT_MINUTES
