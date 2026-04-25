from concurrent.futures import ThreadPoolExecutor, as_completed

from .api import api_get
from .config import (
    MIN_DATA_DAYS, MIN_SIDEWAYS_DAYS, MAX_RANGE_PCT,
    MAX_AVG_VOL_USD, MIN_OI_DELTA_PCT, MIN_OI_USD,
    VOL_BREAKOUT_MULT, logger,
)

EXCLUDE_COINS = frozenset({"USDC", "USDP", "TUSD", "FDUSD", "BTCDOM", "DEFI", "USDM"})

MAX_WORKERS = 6  # parallel kline fetch threads


def get_all_perp_symbols():
    """获取所有USDT永续合约"""
    info = api_get("/fapi/v1/exchangeInfo")
    if not info:
        return []
    return [s["symbol"] for s in info["symbols"]
            if s["quoteAsset"] == "USDT"
            and s["contractType"] == "PERPETUAL"
            and s["status"] == "TRADING"]


def _fetch_kline(sym):
    return sym, api_get("/fapi/v1/klines", {
        "symbol": sym, "interval": "1d", "limit": 180
    })


def analyze_accumulation(symbol, klines):
    """分析单个币的收筹特征（增量滑窗 O(n)）"""
    if len(klines) < MIN_DATA_DAYS:
        return None

    coin = symbol.replace("USDT", "")
    if coin in EXCLUDE_COINS:
        return None

    n = len(klines)
    opens = [0.0] * n
    highs = [0.0] * n
    lows = [0.0] * n
    closes = [0.0] * n
    vols = [0.0] * n
    for i, k in enumerate(klines):
        opens[i] = float(k[1])
        highs[i] = float(k[2])
        lows[i] = float(k[3])
        closes[i] = float(k[4])
        vols[i] = float(k[7])

    # 已暴涨过滤：近7天均价 vs 之前均价 > 3x
    split = n - 7
    if split <= 0:
        return None
    prior_avg = sum(closes[:split]) / split
    recent_avg = sum(closes[split:]) / 7
    if prior_avg > 0 and (recent_avg - prior_avg) / prior_avg > 3.0:
        return None

    best_sideways = 0
    best_range = 0
    best_low = 0
    best_high = 0
    best_avg_vol = 0
    best_slope_pct = 0

    # 增量滑窗：从最大窗口向小窗口收缩，一次遍历
    end = split
    run_low = lows[end - 1]
    run_high = highs[end - 1]
    run_vol = vols[end - 1]

    for w in range(1, end + 1):
        idx = end - w
        lo = lows[idx]
        hi = highs[idx]
        v = vols[idx]
        if w == 1:
            run_low, run_high, run_vol = lo, hi, v
        else:
            if lo < run_low:
                run_low = lo
            if hi > run_high:
                run_high = hi
            run_vol += v

        window = w
        if window < MIN_SIDEWAYS_DAYS:
            continue

        if run_low <= 0:
            continue
        range_pct = (run_high - run_low) / run_low * 100
        if range_pct > MAX_RANGE_PCT:
            continue
        avg_vol = run_vol / window
        if avg_vol > MAX_AVG_VOL_USD:
            continue

        # 线性回归斜率（只在候选窗口上计算）
        closes_w = closes[idx:end]
        nw = len(closes_w)
        x_mean = (nw - 1) / 2.0
        y_mean = sum(closes_w) / nw
        num = 0.0
        den = 0.0
        for j in range(nw):
            dx = j - x_mean
            num += dx * (closes_w[j] - y_mean)
            den += dx * dx
        slope = num / den if den > 0 else 0
        slope_pct = (slope * nw / closes_w[0] * 100) if closes_w[0] > 0 else 0
        if abs(slope_pct) > 20:
            continue

        if window > best_sideways:
            best_sideways = window
            best_range = range_pct
            best_low = run_low
            best_high = run_high
            best_avg_vol = avg_vol
            best_slope_pct = slope_pct

    if best_sideways < MIN_SIDEWAYS_DAYS:
        return None

    # 评分
    days_score = min(best_sideways / 90, 1.0) * 25
    range_score = max(0, (1 - best_range / MAX_RANGE_PCT)) * 20
    vol_score = max(0, (1 - best_avg_vol / MAX_AVG_VOL_USD)) * 20
    recent_vol = sum(vols[split:]) / 7
    vol_breakout = recent_vol / best_avg_vol if best_avg_vol > 0 else 0
    breakout_score = min(vol_breakout / VOL_BREAKOUT_MULT, 1.0) * 15

    est_mcap = closes[-1] * best_avg_vol * 30
    if est_mcap > 0 and est_mcap < 50_000_000:
        mcap_score = 20
    elif est_mcap < 100_000_000:
        mcap_score = 15
    elif est_mcap < 200_000_000:
        mcap_score = 10
    elif est_mcap < 500_000_000:
        mcap_score = 5
    else:
        mcap_score = 0

    total_score = days_score + range_score + vol_score + breakout_score + mcap_score
    flatness_bonus = max(0, (1 - abs(best_slope_pct) / 20)) * 5
    total_score += flatness_bonus

    if vol_breakout >= VOL_BREAKOUT_MULT:
        status = "firing"
    elif vol_breakout >= 1.5:
        status = "warming"
    else:
        status = "sleeping"

    return {
        "symbol": symbol,
        "coin": coin,
        "sideways_days": best_sideways,
        "range_pct": best_range,
        "slope_pct": best_slope_pct,
        "low_price": best_low,
        "high_price": best_high,
        "avg_vol": best_avg_vol,
        "current_price": closes[-1],
        "recent_vol": recent_vol,
        "vol_breakout": vol_breakout,
        "score": total_score,
        "status": status,
        "data_days": n,
    }


def scan_accumulation_pool():
    """扫描全市场，找正在被收筹的币（并发拉取K线）"""
    logger.info("📊 扫描全市场收筹标的...")

    symbols = get_all_perp_symbols()
    logger.info(f"  共 {len(symbols)} 个合约，{MAX_WORKERS} 线程并发拉取")

    results = []
    done = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_fetch_kline, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            done += 1
            sym, klines = fut.result()
            if klines and isinstance(klines, list):
                r = analyze_accumulation(sym, klines)
                if r:
                    results.append(r)
            if done % 100 == 0:
                logger.info(f"  进度: {done}/{len(symbols)}... 已发现{len(results)}个")

    results.sort(key=lambda x: x["score"], reverse=True)
    logger.info(f"  ✅ 发现 {len(results)} 个收筹标的")
    return results


def scan_oi_changes(watchlist_symbols, ticker_map=None, funding_map=None):
    """对标的池内的币扫描OI异动（复用已有行情数据避免重复请求）"""
    logger.info(f"📊 扫描OI异动（{len(watchlist_symbols)}个标的）...")
    alerts = []
    for sym in watchlist_symbols:
        oi_hist = api_get("/futures/data/openInterestHist", {
            "symbol": sym, "period": "1h", "limit": 3
        })
        if not oi_hist or len(oi_hist) < 2:
            continue
        prev_oi = float(oi_hist[-2]["sumOpenInterestValue"])
        curr_oi = float(oi_hist[-1]["sumOpenInterestValue"])
        if prev_oi <= 0 or curr_oi < MIN_OI_USD:
            continue
        delta_pct = ((curr_oi - prev_oi) / prev_oi) * 100
        if abs(delta_pct) >= MIN_OI_DELTA_PCT:
            # 优先使用已有数据，避免逐币请求
            tk = ticker_map.get(sym) if ticker_map else None
            if tk:
                price = tk["price"]
                vol_24h = tk["vol"]
                px_chg = tk["px_chg"]
            else:
                ticker = api_get("/fapi/v1/ticker/24hr", {"symbol": sym})
                if not ticker:
                    continue
                price = float(ticker["lastPrice"])
                vol_24h = float(ticker["quoteVolume"])
                px_chg = float(ticker["priceChangePercent"])

            fr = funding_map.get(sym, 0) if funding_map else 0
            if fr == 0 and not funding_map:
                funding = api_get("/fapi/v1/fundingRate", {"symbol": sym, "limit": 1})
                fr = float(funding[0]["fundingRate"]) if funding else 0

            coin = sym.replace("USDT", "")
            alerts.append({
                "symbol": sym, "coin": coin,
                "price": price, "oi_usd": curr_oi,
                "oi_delta_pct": delta_pct, "oi_delta_usd": curr_oi - prev_oi,
                "vol_24h": vol_24h, "px_chg_pct": px_chg, "funding_rate": fr,
            })

    alerts.sort(key=lambda x: abs(x["oi_delta_pct"]), reverse=True)
    logger.info(f"  ✅ 发现 {len(alerts)} 个OI异动")
    return alerts
