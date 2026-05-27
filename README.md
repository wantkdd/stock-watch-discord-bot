# Korean Stock Watch Discord Bot

Public GitHub Actions version of a no-order Korean stock watch bot.

## What it does

- Sends Discord alerts for Korean-market stock/ETF watch conditions.
- Runs without your Mac being on.
- Uses GitHub Actions public-repository runners.
- Optional Gemini API free-tier key for an 08:40 KST morning LLM risk review.
- Never places orders, never logs into a broker, never reads brokerage credentials.

## Schedule, Korea time

- 08:40 — Gemini LLM morning review: news/headlines/market risk summary and conservative overlay.
- 09:00 — market-open watch.
- 09:05 — market-open recheck.
- 10:05, 11:05, 12:05, 13:05, 14:05, 15:05 — hourly during KR market.
- 15:25 — pre-close check.

GitHub Actions cron is UTC, so the workflow file stores converted UTC schedules.

## Required secret

Repository Settings → Secrets and variables → Actions → New repository secret:

- `DISCORD_WEBHOOK_URL`: your Discord incoming webhook URL.

## Optional secret

- `GEMINI_API_KEY`: Google AI Studio / Gemini API key.

If `GEMINI_API_KEY` is missing or Gemini fails, the bot still sends price-condition alerts. The LLM morning review falls back to a conservative headline-only message.

## Why Gemini is OK here

Gemini does **not** decide trades alone. It only creates a morning risk overlay:

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

Actions tab → Korean Stock Watch Discord Bot → Run workflow → choose `morning` or `watch`.

## Safety

This is not financial advice and not an order system. Check all prices, order sizes, fees, slippage, and account risk manually in Mirae Asset M-STOCK before making any trade.
