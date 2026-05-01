# Trading Core

一个面向加密货币合约市场的 **纸交易优先** AI 交易系统。它的目标不是做一套静态量化脚本，而是搭建一个可以持续留痕、复盘、积累经验，并逐步接入 Hermes/MAKIMA 进行判断的交易 Agent 底座。

> 当前状态：可以用于纸交易、模拟观察、回测、决策记录和经验库积累。  
> 不建议直接实盘。真实 Hermes API 与实盘安全闭环仍未完成。

## 项目定位

Trading Core 的核心思想是：

```text
规则发现机会 -> 上下文过滤 -> Agent 判断 -> 硬风控校验 -> 纸交易执行 -> 24h 复盘 -> 经验沉淀
```

系统里有两类智能：

- **本地规则智能**：95% 的扫描、过滤、风控、回测和复盘都在本地执行，不依赖大模型 token。
- **Agent 判断智能**：Hermes/MAKIMA 后续负责结合市场上下文、历史经验和复盘教训，做最终 trade/wait 判断。

目前 Hermes 还没有作为真实 API 接入。仓库里先提供了确定性的 `AgentDecisionGate`，作为过渡版 Agent 判断层，保证系统在无 LLM 的情况下也能跑通。

## 当前能力

### 0. Agent 工作台 Web UI

首页已经重设计为 Agent Workbench：

- 账户权益、余额、浮动盈亏
- 当前持仓与 TP/SL 状态
- 最近信号与拒绝/开仓结果
- 决策流水线状态
- Agent 决策记忆
- 经验库摘要
- 社交热度候选
- 手动扫描、手动平仓、全部平仓

启动后访问：

```text
http://localhost:8080
```

### 1. 市场扫描

系统会从 Binance USD-M 市场获取数据，并优先使用社交热度候选池。数据包括：

- 价格、成交量、涨跌幅
- 资金费率
- OI 与 OI 变化
- 全网多空比、头部账户多空比
- taker 主动买卖结构
- 盘口深度不平衡
- ATR 波动率
- Binance Square 社交热度

### 2. 策略候选信号

当前内置 4 个种子策略，用来发现候选机会：

| 信号类型 | 方向 | 逻辑 |
|---|---:|---|
| `neg_funding_long` | 做多 | 资金费率极端为负，押空头拥挤后的修复/逼空 |
| `pos_funding_short` | 做空 | 资金费率极端为正，押多头拥挤后的回落 |
| `crash_bounce_long` | 做多 | 24h 暴跌后出现反弹，押短线修复 |
| `pump_short` | 做空 | 24h 暴涨后从高点回落，押冲高回落 |

这些策略只负责发现候选机会，不再直接决定开仓。

### 3. 决策流水线

当前主链路已经拆成清晰边界：

```text
signal discovery
-> DecisionPipeline
-> ranking
-> AgentDecisionGate
-> TA/RR guard
-> paper execution
-> decision memory
```

各层职责：

| 层 | 文件 | 职责 |
|---|---|---|
| Signal Discovery | `strategies/detectors.py` | 发现候选机会 |
| Context Scoring | `signals.py`, `market_snapshot.py` | 计算市场质量分、标签、verdict |
| Pre-Agent Pipeline | `decision_pipeline.py` | 环境过滤、质量过滤、账户风控 |
| Decision Provider | `decision_provider.py` | 路由本地判断或未来 Hermes 判断 |
| Agent Gate | `agent_decision.py` | 本地 fallback，结合分数、强度、历史经验做 trade/wait 判断 |
| Trade Hypothesis | `trade_hypothesis.py` | 结构化记录假设、预期路径、失效条件 |
| Semantic Radar | `semantic_radar.py` | 接收新闻、宏观、KOL、Polymarket 等语义事件 |
| TA/RR Guard | `ta_checker.py` | 检查技术结构与盈亏比，要求 R/R >= 1.5 |
| Execution | `executor.py` | 纸交易开仓、TP、移动止损 |
| Memory Loop | `decision_memory.py` | 决策留痕、24h 回看、经验库 |

### 4. 纸交易执行与持仓管理

执行层目前是纸交易优先：

- ATR 动态止损
- 每笔固定风险比例
- TP1 触发后部分止盈并推保护
- TP2 触发后继续减仓
- 剩余仓位使用 trailing stop
- 硬止损保护
- 平仓后记录结果，用于后续复盘和策略权重调整

### 5. 决策记忆与经验库

每个重要动作都会记录：

- `opened`
- `env_reject`
- `score_reject`
- `entry_veto`
- `quality_reject`
- `risk_reject`
- `agent_reject`

记录内容包括：

- 当时信号与市场快照
- 市场质量分和 tags
- 宏观上下文
- 市场状态
- Agent reasoning
- 引用过的历史经验
- 后续 24h 价格表现

系统会在决策到期后回看结果，标记方向对错、是否打到目标、是否失效，并把复盘沉淀为经验案例。

## 项目结构

```text
trading-core/
├── scanner.py                 # 主扫描编排器
├── decision_pipeline.py       # 前置决策流水线
├── agent_decision.py          # 本地 Agent 判断层
├── agent_tools.py             # 给 Hermes/OpenClaw 调用的工具接口
├── main_agent.py              # MAKIMA/Hermes 调度入口雏形
├── decision_memory.py         # 决策记忆、24h 回看、经验库
├── market_snapshot.py         # Binance 市场快照
├── signals.py                 # 市场质量评分
├── ta_checker.py              # 技术结构与盈亏比检查
├── executor.py                # 纸交易执行与 TP 管理
├── risk.py                    # 账户与入场风控
├── backtest.py                # 回测引擎
├── reflection.py              # 失败归档、策略权重、规则建议
├── social_heat.py             # Binance Square 热度候选池
├── db/                        # SQLite schema 与交易记录
├── strategies/                # 种子策略检测器
├── tests/                     # smoke tests
└── docs/                      # 项目计划、交接文档、状态记录
```

## 快速开始

### 1. 安装

```bash
git clone https://github.com/xaohit/trading-core.git
cd trading-core
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

Linux/macOS:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置

复制环境变量模板：

```bash
cp .env.example .env
```

系统默认读取：

```text
~/.hermes/.env
```

Binance API key 当前不是必须项。纸交易和公开行情扫描可以先不配置真实交易权限。

### 3. 运行服务

```bash
python server.py restart
python server.py status
```

Web UI:

```text
http://localhost:8080
```

### 4. 手动跑测试

```bash
python -m py_compile config.py scanner.py decision_pipeline.py agent_decision.py decision_memory.py executor.py backtest.py

python tests/smoke_decision_pipeline.py
python tests/smoke_agent_gate.py
python tests/smoke_decision_memory.py
python tests/smoke_phase4b.py
python tests/smoke_phase6.py
python tests/smoke_phase7a.py
python tests/smoke_phase7e.py
```

## 当前交易逻辑

简化版：

```text
1. 获取候选币
2. 运行 4 个种子策略，产生候选信号
3. 拉取市场快照并打分
4. DecisionPipeline 做前置过滤
5. 候选信号排序
6. AgentDecisionGate 结合经验与上下文决定 trade/wait
7. TA/RR guard 检查盈亏比和技术结构
8. 纸交易开仓
9. TP/SL/trailing stop 管理
10. 决策留痕，24h 后复盘
```

这套逻辑的设计重点是避免“看到信号就开仓”。策略只是提出假设，Agent 与风控负责验证假设。

## Hermes / MAKIMA 接入方向

未来 Hermes 应该作为 `DecisionProvider` 接入，而不是替代整套系统：

1. `DecisionPipeline` 先完成本地筛选。
2. `EventTriggeredDecisionProvider` 判断是否需要 Hermes。
3. 只有高价值、冲突、语义事件触发的候选才交给 Hermes。
4. Hermes 输出结构化 JSON：
   - `action`
   - `conviction`
   - `hypothesis`
   - `expected_path`
   - `stop_loss`
   - `target_price`
   - `invalidation_condition`
   - `reasoning`
5. 系统用本地 hard guard 校验。
6. 通过则纸交易执行，拒绝也记录。
7. 24h 后回看，失败样本交给 Hermes 复盘。
8. 复盘结果写入经验库，下次类似场景自动注入。

Provider 模式：

```text
DECISION_PROVIDER=event   # 默认：事件触发路由，本地优先，必要时触发 Hermes 占位
DECISION_PROVIDER=local   # 只使用本地 AgentDecisionGate
DECISION_PROVIDER=hermes  # 只使用 Hermes provider；未接真实客户端前会安全 wait
```

Hermes 触发条件包括：

- 高 severity 语义事件
- S 级高分候选
- 历史经验存在明显冲突
- A 级中高分且已有足够历史经验

## 当前完成度

| 模块 | 状态 |
|---|---|
| 市场数据快照 | 已完成 |
| 种子策略信号 | 已完成 |
| 市场质量评分 | 已完成 |
| 前置决策流水线 | 已完成 |
| 本地 Agent Gate | 已完成 |
| Decision Provider 架构 | 已完成 |
| 结构化交易假设 | 已完成 |
| 语义雷达骨架 | 已完成 |
| 每日复盘报告 | 已完成 |
| TA/RR 校验 | 已完成 |
| 纸交易执行 | 已完成 |
| TP/SL 管理 | 已完成 |
| 决策记忆 | 已完成 |
| 24h 回看 | 已完成 |
| 经验库 | 已完成 |
| 回测引擎 | 已完成 |
| Hermes 真实 API 客户端 | 未完成 |
| 实盘交易安全闭环 | 未完成 |

## 重要安全说明

本项目当前只建议用于：

- 技术研究
- 纸交易
- 模拟盘观察
- 回测
- Agent 交易记忆系统实验

不建议直接实盘。即使未来接入交易 API，也必须先完成：

- API 权限隔离
- 最大亏损熔断
- 订单失败恢复
- 网络异常处理
- 交易所异常返回处理
- 实盘 dry-run 对账
- 小资金灰度验证

本项目不构成投资建议，也不保证盈利。任何实盘风险由使用者自行承担。
