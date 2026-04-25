from datetime import datetime, timezone, timedelta


def format_usd(v):
    if v >= 1e9: return f"${v/1e9:.1f}B"
    if v >= 1e6: return f"${v/1e6:.1f}M"
    if v >= 1e3: return f"${v/1e3:.0f}K"
    return f"${v:.0f}"


def mcap_str(v):
    if v >= 1e6: return f"${v/1e6:.0f}M"
    if v >= 1e3: return f"${v/1e3:.0f}K"
    return f"${v:.0f}"


def fmt_price(v):
    """智能价格格式：大币2位，小币自适应"""
    if v <= 0:
        return "$0"
    if v >= 100:
        return f"${v:.2f}"
    if v >= 1:
        return f"${v:.4f}"
    if v >= 0.01:
        return f"${v:.6f}"
    # 极小币：去前导零后取4位有效数字
    s = f"{v:.8f}".rstrip("0")
    return f"${s}"


# 状态显示映射
STATUS_DISPLAY = {
    "firing": "🔥放量启动",
    "warming": "⚡开始放量",
    "sleeping": "💤收筹中",
}


def build_pool_report(results):
    """生成收筹标的池报告"""
    if not results:
        return ""

    now = datetime.now(timezone(timedelta(hours=8)))
    lines = [
        f"🏦 **庄家收筹雷达** — 标的池更新",
        f"⏰ {now.strftime('%Y-%m-%d %H:%M')} CST",
        f"━━━━━━━━━━━━━━━━━━",
        f"扫描 {len(results)} 个合约，发现标的：",
        "",
    ]

    firing = [r for r in results if r["status"] == "firing"]
    warming = [r for r in results if r["status"] == "warming"]
    sleeping = [r for r in results if r["status"] == "sleeping"]

    if firing:
        lines.append(f"🔥 **放量启动** ({len(firing)}个) — 最高优先级！")
        for r in firing[:10]:
            lines.append(
                f"  🔥 **{r['coin']}** | 分:{r['score']:.0f} | "
                f"横盘{r['sideways_days']}天 | 波动{r['range_pct']:.0f}% | "
                f"Vol放大{r['vol_breakout']:.1f}x"
            )
            lines.append(
                f"     {fmt_price(r['current_price'])} | "
                f"区间: {fmt_price(r['low_price'])}~{fmt_price(r['high_price'])} | "
                f"日均Vol: {format_usd(r['avg_vol'])}"
            )
        lines.append("")

    if warming:
        lines.append(f"⚡ **开始放量** ({len(warming)}个) — 关注中")
        for r in warming[:10]:
            lines.append(
                f"  ⚡ {r['coin']} | 分:{r['score']:.0f} | "
                f"横盘{r['sideways_days']}天 | 波动{r['range_pct']:.0f}% | "
                f"Vol{r['vol_breakout']:.1f}x"
            )
        lines.append("")

    if sleeping:
        lines.append(f"💤 **收筹中** ({len(sleeping)}个) — 持续监控")
        for r in sleeping[:15]:
            lines.append(
                f"  💤 {r['coin']} | 分:{r['score']:.0f} | "
                f"横盘{r['sideways_days']}天 | 波动{r['range_pct']:.0f}% | "
                f"日均Vol {format_usd(r['avg_vol'])}"
            )

    return "\n".join(lines)


def build_strategy_report(coin_data, chase, combined, ambush):
    """生成三策略+热度+值得关注报告"""
    now = datetime.now(timezone(timedelta(hours=8)))
    lines = [
        f"🏦 **庄家雷达** 三策略+热度",
        f"⏰ {now.strftime('%Y-%m-%d %H:%M')} CST",
    ]

    # 热度榜
    hot_coins = sorted(
        [d for d in coin_data.values() if d["heat"] > 0],
        key=lambda x: x["heat"], reverse=True
    )
    if hot_coins:
        lines.append(f"\n🔥 **热度榜** (CG趋势+成交量暴增)")
        for s in hot_coins[:8]:
            tags = []
            if s["in_cg"]: tags.append("🌐CG热搜")
            if s["vol_surge"]: tags.append("📈放量")
            oi_tag = f"OI{s['d6h']:+.0f}%" if abs(s["d6h"]) >= 3 else ""
            if oi_tag: tags.append(f"⚡{oi_tag}")
            if s["in_pool"]: tags.append(f"💤池{s['sw_days']}天")
            fr_tag = f"🧊{s['fr_pct']:.2f}%" if s["fr_pct"] < -0.03 else ""
            if fr_tag: tags.append(fr_tag)
            lines.append(
                f"  {s['coin']:<8} ~{mcap_str(s['est_mcap'])} 涨{s['px_chg']:+.0f}% | {' '.join(tags)}"
            )

    # 追多
    lines.append(f"\n🔥 **追多** (费率负+OI确认+量能)")
    if chase:
        for s in chase[:8]:
            extras = [s["trend"]]
            if s.get("oi_confirm"): extras.append(s["oi_confirm"])
            if s.get("vol_tag"): extras.append(s["vol_tag"])
            lines.append(
                f"  {s['coin']:<7} 费率{s['fr_pct']:+.3f}% {' '.join(extras)}"
                f" | 涨{s['px_chg']:+.0f}% | ~{mcap_str(s['est_mcap'])}"
            )
    else:
        lines.append("  暂无（需涨>3%+费率负）")

    # 综合
    lines.append(f"\n📊 **综合** (费率30+OI30+市值20+横盘20)")
    for s in combined[:8]:
        dims = []
        if s["f_sc"] >= 15: dims.append(f"🧊{s['fr_pct']:.2f}%")
        if s["m_sc"] >= 10: dims.append(f"💎{mcap_str(s['est_mcap'])}")
        if s["s_sc"] >= 10: dims.append(f"💤{s['sw_days']}天")
        if s["o_sc"] >= 15: dims.append(f"⚡OI{s['d6h']:+.0f}%")
        if s.get("bonus"): dims.append(f"🔗联{s['bonus']}")
        lines.append(f"  {s['coin']:<7} {s['total']}分 | {' '.join(dims)}")

    # 埋伏
    lines.append(f"\n🎯 **埋伏** (市值30+OI25+横盘20+暗流15+热度10)")
    for s in ambush[:8]:
        tags = [f"~{mcap_str(s['est_mcap'])}"]
        if abs(s["d6h"]) >= 2: tags.append(f"OI{s['d6h']:+.0f}%")
        if s.get("dc_sc", 0) >= 10: tags.append("🎯暗流")
        if s["sw_days"] >= 45: tags.append(f"横盘{s['sw_days']}天")
        if s["fr_pct"] < -0.01: tags.append(f"费率{s['fr_pct']:.2f}%")
        if s.get("h_sc", 0) >= 5: tags.append(f"🔥热{s.get('h_sc',0):.0f}")
        lines.append(f"  {s['coin']:<7} {s['total']}分 | {' '.join(tags)}")

    # 值得关注
    _build_highlights(lines, coin_data, chase, combined, ambush)

    # 图例
    lines.append(f"\n📖 **图例**")
    lines.append("  🔥热度=CG热搜+成交量暴增(OI领先指标)")
    lines.append("  费率负=空头燃料 | 💎市值 | 💤横盘(收筹)")
    lines.append("  🔥💤热度+收筹=最强预判 | 🔥⚡热度+OI=正在发生")

    return "\n".join(lines)


def _build_highlights(lines, coin_data, chase, combined, ambush):
    """生成值得关注提醒"""
    highlights = []
    highlighted_coins = set()

    def add_highlight(text):
        parts = text.split()
        coin = parts[1] if len(parts) > 1 else ""
        if coin and coin in highlighted_coins:
            return
        highlighted_coins.add(coin)
        highlights.append(text)

    # 热度+收筹池重叠
    hot_pool = [d for d in coin_data.values() if d["heat"] > 0 and d["in_pool"]]
    for s in sorted(hot_pool, key=lambda x: x["heat"], reverse=True)[:2]:
        tags = []
        if s["in_cg"]: tags.append("CG热搜")
        if s["vol_surge"]: tags.append("放量")
        add_highlight(f"🔥💤 {s['coin']} 热度({'+'.join(tags)})+收筹{s['sw_days']}天=OI将涨")

    # 热度+OI双涨
    hot_oi = [d for d in coin_data.values() if d["heat"] > 0 and d["d6h"] > 5]
    for s in sorted(hot_oi, key=lambda x: x["d6h"], reverse=True)[:2]:
        if s["coin"] not in highlighted_coins:
            add_highlight(f"🔥⚡ {s['coin']} 热度+OI{s['d6h']:+.0f}%双涨！")

    # 追多费率加速恶化
    chase_fire = [s for s in chase[:5] if "加速" in s.get("trend", "")]
    for s in chase_fire[:2]:
        add_highlight(f"🔥 {s['coin']} 费率{s['fr_pct']:.3f}%加速恶化，空头涌入中")

    # 追多+综合双榜
    chase_coins = set(s["coin"] for s in chase[:10])
    combined_coins = set(s["coin"] for s in combined[:10])
    for c in list(chase_coins & combined_coins)[:2]:
        add_highlight(f"⭐ {c} 追多+综合双榜上榜")

    # 埋伏暗流
    ambush_dark = [s for s in ambush[:10] if s["d6h"] > 2 and abs(s["px_chg"]) < 5]
    for s in ambush_dark[:2]:
        add_highlight(f"🎯 {s['coin']} 暗流！OI{s['d6h']:+.0f}%但价格没动，市值仅{mcap_str(s['est_mcap'])}")

    # 埋伏低市值+OI异动
    ambush_gem = [s for s in ambush[:10] if s["est_mcap"] < 100e6 and abs(s["d6h"]) >= 3]
    for s in ambush_gem[:2]:
        if s["coin"] not in highlighted_coins:
            add_highlight(f"💎 {s['coin']} 低市值{mcap_str(s['est_mcap'])}+OI{s['d6h']:+.0f}%，埋伏首选")

    if highlights:
        lines.append(f"\n💡 **值得关注**")
        for h in highlights[:7]:
            lines.append(f"  {h}")
