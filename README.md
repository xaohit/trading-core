# Trading Core — 自主进化的 AI 量化交易系统

> **Vision**: 一个真正会自己学习的交易 Agent。从新手到老手，每一次决策都留痕，从失败中提炼经验，主动调整判断逻辑。

## 🌟 系统定位

这不是一个普通的静态量化脚本，而是一个**基于 Agent (MAKIMA/Hermes) 的自主进化系统**。
- **Claude** 负责搭建系统架构与规则底线。
- **Hermes** 负责 7x24 小时的策略分析与执行。
- **95% 的计算在本地运行 (0 Token 消耗)**，仅在深度反思和规则进化时调用 LLM。

## 🚀 核心能力 (Current Features)

### 🧠 Agent 学习闭环 (Phase 8)
- **深度决策快照 (Deep Snapshots)**: 记录每一笔决策的宏观环境、市场状态（趋势/震荡）、以及 AI 的原始推理过程。
- **经验库检索 (Experience Retrieval)**: 开仓前自动调取 Top 3 历史相似案例，避免重复犯错。
- **市场状态识别**: 自动判断当前是 Trend / Range / Volatile 模式，智能过滤不适用的历史经验。
- **Agent 工具接口**: 提供清晰的 API (`agent_tools.py`) 供外部 Agent (OpenClaw/Hermes) 调用。

### 📊 市场感知层 (Phase 1-3)
- **多维数据**: 实时获取 Binance 的 OI 变化、资金费率、多空持仓比 (LSR)、主动买卖比、深度不平衡等。
- **信号评分**: 0-100 分综合打分，包含 verdict、tags、OI 背离检测。

### 🛡️ 多层级风控 (Phase 4B, 7A)
- **TP 金字塔**: TP1 (1.5R) 减仓并推保本，TP2 (3R) 减仓开启追踪止损。
- **动态风控**: 板块集中度限制、单日亏损熔断、止损后冷却机制。
- **入场质量门**: 7 项硬性否决 + 7 项质量检查，拒绝低质量交易。

### 📜 历史回测 (Phase 7E)
- **K 线回放引擎**: 支持任意币种、任意时间段的策略验证。
- **经验注入**: 将历史回测结果转化为"初始经验库"，让 Agent 上岗即具备历史智慧。

## 🏗️ 架构与模块

```text
┌───────────────┐       ┌──────────────┐       ┌───────────────┐
│  Agent (Hermes) │ ────→ │ Agent Tools  │ ────→ │ Trading Core  │
│  分析与反思     │       │  (Skill API) │       │  (Engine)     │
└───────────────┘       └──────────────┘       └───────┬───────┘
                                                       │
                                                       ▼
┌───────────────────────────────────────────────────────────────────────┐
│                          底层系统模块                                  │
│  [Market Snapshot] → [Signals] → [Scanner] → [Risk] → [Execution]     │
└───────────────────────────────────────────────────────────────────────┘
```

### 模块清单
| 模块 | 描述 |
| :--- | :--- |
| `agent_tools.py` | Agent 技能接口，提供 `get_market_analysis`, `record_agent_decision` 等工具 |
| `scanner.py` | 核心调度器：过滤、评分、风控检查、开仓 |
| `market_snapshot.py` | 获取 Binance 深度数据（OI, LSR, Taker, ATR） |
| `decision_memory.py` | 决策记忆：快照记录、结果回顾、经验归档 |
| `backtest.py` | 历史回测引擎，支持 ATR 与固定仓位对比 |
| `market_state.py` | 识别市场状态 (Trend/Range/Volatile)，辅助经验匹配 |
| `reflection.py` | 失败归档、策略权重计算、规则反思 |

## 🛠️ 快速开始 (Quick Start)

### 1. 部署核心
```bash
git clone https://github.com/xaohit/trading-core.git
cd trading-core
pip install -r requirements.txt

# 配置环境
cp .env.example .env
# 编辑 .env 填入你的代理设置等（API Key 可选）
```

### 2. 启动系统
```bash
# 启动后台监控服务（模拟盘）
python server.py restart

# 检查状态
python server.py status
# 访问 http://localhost:8080 查看 Web 仪表盘
```

### 3. 接入 Hermes (Agent)
在 Hermes 的上下文中导入工具包，开始工作：
```python
from agent_tools import *

# 获取 BTC 深度分析及相关历史经验
data = get_market_analysis("BTCUSDT")

# 记录你的判断（系统会自动存档并设定 24h 后回访）
record_agent_decision(
    symbol="BTCUSDT",
    action="open_long",
    reasoning="RSI 超卖反弹，宏观环境向好",
    target_price=75000.0
)
```

## 🗺️ Roadmap 进度

| Phase | 名称 | 状态 |
| :--- | :--- | :--- |
| 1-3 | 市场快照、信号、扫描集成 | ✅ 已完成 |
| 4A | 决策记忆循环 (Decision Memory) | ✅ 已完成 |
| 4B | ATR 动态仓位与 TP 金字塔 | ✅ 已完成 |
| 5 | 社交热度候选池 (Social Heat) | ✅ 已完成 |
| 6 | 反思引擎 (Reflection Engine) | ✅ 已完成 |
| 7A | 风险硬化 (Risk Hardening) | ✅ 已完成 |
| 7E | 回测引擎 (Backtest Engine) | ✅ 已完成 |
| **8A** | **深度决策快照 & Agent 接口** | **✅ 刚刚完成** |
| 8B | 历史回测 → 经验库注入 | 🔧 进行中 |
| 8C | 24h 自动回访与 LLM 反思 | ⏳ 待启动 |
| 8D | 场景感知经验调取 | ⏳ 待启动 |
| 8E | 规则反馈与自动进化 | ⏳ 待启动 |

## 📜 免责声明
本项目仅供技术研究和学习使用。系统包含风险控制系统，但不保证盈利。任何基于此项目的实盘操作风险由操作者自行承担。
