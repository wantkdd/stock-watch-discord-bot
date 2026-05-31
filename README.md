# Korean Stock Watch Discord Bot

Public GitHub Actions version of a no-order Korean stock watch bot.

## What it does

- Sends Discord alerts for Korean-market stock/ETF watch conditions.
- Runs without your Mac being on.
- Uses GitHub Actions public-repository runners.
- Optional Gemini API free-tier key for an LLM overall review inside each scheduled alert.
- Never places orders, never logs into a broker, never reads brokerage credentials.

## Schedule, Korea time

- 09:00–15:00 — every hour during KR market; each alert includes the price checklist and Gemini overall review when configured.

GitHub scheduled workflows can be delayed by runner availability. The workflow
therefore may occasionally arrive a few minutes late. Each hourly watch alert
includes the concrete “what/price/size” checklist, current price, day-change
trend, KOSPI/KOSDAQ context, SOXX/SMH risk-proxy trend, current Google News RSS
headlines, and, when `GEMINI_API_KEY` is configured, a conservative LLM overall
review before sending.

GitHub Actions cron is UTC, so the workflow file stores converted UTC schedules.

## Required secret

Repository Settings → Secrets and variables → Actions → New repository secret:

- `DISCORD_WEBHOOK_URL`: your Discord incoming webhook URL.

## Optional secret

- `GEMINI_API_KEY`: Google AI Studio / Gemini API key.

If `GEMINI_API_KEY` is missing or Gemini fails, the bot still sends price-condition alerts. The LLM section falls back to fixed-rule/news context only.

## Why Gemini is OK here

Gemini does **not** decide trades alone. Inside each hourly alert, it only creates a conservative risk/summary overlay:

- can add caution notes;
- can pause new buys if news/risk is bad;
- cannot automatically loosen buy prices;
- cannot increase position size;
- cannot place orders.

The actual intraday watch uses fixed risk rules from `config/stock-watch-config.json`.

## Main targets

- `091160` KODEX 반도체
- `381180` TIGER 미국필라델피아반도체나스닥

`SOXX`/`SMH` are used internally as risk proxies for `381180`.

## Manual test

Actions tab → Korean Stock Watch Discord Bot → Run workflow. It runs `watch` mode only.

## Safety

This is not financial advice and not an order system. Check all prices, order sizes, fees, slippage, and account risk manually in Mirae Asset M-STOCK before making any trade.
