# Paper desk: does a multi-agent LLM desk beat simple baselines?

**Paper only.** No orders, no exchange keys, no real money. It reads public market data (Bybit, alternative.me) and books a simulated 10,000 USDT account per arm, with fees (0.055%) and slippage (0.02%) per side.

Five arms trade the same symbol (BTCUSDT) on the same prices, every 4 hours:
`buyhold`, `random`, `ema` (EMA20/50), `single_llm` (one model call), `desk` (Atlas, Vega, Orion, Luna analysts -> Nova decides -> Titan sizes/vetoes in plain code).

Every number in the prompts is computed in code. Every decision, with reason and the facts it saw, goes to `data/decisions.jsonl`; equity per cycle to `data/equity.jsonl`.
Orion uses funding rate and open interest instead of news (no free news feed); say so if you compare with other "desks".

## Try it
```
python3 automation/paper_desk/selfcheck.py     # offline checks of the accounting
python3 -m automation.paper_desk.desk check          # needs network: fetches facts, writes nothing
DESK_STUB=1 python3 -m automation.paper_desk.desk run --force   # one cycle without model calls
python3 -m automation.paper_desk.score               # table + honest verdict
```
Without `ANTHROPIC_API_KEY` (or with `DESK_STUB=1`) the model arms use a labelled stub (EMA/RSI rule), so a stub run says nothing about LLMs.

## Run it every 4 hours (macOS)
Put `export ANTHROPIC_API_KEY=...` in `~/.paper_desk_env`, then copy `com.nognize.paperdesk.plist` to `~/Library/LaunchAgents/` and `launchctl load` it. Default model `claude-haiku-5-5` (override with `DESK_MODEL`); roughly 5 model calls per arm-cycle pair, 6 cycles a day.

## How to read the result
`score.py` refuses to name a winner before 30 days and 100 cycles. Even then one asset over one month cannot prove an edge. Keep it on paper for at least three months and check out of sample before any real money.
