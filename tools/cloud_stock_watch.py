#!/usr/bin/env python3
"""Cloud stock watch for GitHub Actions.

No orders. No broker login. Sends Discord-only manual review signals.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import email.utils
import html
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/stock-watch-config.json"
DATA = ROOT / "data"
KST = ZoneInfo("Asia/Seoul")
STOOQ_CODES = {"SMH":"smh.us","SOXX":"soxx.us","KOSPI":"^kospi","KOSDAQ":"^kosdaq"}
NAVER_INDEX_CODES = {"KOSPI":"KOSPI","KOSDAQ":"KOSDAQ"}
ALLOWED_SIGNALS = {"BUY_CANDIDATE","STOP_REVIEW","TRIM_REVIEW","WAIT","DATA_MISSING","REPLACEMENT_ONLY","WATCH_ONLY","PROXY_ONLY","DATA_CONTEXT"}
SEVERITY = {"STOP_REVIEW":5,"DATA_MISSING":4,"TRIM_REVIEW":3,"BUY_CANDIDATE":3,"WAIT":2,"WATCH_ONLY":1,"REPLACEMENT_ONLY":1,"PROXY_ONLY":0,"DATA_CONTEXT":0}

@dataclass
class Quote:
    symbol: str
    price: Optional[float]
    open: Optional[float] = None
    market_date: Optional[str] = None
    captured_at: str = ""
    source: str = ""
    currency: str = ""
    error: Optional[str] = None


def now_kst() -> dt.datetime:
    return dt.datetime.now(KST).replace(microsecond=0)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_number(v: Any) -> Optional[float]:
    if v is None: return None
    try:
        f = float(str(v).replace(',', '').strip())
        return f if math.isfinite(f) else None
    except Exception:
        return None


def parse_yyyymmdd_dot(text: Optional[str]) -> Optional[str]:
    if not text: return None
    m = re.search(r"(\d{4})\.(\d{2})\.(\d{2})", text)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def naver_equity(symbol: str, currency: str) -> Quote:
    url = f"https://finance.naver.com/item/main.naver?code={symbol}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("euc-kr", errors="replace")
        m = re.search(r'<p class="no_today">.*?<span class="blind">([0-9,]+)</span>', text, re.S)
        price = parse_number(html.unescape(m.group(1)) if m else None)
        if price is None:
            return Quote(symbol, None, captured_at=now_iso(), source=url, currency=currency, error="missing naver price")
        dm = re.search(r'<em class="date">\s*(\d{4}\.\d{2}\.\d{2})', text)
        md = parse_yyyymmdd_dot(dm.group(1) if dm else None)
        err = None if md else "source market_date unavailable"
        return Quote(symbol, price, market_date=md, captured_at=now_iso(), source=url, currency=currency, error=err)
    except Exception as e:
        return Quote(symbol, None, captured_at=now_iso(), source=url, currency=currency, error=str(e))


def naver_index(symbol: str, currency: str) -> Quote:
    code = NAVER_INDEX_CODES.get(symbol, symbol)
    url = f"https://finance.naver.com/sise/sise_index.naver?code={code}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("euc-kr", errors="replace")
        m = re.search(r'<em id="now_value">\s*([0-9,.]+)\s*</em>', text)
        price = parse_number(m.group(1) if m else None)
        dm = re.search(r'<span id="time">\s*(\d{4}\.\d{2}\.\d{2})', text)
        md = parse_yyyymmdd_dot(dm.group(1) if dm else None)
        return Quote(symbol, price, market_date=md, captured_at=now_iso(), source=url, currency=currency, error=None if price and md else "missing index data")
    except Exception as e:
        return Quote(symbol, None, captured_at=now_iso(), source=url, currency=currency, error=str(e))


def stooq(symbol: str, currency: str) -> Quote:
    code = {"SMH":"smh.us","SOXX":"soxx.us"}.get(symbol, symbol.lower())
    url = f"https://stooq.com/q/l/?s={code}&f=sd2t2ohlcv&h&e=csv"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            rows = list(csv.DictReader(resp.read().decode('utf-8', errors='replace').splitlines()))
        row = rows[0] if rows else {}
        price = parse_number(row.get('Close'))
        openp = parse_number(row.get('Open'))
        if price is None:
            return Quote(symbol, None, captured_at=now_iso(), source=url, currency=currency, error="missing stooq close")
        return Quote(symbol, price, open=openp, market_date=row.get('Date') or None, captured_at=now_iso(), source=url, currency=currency)
    except Exception as e:
        return Quote(symbol, None, captured_at=now_iso(), source=url, currency=currency, error=str(e))


def load_config() -> Dict[str, Any]:
    return json.loads(CONFIG.read_text())


def fetch_quotes(config: Dict[str, Any]) -> Dict[str, Quote]:
    out: Dict[str, Quote] = {}
    for s in config['symbols']:
        if not s.get('enabled', True):
            continue
        sym, src, cur = s['symbol'], s.get('source'), s.get('currency','')
        if src == 'naver': out[sym] = naver_equity(sym, cur)
        elif src == 'naver_index': out[sym] = naver_index(sym, cur)
        elif src == 'stooq': out[sym] = stooq(sym, cur)
    return out


def age_hours(q: Quote) -> Optional[float]:
    try:
        captured = dt.datetime.fromisoformat(q.captured_at.replace('Z','+00:00'))
        if captured.tzinfo is None: captured = captured.replace(tzinfo=dt.timezone.utc)
        return (dt.datetime.now(dt.timezone.utc)-captured.astimezone(dt.timezone.utc)).total_seconds()/3600
    except Exception:
        return None


def market_date_age_hours(q: Quote) -> Optional[float]:
    if not q.market_date: return None
    try: d = dt.date.fromisoformat(q.market_date)
    except Exception: return None
    return (dt.datetime.now(dt.timezone.utc)-dt.datetime.combine(d, dt.time(23,59), tzinfo=dt.timezone.utc)).total_seconds()/3600


def data_errors(sym: Dict[str,Any], q: Optional[Quote], config: Dict[str,Any]) -> List[str]:
    if q is None: return ["quote missing"]
    errs=[]
    if q.error: errs.append(q.error)
    if q.price is None or q.price <= 0: errs.append("invalid price")
    max_age=float(config.get('data_quality',{}).get('max_age_hours',36))
    ah=age_hours(q); mh=market_date_age_hours(q)
    if ah is None or ah > max_age: errs.append("stale/invalid capture")
    if q.market_date and mh is not None and mh > max_age: errs.append("stale market_date")
    return errs


def in_zone(price: float, zones: List[List[float]]) -> bool:
    return any(len(z)==2 and float(z[0]) <= price <= float(z[1]) for z in zones or [])


def symcfg(config: Dict[str,Any], symbol: str) -> Optional[Dict[str,Any]]:
    return next((s for s in config['symbols'] if s['symbol']==symbol), None)


def proxy_gate(sym: Dict[str,Any], quotes: Dict[str,Quote], config: Dict[str,Any]) -> Tuple[bool, List[str]]:
    notes=[]; limit=sym.get('proxy_max_fall_from_open_pct')
    for ps in sym.get('requires_proxy_symbols') or []:
        q=quotes.get(ps); sc=symcfg(config, ps) or {'symbol':ps}
        errs=data_errors(sc, q, config)
        if errs:
            notes.append(f"proxy {ps} data invalid: {', '.join(errs)}"); return False, notes
        assert q and q.price is not None
        if ps in {'SOXX','SMH'}:
            if not q.open: notes.append(f"proxy {ps} open missing"); return False, notes
            fall=(q.price-q.open)/q.open*100
            notes.append(f"{ps} from open {fall:.2f}%")
            if limit is not None and fall < float(limit):
                notes.append(f"{ps} fell below gate {float(limit):.2f}%"); return False, notes
    return True, notes


def evaluate(config: Dict[str,Any], quotes: Dict[str,Quote]) -> List[Dict[str,Any]]:
    rows=[]
    for s in config['symbols']:
        if not s.get('enabled', True): continue
        q=quotes.get(s['symbol'])
        r={"symbol":s['symbol'],"name":s['name'],"output_type":s.get('output_type'),"signal":"WAIT","price":q.price if q else None,"currency":s.get('currency',''),"notes":[],"source":q.source if q else s.get('source'),"max_position_krw":s.get('max_position_krw',0),"exact_triggers":s.get('exact_triggers',{}),"internal_only":bool(s.get('internal_only',False))}
        errs=data_errors(s,q,config)
        if errs: r['signal']='DATA_MISSING'; r['notes'].extend(errs); rows.append(r); continue
        assert q and q.price is not None
        ot=s.get('output_type')
        if ot in {'PROXY_ONLY','DATA_CONTEXT','WATCH_ONLY','REPLACEMENT_ONLY'}:
            r['signal']=ot; r['notes'].extend(s.get('manual_qualifiers',[])); rows.append(r); continue
        stop=s.get('stop',{})
        if stop.get('type')=='price' and q.price <= float(stop['price']):
            r['signal']='STOP_REVIEW'; r['notes'].append(f"stop threshold reached {q.price:g}"); rows.append(r); continue
        trims=[t for t in s.get('trim',[]) if q.price >= float(t.get('price', math.inf))]
        if trims:
            r['signal']='TRIM_REVIEW'; r['notes'].append('trim threshold reached'); rows.append(r); continue
        ok,pnotes=proxy_gate(s,quotes,config); r['notes'].extend(pnotes)
        if s.get('requires_proxy_symbols') and not ok:
            r['signal']='WAIT'; r['notes'].append('proxy gate failed'); rows.append(r); continue
        if in_zone(q.price, s.get('candidate_zones',[])) or in_zone(q.price, s.get('add_zones',[])):
            r['signal']='BUY_CANDIDATE'; r['notes'].append('manual review only')
        else:
            r['signal']='WAIT'; r['notes'].append('outside configured zones')
        rows.append(r)
    return rows


def load_overlay() -> Dict[str,Any]:
    p=DATA/'morning-overlay.json'
    if not p.exists(): return {}
    try: data=json.loads(p.read_text())
    except Exception: return {}
    if data.get('date') != now_kst().date().isoformat(): return {}
    return data


def apply_overlay(results: List[Dict[str,Any]], overlay: Dict[str,Any]) -> List[Dict[str,Any]]:
    if not overlay: return results
    overrides=overlay.get('symbol_overrides',{}) if isinstance(overlay.get('symbol_overrides'),dict) else {}
    for r in results:
        if overlay.get('risk_mode') in {'risk_off','pause_all'} and r['signal']=='BUY_CANDIDATE':
            r['signal']='WAIT'; r['notes'].append(f"LLM morning {overlay.get('risk_mode')}: new buys paused")
        ov=overrides.get(r['symbol'],{}) if isinstance(overrides,dict) else {}
        if isinstance(ov,dict) and ov.get('disable_buy') and r['signal']=='BUY_CANDIDATE':
            r['signal']='WAIT'; r['notes'].append('LLM morning disabled buy')
        for n in ov.get('notes',[]) if isinstance(ov,dict) and isinstance(ov.get('notes'),list) else []:
            r['notes'].append('LLM: '+str(n))
    return results


def trigger_text(r: Dict[str,Any]) -> Tuple[str,str,str,str,str]:
    ex=r.get('exact_triggers') or {}; cur=r.get('currency','')
    pull='-'; br='-'; stop='-'; trim='-'; amount='-'
    if 'first_buy_at_or_below' in ex:
        pull=f"{float(ex['first_buy_at_or_below']):,.0f}{cur} 이하"
    if 'add_buy_at_or_below' in ex:
        pull += f" / 추가 {float(ex['add_buy_at_or_below']):,.0f}{cur} 이하"
    if 'breakout_buy_at_or_above' in ex:
        br=f"{float(ex['breakout_buy_at_or_above']):,.0f}{cur} 이상"
    if 'breakout_chase_stop_above' in ex:
        br += f" / {float(ex['breakout_chase_stop_above']):,.0f}{cur} 초과 추격금지"
    if 'stop_at_or_below' in ex:
        stop=f"{float(ex['stop_at_or_below']):,.0f}{cur} 이하"
    trims=[]
    if 'first_trim_at_or_above' in ex: trims.append(f"1차 {float(ex['first_trim_at_or_above']):,.0f}{cur} 이상")
    if 'second_trim_at_or_above' in ex: trims.append(f"2차 {float(ex['second_trim_at_or_above']):,.0f}{cur} 이상")
    trim=', '.join(trims) or '-'
    parts=[]
    for key,label,shkey in [('first_buy_at_or_below','눌림','first_buy_shares'),('add_buy_at_or_below','추가','add_buy_shares'),('breakout_buy_at_or_above','돌파','breakout_buy_shares')]:
        if key in ex:
            shares=int(ex.get(shkey,1)); parts.append(f"{label} {shares}주≈{float(ex[key])*shares:,.0f}원")
    amount=' / '.join(parts) or '-'
    return pull,br,amount,stop,trim


def discord_summary(results: List[Dict[str,Any]]) -> str:
    rows=[]
    for r in sorted(results, key=lambda x:(-SEVERITY.get(x['signal'],0), x['symbol'])):
        if r.get('internal_only') or r.get('output_type')!='TRADE_CANDIDATE': continue
        pull,br,amount,stop,trim=trigger_text(r)
        price='-' if r.get('price') is None else f"{float(r['price']):,.0f}{r.get('currency','')}"
        notes='; '.join(r.get('notes',[])[:2])
        rows.append(f"**{r['symbol']} {r['name']}**\n> 판단 `{r['signal']}` 현재가 `{price}`\n> 눌림매수 `{pull}`\n> 돌파매수 `{br}`\n> 살 금액 `{amount}`\n> 손절 `{stop}` / 익절 `{trim}`\n> 메모 {notes or '-'}")
    return '\n\n'.join(rows)[:3500] or '오늘은 감시 대상 신호가 없습니다.'


def send_discord(webhook: str, title: str, description: str, color: int=3066993) -> None:
    payload={"username":"Stock Watch Bot","content":"📈 **주식 감시 리포트** — 주문 없는 조건 확인표","embeds":[{"title":title,"description":description[:4000],"color":color}]}
    req=urllib.request.Request(webhook, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), headers={"Content-Type":"application/json","User-Agent":"cloud-stock-watch/1.0"}, method='POST')
    with urllib.request.urlopen(req, timeout=20) as resp:
        if resp.status not in {200,204}: raise RuntimeError(f"Discord HTTP {resp.status}")


def news_rss(query: str, limit: int=5) -> List[Dict[str,str]]:
    url='https://news.google.com/rss/search?'+urllib.parse.urlencode({'q':query,'hl':'ko','gl':'KR','ceid':'KR:ko'})
    out=[]
    try:
        with urllib.request.urlopen(url, timeout=15) as resp: xml=resp.read()
        root=ET.fromstring(xml)
        for item in root.findall('.//item')[:limit]:
            out.append({'title':html.unescape(item.findtext('title') or ''),'url':item.findtext('link') or '','published':item.findtext('pubDate') or ''})
    except Exception as e:
        out.append({'title':f'news fetch failed: {e}','url':url,'published':''})
    return out


def gemini_review(api_key: str, market_json: str, headlines_json: str) -> Dict[str,Any]:
    prompt=f"""
한국어로만 답하라. 너는 보수적인 한국장 반도체 매매 리스크 리뷰어다.
아래 최신 가격/헤드라인을 보고 091160, 381180 신규매수 허용 여부를 판단하라.
자동주문/수익보장/강한 매수지시 금지. bullish해도 조건 완화 금지. 위험하면 disable_buy=true.
반드시 JSON만 반환하라. 스키마:
{{"risk_mode":"normal|caution|risk_off|pause_all","notes":["..."],"symbol_overrides":{{"091160":{{"disable_buy":false,"notes":[]}},"381180":{{"disable_buy":false,"notes":[]}}}},"summary":"짧은 결론"}}
가격:
{market_json}
헤드라인:
{headlines_json}
"""
    url=f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={urllib.parse.quote(api_key)}"
    body={"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"temperature":0.2,"responseMimeType":"application/json"}}
    req=urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers={"Content-Type":"application/json"}, method='POST')
    with urllib.request.urlopen(req, timeout=45) as resp:
        data=json.loads(resp.read().decode('utf-8'))
    text=data['candidates'][0]['content']['parts'][0]['text']
    return json.loads(text)


def morning(webhook: str, api_key: str) -> int:
    config=load_config(); quotes=fetch_quotes(config); results=evaluate(config,quotes)
    headlines=[]
    for q in ['삼성전자 SK하이닉스 반도체 HBM', 'KODEX 반도체 091160', 'TIGER 미국필라델피아반도체나스닥 381180', 'NVIDIA SOXX SMH semiconductor']:
        headlines.extend(news_rss(q, 4))
    market_json=json.dumps([{k:v for k,v in r.items() if k in {'symbol','name','signal','price','currency','notes'}} for r in results], ensure_ascii=False)
    headlines_json=json.dumps(headlines, ensure_ascii=False)
    today=now_kst().date().isoformat()
    overlay={"date":today,"risk_mode":"caution","notes":["Gemini key missing; headline-only fallback"],"symbol_overrides":{"091160":{"disable_buy":False,"notes":[]},"381180":{"disable_buy":False,"notes":[]}},"sources":headlines[:8]}
    summary="Gemini API key missing: 가격감시는 계속, LLM 판단은 비활성."
    if api_key:
        try:
            g=gemini_review(api_key, market_json, headlines_json)
            overlay.update({"risk_mode":g.get('risk_mode','caution'),"notes":g.get('notes',[])[:5],"symbol_overrides":g.get('symbol_overrides', overlay['symbol_overrides']),"sources":headlines[:10]})
            summary=str(g.get('summary',''))
        except Exception as e:
            overlay['notes'].insert(0, f"Gemini failed: {e}")
            summary=f"Gemini 실패: {e}. 가격감시는 계속."
    DATA.mkdir(exist_ok=True)
    (DATA/'morning-overlay.json').write_text(json.dumps(overlay, ensure_ascii=False, indent=2)+"\n")
    md=DATA/f"morning-review-{today.replace('-','')}.md"
    md.write_text(f"# Morning LLM Review — {today}\n\n## 결론\n- risk_mode: {overlay['risk_mode']}\n- {summary}\n\n## 메모\n" + '\n'.join(f"- {n}" for n in overlay.get('notes',[])) + "\n\n## 주요 헤드라인\n" + '\n'.join(f"- [{h['title']}]({h['url']})" for h in headlines[:12]) + "\n")
    send_discord(webhook, f"Morning LLM Review — {today}", md.read_text(), 3447003)
    return 0


def watch(webhook: str) -> int:
    config=load_config(); quotes=fetch_quotes(config); results=apply_overlay(evaluate(config,quotes), load_overlay())
    DATA.mkdir(exist_ok=True)
    report=DATA/f"daily-signal-{now_kst().strftime('%Y%m%d-%H%M')}.json"
    report.write_text(json.dumps(results, ensure_ascii=False, indent=2)+"\n")
    send_discord(webhook, f"Stock Watch — {now_kst().strftime('%Y-%m-%d %H:%M KST')}", discord_summary(results))
    return 0


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--mode', choices=['morning','watch'], default=os.environ.get('RUN_MODE','watch'))
    ap.add_argument('--discord-webhook', default=os.environ.get('DISCORD_WEBHOOK_URL',''))
    ap.add_argument('--gemini-api-key', default=os.environ.get('GEMINI_API_KEY',''))
    args=ap.parse_args()
    if not args.discord_webhook:
        print('DISCORD_WEBHOOK_URL missing', file=sys.stderr); return 1
    return morning(args.discord_webhook, args.gemini_api_key) if args.mode=='morning' else watch(args.discord_webhook)

if __name__ == '__main__':
    raise SystemExit(main())
