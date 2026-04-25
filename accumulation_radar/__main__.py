"""庄家收筹雷达 — 入口

用法: python -m accumulation_radar [pool|oi|full]
"""
import sys
from datetime import datetime

from .config import logger
from .db import get_db, save_watchlist, load_watchlist_symbols, load_pool_map
from .scanner import scan_accumulation_pool
from .market import fetch_market_data, fetch_heat_data, scan_oi_history, build_coin_data
from .strategy import score_chase, score_combined, score_ambush
from .report import build_pool_report, build_strategy_report
from .notify import send_telegram


def run_pool(conn):
    """模块A: 更新收筹标的池"""
    results = scan_accumulation_pool()
    if results:
        save_watchlist(conn, results)
        report = build_pool_report(results)
        if report:
            send_telegram(report)


def run_oi(conn):
    """模块B: OI异动 + 三策略评分"""
    watchlist = load_watchlist_symbols(conn)
    if not watchlist:
        logger.warning("⚠️ 标的池为空，先运行 pool 模式")
        return

    # 1. 全市场数据
    ticker_map, funding_map, mcap_map = fetch_market_data()
    if not ticker_map:
        logger.error("❌ API失败")
        return

    # 2. 热度数据
    heat_map, cg_trending, vol_surge_coins = fetch_heat_data(ticker_map)

    # 3. 收筹池 + OI历史
    pool_map = load_pool_map(conn)
    scan_syms = set()
    for sym, pd in pool_map.items():
        st = pd.get("status", "")
        if st in ("firing", "warming"):
            scan_syms.add(sym)
    top_by_vol = sorted(ticker_map.items(), key=lambda x: x[1]["vol"], reverse=True)[:100]
    for sym, _ in top_by_vol:
        scan_syms.add(sym)

    oi_map = scan_oi_history(scan_syms)

    # 4. 合并数据 + 三策略评分
    coin_data = build_coin_data(
        pool_map, oi_map, ticker_map, funding_map, mcap_map,
        heat_map, cg_trending, vol_surge_coins,
    )
    chase = score_chase(coin_data)
    combined = score_combined(coin_data)
    ambush = score_ambush(coin_data)

    # 5. 生成报告并推送
    report = build_strategy_report(coin_data, chase, combined, ambush)
    send_telegram(report)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    logger.info(f"🏦 庄家收筹雷达 v2 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 模式: {mode}")

    conn = get_db()
    try:
        if mode in ("full", "pool"):
            run_pool(conn)
        if mode in ("full", "oi"):
            run_oi(conn)
    finally:
        conn.close()

    logger.info("✅ 完成")


if __name__ == "__main__":
    main()
