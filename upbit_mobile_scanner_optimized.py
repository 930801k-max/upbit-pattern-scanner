#!/usr/bin/env python3

from flask import Flask, jsonify, render_template_string
import requests
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)

BASE = "https://api.upbit.com/v1"

# ---- Scanner settings ----
BASE_BARS = 20
V1_MULT = 5.0
MIN_R = 1.0
MAX_R = 7.0
MIN_V2_RATIO = 0.70
MAX_LOOKAHEAD = 16
MAX_DRAWDOWN = 0.05
TOP_N = 20

# Render Free 메모리를 고려해 동시 작업 수를 낮춤
WORKERS = 4


def get_session():
    s = requests.Session()
    s.headers.update({
        "Accept": "application/json",
        "User-Agent": "UpbitPatternScanner/2.0"
    })
    return s


def get_markets():
    s = get_session()
    r = s.get(
        f"{BASE}/market/all",
        params={"isDetails": "false"},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    return [x["market"] for x in data if x["market"].startswith("KRW-")]


def get_candles(market):
    s = get_session()
    r = s.get(
        f"{BASE}/candles/minutes/15",
        params={"market": market, "count": 200},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise RuntimeError("Upbit returned non-list candle data")

    # 필요한 값만 남겨 메모리 사용량을 줄임
    candles = []
    for x in data:
        try:
            candles.append({
                "time": x["candle_date_time_kst"],
                "volume": float(x["candle_acc_trade_volume"]),
                "close": float(x["trade_price"]),
                "high": float(x["high_price"]),
            })
        except (KeyError, TypeError, ValueError):
            continue

    return candles


def scan_market(market):
    candles = get_candles(market)

    if len(candles) < BASE_BARS + 5:
        return None

    candles.sort(key=lambda x: x["time"])

    v = [x["volume"] for x in candles]
    close = [x["close"] for x in candles]
    high = [x["high"] for x in candles]

    best = None

    # V1 -> volume contraction/holding -> V2
    for i in range(BASE_BARS, len(candles) - 2):
        baseline_values = sorted(v[i-BASE_BARS:i])
        mid = len(baseline_values) // 2
        if len(baseline_values) % 2:
            baseline = baseline_values[mid]
        else:
            baseline = (baseline_values[mid-1] + baseline_values[mid]) / 2

        if baseline <= 0:
            continue

        v1 = v[i]
        v1_mult = v1 / baseline

        if v1_mult < V1_MULT:
            continue

        p1 = close[i]

        for j in range(i + 2, min(i + MAX_LOOKAHEAD + 1, len(candles))):
            middle = v[i+1:j]

            if not middle:
                continue

            # V1 이후 거래량이 최소 한 번은 의미 있게 수축해야 함
            if max(middle) > v1 * 0.90:
                continue

            v2 = v[j]
            v2_ratio = v2 / v1 if v1 else 0.0
            vcum = sum(middle)
            R = vcum / v1 if v1 else 0.0

            min_price = min(close[i+1:j+1])
            drawdown = min_price / p1 - 1.0

            if drawdown < -MAX_DRAWDOWN:
                continue

            if not (MIN_R <= R <= MAX_R):
                continue

            if v2_ratio < MIN_V2_RATIO:
                continue

            pre_v2_high = max(high[i:j])
            breakout = high[j] > pre_v2_high

            # Score
            score = 0.0
            score += min(20.0, max(0.0, 10.0 + (v1_mult - 5.0) * 3.0))

            if 2.0 <= R <= 5.0:
                score += 25.0
            elif 1.0 <= R < 2.0:
                score += 15.0
            else:
                score += 18.0

            score += min(20.0, max(0.0, v2_ratio * 15.0))

            retention_score = max(
                0.0,
                15.0 * (1.0 - abs(drawdown) / MAX_DRAWDOWN)
            )
            score += min(15.0, retention_score)

            if breakout:
                score += 15.0

            candidate = {
                "market": market.replace("KRW-", ""),
                "v1_mult": round(v1_mult, 2),
                "R": round(R, 2),
                "v2_ratio": round(v2_ratio, 2),
                "drawdown": round(drawdown * 100, 2),
                "breakout": breakout,
                "price": close[j],
                "score": round(score, 1),
                "v1_time": candles[i]["time"],
                "v2_time": candles[j]["time"],
            }

            if best is None or candidate["score"] > best["score"]:
                best = candidate

            # 각 V1에서 첫 유효 V2만 사용
            break

    return best


HTML = r"""
<!doctype html>
<html lang="ko">
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Upbit Pattern Scanner</title>
<style>
body{font-family:system-ui,sans-serif;margin:0;background:#f5f6f8;color:#171717}
header{background:#111;color:white;padding:18px}
h1{margin:0;font-size:21px}
main{padding:12px}
button{width:100%;padding:16px;border:0;border-radius:12px;background:#111;color:white;font-size:17px;margin:15px 0}
button:disabled{opacity:.55}
#status{padding:8px;color:#555;white-space:pre-wrap}
.card{background:white;border-radius:16px;padding:16px;margin:10px 0;box-shadow:0 2px 10px #00000010}
.rank{font-size:19px;font-weight:700}
.score{float:right;font-size:18px}
.row{display:flex;justify-content:space-between;margin-top:9px}
.label{color:#777}.value{font-weight:600}
.good{color:#16803c}.bad{color:#c0392b}
.small{font-size:12px;color:#888;margin-top:10px}
</style>
</head>
<body>
<header><h1>🔥 Upbit 15분봉 패턴 스캐너</h1></header>
<main>
<button id="scanBtn" onclick="scan()">🔄 지금 전체 스캔</button>
<div id="status">스캔 버튼을 눌러주세요.</div>
<div id="results"></div>
</main>

<script>
async function scan(){
 const s=document.getElementById('status');
 const btn=document.getElementById('scanBtn');
 btn.disabled=true;
 s.textContent='⏳ 업비트 KRW 종목을 스캔하고 있습니다...';
 document.getElementById('results').innerHTML='';

 try{
   const r=await fetch('/scan',{cache:'no-store'});
   const text=await r.text();
   let data;

   try{
     data=JSON.parse(text);
   }catch(parseError){
     throw new Error('서버가 JSON 대신 오류 페이지를 반환했습니다.\\nHTTP '+r.status);
   }

   if(!r.ok || data.error){
     throw new Error(data.error || ('HTTP '+r.status));
   }

   s.textContent=`✅ ${data.count}개 패턴 발견 · ${data.elapsed}초`;

   document.getElementById('results').innerHTML=data.results.map((x,i)=>`
   <div class="card">
   <span class="rank">${i+1}. ${x.market}</span>
   <span class="score">⭐ ${x.score}</span>
   <div class="row"><span class="label">V1</span><span class="value">${x.v1_mult}배</span></div>
   <div class="row"><span class="label">누적 R</span><span class="value">${x.R}배</span></div>
   <div class="row"><span class="label">V2 / V1</span><span class="value">${x.v2_ratio}배</span></div>
   <div class="row"><span class="label">V1 이후 하락</span><span class="value">${x.drawdown}%</span></div>
   <div class="row"><span class="label">고점 돌파</span><span class="${x.breakout?'good':'bad'}">${x.breakout?'✅ YES':'❌ NO'}</span></div>
   <div class="row"><span class="label">V2 가격</span><span class="value">${Number(x.price).toLocaleString()}</span></div>
   <div class="small">${x.v1_time} → ${x.v2_time}</div>
   </div>`).join('');

 }catch(e){
   s.textContent='❌ '+e.message;
 }finally{
   btn.disabled=false;
 }
}
</script>
</body>
</html>
"""


@app.route("/")
def home():
    return render_template_string(HTML)


@app.route("/health")
def health():
    return jsonify({"ok": True})


@app.route("/scan")
def scan():
    start = time.time()

    try:
        markets = get_markets()
    except Exception as e:
        return jsonify({
            "error": "업비트 종목 목록을 가져오지 못했습니다: " + str(e)
        }), 502

    results = []
    errors = 0

    # 메모리 절약을 위해 worker 수를 4개로 제한
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = [executor.submit(scan_market, m) for m in markets]

        for future in as_completed(futures):
            try:
                x = future.result()
                if x:
                    results.append(x)
            except Exception:
                errors += 1

    results.sort(
        key=lambda x: (x["score"], x["R"], x["v2_ratio"]),
        reverse=True
    )

    return jsonify({
        "count": len(results),
        "elapsed": round(time.time() - start, 1),
        "errors": errors,
        "results": results[:TOP_N]
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
