# TRADINGVIEW IMPLEMENTATION TARGET

## v1
- Pine Script v6 strategy/indicator logic in a single source if practical.
- 5-minute NQ/MNQ chart.
- America/New_York session logic.
- Plot ORB and state labels.
- Generate deterministic alerts/events.

## Alert message
Construct valid JSON string in Pine matching `spec/alert_schema.json`.

## Paper automation
TradingView alert -> webhook receiver -> paper ledger.

## Connection/MCP
If Claude Code has a TradingView connector/MCP available, use it for:
- opening/inspecting the script
- deployment assistance
- chart/alert setup where supported
Do not assume a connector can place brokerage orders.
The actual execution interface remains the external paper/broker adapter.

## First success criterion
The Pine ORB signal list matches the supplied verified benchmark sufficiently that every residual mismatch is explained by data-feed/continuous-contract differences rather than logic drift.
