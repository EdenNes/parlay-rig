# CLAUDE.md

## Prior art and reference libraries

Source list: https://github.com/wangzhe3224/awesome-systematic-trading (actively maintained, MIT, ~257 linked repos). The dead fork with more stars is paperswithbacktest/awesome-systematic-trading, last commit 2025-01-22. Do not use it.

### Read this before writing scoring.py

**pykalshi** (https://github.com/ArshKA/kalshi-client) is a Kalshi client with WebSocket streaming, automatic retries, rate limiting, and pandas integration.

This is aimed at the blocker in `docs/scoring-asymmetry.md`: leg-quote quality (roughly 30% empty books) and the timing gap make the "fills outside Frechet bounds" rate unmeasurable. The current `kalshi.py` is REST polling with throttle and backoff sleeps (lines 30, 33, 38), so the rig samples the book instead of following it. A WebSocket book feed changes the measurement from "snapshot every N minutes" to "every update," which is the difference between an unmeasurable quantity and a noisy one.

Read its WebSocket handling and reconnect logic before deciding whether to port the idea or adopt the library. Do not swap the collector mid-run without a plan for the schema break in `data/rig.db`.

**Parsec** (https://github.com/parsecular/parsec-mcp) is prediction market data, execution, and live streams across major exchanges, shipped as an MCP server. Relevant as a cross-venue comparison and as MCP practice.

**TurbineFi** (https://turbinefi.com) backtests and deploys prediction market strategies on Kalshi and Polymarket. Closed source, useful as a feature reference only.

### Standing instruction

Before adding any new data-collection or scoring capability here, check the Prediction Markets section of the list first. It is small (6 entries) and the Kalshi tooling in it is ahead of what this repo has.
