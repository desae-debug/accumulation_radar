from .api import api_get
from .config import logger


# ── 工具函数 ──

def _curve(max_pts, lo, hi, value, power=1.0):
    """连续评分曲线：value 在 [lo, hi] 区间从 0 线性到 max_pts"""
    if value <= lo:
        return 0
    if value >= hi:
        return max_pts
    return max_pts * ((value - lo) / (hi - lo)) ** power


# ── 策略1: 追多（短线轧空） ──

def score_chase(coin_data):
    """费率负+OI涨+量确认 → 轧空信号强度排序

    过滤: 涨>3% + 费率<-0.005% + 量>$1M
    排序: 费率负深度(主) + OI涨确认(辅) + 量能确认(辅)
    """
    chase = []
    for sym, d in coin_data.items():
        if d["px_chg"] <= 3 or d["fr_pct"] >= -0.005 or d["vol"] <= 1_000_000:
            continue

        fr_hist = api_get("/fapi/v1/fundingRate", {"symbol": sym, "limit": 5})
        fr_rates = [float(f["fundingRate"]) * 100 for f in fr_hist] if fr_hist else [d["fr_pct"]]
        fr_prev = fr_rates[-2] if len(fr_rates) >= 2 else d["fr_pct"]
        fr_delta = d["fr_pct"] - fr_prev

        # 费率趋势
        if fr_delta < -0.05:
            trend = "🔥加速"
        elif fr_delta < -0.01:
            trend = "⬇️变负"
        elif abs(fr_delta) < 0.01:
            trend = "➡️"
        else:
            trend = "⬆️回升"

        # OI确认: 轧空需要OI涨（空头在加仓）
        oi_confirm = ""
        if d["d6h"] > 5:
            oi_confirm = "⚡OI强"
        elif d["d6h"] > 2:
            oi_confirm = "⚡OI涨"

        # 量能确认
        vol_tag = "📈放量" if d.get("vol_surge") else ""

        chase.append({**d, "fr_delta": fr_delta, "trend": trend,
                      "oi_confirm": oi_confirm, "vol_tag": vol_tag,
                      "rates": " → ".join(f"{x:.3f}" for x in fr_rates[-3:])})

    # 排序: 费率最负优先, OI涨的排前面
    chase.sort(key=lambda x: (x["fr_pct"], -x.get("d6h", 0)))
    return chase


# ── 策略2: 综合（四维均衡+交互） ──

def score_combined(coin_data):
    """费率30 + OI 30 + 市值20 + 横盘20 = 100分

    - 连续曲线替代阶跃，避免排名抖动
    - OI方向区分：OI涨+价平(暗流)比OI跌得分更高
    - 费率+OI交互加成
    """
    combined = []
    for sym, d in coin_data.items():
        # 费率 (30分): 越负越好
        fr = d["fr_pct"]
        if fr >= 0:
            f_sc = 0
        elif fr >= -0.01:
            f_sc = _curve(15, 0, 0.01, abs(fr))
        elif fr >= -0.1:
            f_sc = 15 + _curve(10, 0.01, 0.1, abs(fr))
        else:
            f_sc = 25 + _curve(5, 0.1, 0.5, min(abs(fr), 0.5))

        # OI异动 (30分): 涨幅>跌幅, 暗流加分
        d6h = d["d6h"]
        abs6 = abs(d6h)
        if d6h > 0:
            # OI涨: 主力开仓信号
            o_sc = _curve(30, 1, 15, abs6, 0.7)
            if abs(d["px_chg"]) < 5 and abs6 >= 2:
                o_sc = min(o_sc + 3, 30)  # 暗流微加
        else:
            # OI跌: 减仓/平仓，信号弱
            o_sc = _curve(18, 1, 15, abs6, 0.8)

        # 市值 (20分): 越小越容易拉
        mc = d["est_mcap"]
        if mc <= 0 or mc >= 1e9:
            m_sc = 0
        else:
            m_sc = 20 * (1 - mc / 1e9) ** 0.5

        # 横盘 (20分): 越长越好
        s_sc = _curve(20, 30, 120, d["sw_days"])

        # 交互: 费率负+OI涨 = 空头被套+主力加仓
        bonus = 0
        if fr < -0.03 and d6h > 3:
            bonus = 5

        total = f_sc + o_sc + m_sc + s_sc + bonus
        if total < 25:
            continue

        combined.append({**d, "total": round(total),
                         "f_sc": round(f_sc), "m_sc": round(m_sc),
                         "s_sc": round(s_sc), "o_sc": round(o_sc),
                         "bonus": bonus})

    combined.sort(key=lambda x: x["total"], reverse=True)
    return combined


# ── 策略3: 埋伏（中长线早期布局） ──

def score_ambush(coin_data):
    """市值30 + OI方向25 + 横盘20 + 暗流15 + 热度10 = 100分

    - 暗流从bonus升为独立维度（核心论点）
    - OI涨/跌方向区分
    - 热度作为OI领先指标
    """
    ambush = []
    for sym, d in coin_data.items():
        if not d["in_pool"] or d["px_chg"] > 50:
            continue

        # 市值 (30分)
        mc = d["est_mcap"]
        if mc <= 0 or mc >= 1e9:
            m_sc = 0
        else:
            m_sc = 30 * (1 - mc / 1e9) ** 0.5

        # OI方向 (25分): 只奖励OI上涨
        d6h = d["d6h"]
        if d6h > 0:
            o_sc = _curve(25, 1, 12, d6h, 0.7)
        else:
            o_sc = _curve(8, 1, 12, abs(d6h), 0.8)

        # 横盘 (20分)
        s_sc = _curve(20, 30, 120, d["sw_days"])

        # 暗流 (15分): OI涨+价平 = 庄家收筹核心信号
        if d6h > 2 and abs(d["px_chg"]) < 5:
            dc_sc = _curve(15, 2, 8, d6h)
        else:
            dc_sc = 0

        # 热度 (10分): OI领先指标
        h_sc = _curve(10, 10, 60, d["heat"]) if d["heat"] > 0 else 0

        total = m_sc + o_sc + s_sc + dc_sc + h_sc
        if total < 20:
            continue

        ambush.append({**d, "total": round(total),
                       "m_sc": round(m_sc), "o_sc": round(o_sc),
                       "s_sc": round(s_sc), "dc_sc": round(dc_sc),
                       "h_sc": round(h_sc)})

    ambush.sort(key=lambda x: x["total"], reverse=True)
    return ambush
