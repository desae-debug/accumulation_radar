# 庄家收筹雷达

全市场扫描 Binance 合约的庄家吸筹信号，横盘检测、OI 异动、三策略评分，结果推到 Telegram。

## 逻辑链路

庄家拉盘前需要先收筹，收筹期通常 3-4 个月，这段时间价格横盘、成交量萎缩。等仓位够了，OI 突然放大就是大资金进场，离拉盘不远了。两个信号叠加是最强触发。

要抓的是庄家盘（RAVE 138x、STO 38x 这种），不是基本面慢涨。空头燃料也很关键，涨完之后没人做空就没燃料继续拉。

两个模块：

- **pool**（每天跑一次）：扫描 535 个 USDT 永续合约，找横盘吸筹的币，写入标的池
- **oi**（每小时跑一次）：对标的池 + 全市场 top100 做 OI 异动扫描，三策略评分，推送到 Telegram

## 三策略评分

### 追多：费率排名（短线轧空）

前提：涨 >3%、费率为负、成交额 >$1M

费率越负说明做空的人越多，一旦价格涨上去空头就要平仓，形成轧空。费率趋势分四档：

| 趋势 | 含义 |
|------|------|
| 加速 | 费率比上期更负，空头还在加仓 |
| 变负 | 费率从正转负，刚有人开始做空 |
| 稳定 | 费率变化不大 |
| 回升 | 空头在减仓，燃料变少 |

### 综合：四维均衡（各 25 分 = 100 分）

| 维度 | 打分逻辑 | 满分 |
|------|---------|------|
| 费率 | 越负分越高 | 25 |
| 市值 | 越低分越高（用真实流通市值） | 25 |
| 横盘天数 | 越久分越高 | 25 |
| OI 变化 | 变化幅度越大分越高 | 25 |

总分 < 25 不入榜。

### 埋伏：提前布局（中长线）

只看收筹池内的币，涨幅超过 50% 的排除。权重偏市值和 OI：

| 维度 | 权重 | 打分逻辑 |
|------|------|---------|
| 市值 | 35 | <$50M 满分 |
| OI 异动 | 30 | 有"暗流"加成（OI 涨但价格没动） |
| 横盘天数 | 20 | >=120 天满分 |
| 负费率 | 15 | 有负费率是加分项 |

总分 < 20 不入榜。

### 自动提醒

报告底部的"值得关注"会交叉验证几个高优先级信号：

- 热度 + 收筹池重叠（热度是 OI 的领先指标）
- 热度 + OI 同时上涨（正在发生）
- 费率加速恶化（空头涌入）
- 多策略同时上榜
- 暗流信号（OI 变但价格没动）
- 低市值 + OI 异动

## 数据源

全部是 Binance 和 CoinGecko 的免费公开 API，不需要 API Key：

| 数据 | 来源 | 说明 |
|------|------|------|
| 真实流通市值 | Binance 现货 API | 一次请求拿 400+ 币全量市值 |
| K 线 / 24h 行情 | Binance 合约 API | 180 日 K 线 + 实时行情 |
| OI 历史 | Binance 合约 API | 6 小时 OI 变化 + CMC 流通量 |
| 资金费率 | Binance 合约 API | 全量费率 + 5 期历史 |
| 热门币 | CoinGecko Trending | 热搜排行 |

市值三级回退：Binance 现货 API → CMC 流通量 x 价格 → 粗估公式。

## 安装

```bash
git clone https://github.com/chencore/accumulation_radar.git
cd accumulation_radar

pip install -r requirements.txt
```

Python 3.8+ 就行，唯一的外部依赖是 requests。

### 配置 Telegram 推送（可选）

```bash
cp .env.example .env.oi
```

编辑 `.env.oi`，填入 `TG_BOT_TOKEN` 和 `TG_CHAT_ID`。不配的话报告会打印到 stdout。

创建 Bot 的步骤：
1. 找 [@BotFather](https://t.me/BotFather) 发 `/newbot`，拿到 Token
2. 给 bot 发一条消息
3. 访问 `https://api.telegram.org/bot<TOKEN>/getUpdates` 拿到你的 Chat ID

## 使用

```bash
# 扫描收筹标的池（每天跑一次）
python -m accumulation_radar pool

# 三策略评分 + OI 异动（每小时跑一次）
python -m accumulation_radar oi

# 两个都跑
python -m accumulation_radar full
```

### Crontab 配置

```crontab
0 10 * * *  cd /path/to/accumulation-radar && python -m accumulation_radar pool >> accumulation.log 2>&1
30 * * * *  cd /path/to/accumulation-radar && python -m accumulation_radar oi >> accumulation_oi.log 2>&1
```

## 推送示例

```
🏦 庄家雷达 三策略+热度
⏰ 2026-04-24 09:51 CST

🔥 热度榜 (CG趋势+成交量暴增)
  RED      ~$57M 涨+17% | 🌐CG热搜 📈放量 ⚡OI+22% 🧊-1.00%
  KAT      ~$36M 涨+45% | 📈放量 ⚡OI+33%

🔥 追多 (按费率排名)
  RED     费率-1.003% 🔥加速 | 涨+17% | ~$57M
  KAT     费率-0.627% 🔥加速 | 涨+45% | ~$36M
  MOVR    费率-0.146% 🔥加速 | 涨+56% | ~$30M

📊 综合 (费率+市值+横盘+OI 各25)
  MOVR    86分 | 🧊-0.15% 💎$30M 💤71天 ⚡OI-22%
  KAT     75分 | 🧊-0.63% 💎$36M ⚡OI+33%

🎯 埋伏 (市值35+OI30+横盘20+费率15)
  RARE    82分 | ~$18M OI-24% 横盘75天
  SAGA    74分 | ~$15M OI+4% 🎯暗流 横盘77天

💡 值得关注
  🔥 RED 费率-1.003%加速恶化，空头涌入中
  🎯 SAGA 暗流！OI+4%但价格没动，市值仅$15M
```

## OI 异动信号解读

| OI | 价格 | 含义 |
|----|------|------|
| 上升 | 上升 | 主动加仓做多，趋势确立 |
| 上升 | 下降 | 主动加仓做空 |
| 上升 | 持平 | 暗流，庄家在建仓 |
| 下降 | 上升 | 轧空，空头爆仓 |
| 下降 | 下降 | 多头止损 |

其中 OI 上升 + 价格持平（暗流）是最典型的庄家收筹信号。

## 项目结构

```
accumulation_radar/
├── __main__.py   # 入口，流程编排
├── config.py     # 常量、环境变量、日志
├── api.py        # Binance API 封装
├── db.py         # SQLite 读写
├── scanner.py    # 横盘收筹扫描 + OI 扫描
├── market.py     # 行情/费率/市值/热度获取
├── strategy.py   # 三策略评分
├── report.py     # 报告生成
└── notify.py     # Telegram 推送
```

## 成本

$0/月。纯 Python + 公开 API，没有 AI 调用，没有付费 Key。

## License

MIT
