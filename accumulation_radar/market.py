from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from .api import api_get
from .config import logger

OI_WORKERS = 4


def fetch_market_data():
    """拉全市场行情 + 费率 + 真实市值"""
    tickers_raw = api_get("/fapi/v1/ticker/24hr")
    premiums_raw = api_get("/fapi/v1/premiumIndex")
    if not tickers_raw or not premiums_raw:
        return None, None, None

    ticker_map = {}
    for t in tickers_raw:
        if t["symbol"].endswith("USDT"):
            ticker_map[t["symbol"]] = {
                "px_chg": float(t["priceChangePercent"]),
                "vol": float(t["quoteVolume"]),
                "price": float(t["lastPrice"]),
            }

    funding_map = {}
    for p in premiums_raw:
        if p["symbol"].endswith("USDT"):
            funding_map[p["symbol"]] = float(p["lastFundingRate"])

    mcap_map = {}
    try:
        _r = requests.get(
            "https://www.binance.com/bapi/composite/v1/public/marketing/symbol/list",
            timeout=10,
        )
        if _r.status_code == 200:
            for item in _r.json().get("data", []):
                name = item.get("name", "")
                mc = item.get("marketCap", 0)
                if name and mc:
                    mcap_map[name] = float(mc)
            logger.info(f"✅ 拉到 {len(mcap_map)} 个币的真实市值")
    except Exception as e:
        logger.warning(f"⚠️ 市值API失败，走fallback: {e}")

    return ticker_map, funding_map, mcap_map


def fetch_heat_data(ticker_map):
    """拉热度数据: CoinGecko Trending + 成交量暴增"""
    heat_map = {}
    cg_trending = set()

    try:
        _r = requests.get("https://api.coingecko.com/api/v3/search/trending", timeout=10)
        if _r.status_code == 200:
            for item in _r.json().get("coins", []):
                sym = item["item"]["symbol"].upper()
                rank = item["item"].get("score", 99)
                cg_trending.add(sym)
                heat_map[sym] = heat_map.get(sym, 0) + max(50 - rank * 3, 10)
            logger.info(f"🔥 CoinGecko Trending: {len(cg_trending)}个币")
    except Exception as e:
        logger.warning(f"⚠️ CG Trending失败: {e}")

    vol_surge_coins = set()
    top_vol_syms = sorted(ticker_map.items(), key=lambda x: x[1]["vol"], reverse=True)[:200]

    def _check_vol_surge(sym_tk):
        sym, tk = sym_tk
        coin = sym.replace("USDT", "")
        vol_24h = tk["vol"]
        if vol_24h <= 50_000_000:
            return None
        kl = api_get("/fapi/v1/klines", {"symbol": sym, "interval": "1d", "limit": 6})
        if kl and len(kl) >= 5:
            avg_5d = sum(float(k[7]) for k in kl[:-1]) / (len(kl) - 1)
            if avg_5d > 0:
                ratio = vol_24h / avg_5d
                if ratio >= 2.5:
                    return coin, ratio
        return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_check_vol_surge, item) for item in top_vol_syms]
        for fut in as_completed(futures):
            result = fut.result()
            if result:
                coin, ratio = result
                vol_surge_coins.add(coin)
                heat_map[coin] = heat_map.get(coin, 0) + min(ratio * 10, 50)

    logger.info(f"📈 成交量暴增(≥2.5x): {len(vol_surge_coins)}个币")
    dual_heat = cg_trending & vol_surge_coins
    if dual_heat:
        for coin in dual_heat:
            heat_map[coin] = heat_map.get(coin, 0) + 20
        logger.info(f"🔥🔥 双重热度: {dual_heat}")

    return heat_map, cg_trending, vol_surge_coins


def _fetch_oi(sym):
    oi_hist = api_get("/futures/data/openInterestHist", {
        "symbol": sym, "period": "1h", "limit": 6
    })
    if oi_hist and len(oi_hist) >= 2:
        curr = float(oi_hist[-1]["sumOpenInterestValue"])
        prev_1h = float(oi_hist[-2]["sumOpenInterestValue"])
        prev_6h = float(oi_hist[0]["sumOpenInterestValue"])
        d1h = ((curr - prev_1h) / prev_1h * 100) if prev_1h > 0 else 0
        d6h = ((curr - prev_6h) / prev_6h * 100) if prev_6h > 0 else 0
        circ_supply = float(oi_hist[-1].get("CMCCirculatingSupply", 0))
        return sym, {"oi_usd": curr, "d1h": d1h, "d6h": d6h, "circ_supply": circ_supply}
    return sym, None


def scan_oi_history(scan_syms):
    """批量并发扫描OI历史"""
    oi_map = {}
    syms = list(scan_syms)
    logger.info(f"  扫描 {len(syms)} 个OI历史，{OI_WORKERS} 线程并发")

    with ThreadPoolExecutor(max_workers=OI_WORKERS) as pool:
        futures = [pool.submit(_fetch_oi, sym) for sym in syms]
        for fut in as_completed(futures):
            sym, data = fut.result()
            if data:
                oi_map[sym] = data

    logger.info(f"  ✅ 获取 {len(oi_map)} 个OI数据")
    return oi_map


def build_coin_data(pool_map, oi_map, ticker_map, funding_map, mcap_map,
                    heat_map, cg_trending, vol_surge_coins):
    """合并所有维度数据"""
    all_syms = set(list(pool_map.keys()) + list(oi_map.keys()))
    coin_data = {}
    for sym in all_syms:
        tk = ticker_map.get(sym, {})
        if not tk:
            continue
        pool = pool_map.get(sym, {})
        oi = oi_map.get(sym, {})
        fr = funding_map.get(sym, 0)
        coin = sym.replace("USDT", "")

        d6h = oi.get("d6h", 0)
        fr_pct = fr * 100
        oi_usd = oi.get("oi_usd", 0)

        if coin in mcap_map:
            est_mcap = mcap_map[coin]
        else:
            circ_supply = oi.get("circ_supply", 0)
            price = tk.get("price", 0)
            if circ_supply > 0 and price > 0:
                est_mcap = circ_supply * price
            else:
                est_mcap = max(tk["vol"] * 0.3, oi_usd * 2) if oi_usd > 0 else tk["vol"] * 0.3

        sw_days = pool.get("sideways_days", 0) if pool else 0
        heat = heat_map.get(coin, 0)

        coin_data[sym] = {
            "coin": coin, "sym": sym,
            "px_chg": tk["px_chg"], "vol": tk["vol"],
            "fr_pct": fr_pct, "d6h": d6h,
            "oi_usd": oi_usd, "est_mcap": est_mcap,
            "sw_days": sw_days,
            "in_pool": bool(pool), "heat": heat,
            "in_cg": coin in cg_trending,
            "vol_surge": coin in vol_surge_coins,
        }
    return coin_data
