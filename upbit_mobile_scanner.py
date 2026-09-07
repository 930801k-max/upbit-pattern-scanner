#!/usr/bin/env python3
"""
Mobile-friendly Upbit pattern scanner web app.

Install:
    pip install flask requests pandas

Run:
    python upbit_mobile_scanner.py

Then on the same phone:
    http://127.0.0.1:5000

For access from another device on the same Wi-Fi:
    http://YOUR-PC-IP:5000
"""

from flask import Flask, jsonify, render_template_string
import requests
import pandas as pd
import time

app = Flask(__name__)
BASE = "https://api.upbit.com/v1"
session = requests.Session()
session.headers.update({"Accept": "application/json"})

BASE_BARS = 20
V1_MULT = 5.0
MIN_R = 1.0
MAX_R = 7.0
MIN_V2_RATIO = 0.70
MAX_LOOKAHEAD = 16
MAX_DRAWDOWN = 0.05


def get_markets():
    r = session.get(f"{BASE}/market/all",
                    params={"isDetails": "false"}, timeout=10)
    r.raise_for_status()
    return [x["market"] for x in r.json()
            if x["market"].startswith("KRW-")]


def get_candles(market):
    r = session.get(f"{BASE}/candles/minutes/15",
                    params={"market": market, "count": 200}, timeout=10)
    r.raise_for_status()
    return pd.DataFrame(r.json())


def scan_market(market):
    df = get_candles(market)
    if len(df) < BASE_BARS + 5:
        return None

    df = df.sort_values("candle_date_time_kst").reset_index(drop=True)
    v = df["candle_acc_trade_volume"].astype(float)
    close = df["trade_price"].astype(float)
    high = df["high_price"].astype(float)

    best = None

    for i in range(BASE_BARS, len(df) - 2):
        baseline = v.iloc[i-BASE_BARS:i].median()
        if baseline <= 0:
            continue

        v1 = v.iloc[i]
        v1_mult = v1 / baseline
        if v1_mult < V1_MULT:
            continue

        p1 = close.iloc[i]

        for j in range(i + 2, min(i + MAX_LOOKAHEAD + 1, len(df))):
            v2 = v.iloc[j]
            v2_ratio = v2 / v1
            vcum = v.iloc[i+1:j].sum()
            R = vcum / v1 if v1 else 0

            min_price = close.iloc[i+1:j+1].min()
            drawdown = min_price / p1 - 1
            if drawdown < -MAX_DRAWDOWN:
                continue
            if not (MIN_R <= R <= MAX_R):
                continue
            if v2_ratio < MIN_V2_RATIO:
                continue

            pre_v2_high = high.iloc[i:j].max()
            breakout = high.iloc[j] > pre_v2_high

            score = 0
            score += min(20, max(0, (v1_mult - 5) * 3 + 10))

            if 2 <= R <= 5:
                score += 25
            elif 1 <= R < 2:
                score += 15
            else:
                score += 18

            score += min(20, max(0, v2_ratio * 15))

            if drawdown >= -0.01:
                score += 20
            elif drawdown >= -0.02:
                score += 17
            elif drawdown >= -0.03:
                score += 13
            else:
                score += 5

            if breakout:
                score += 15

            candidate = {
                "market": market.replace("KRW-", ""),
                "v1_mult": round(v1_mult, 2),
                "R": round(R, 2),
                "v2_ratio": round(v2_ratio, 2),
                "drawdown": round(drawdown * 100, 2),
                "breakout": breakout,
                "price": close.iloc[j],
                "score": round(score, 1),
                "v1_time": df.loc[i, "candle_date_time_kst"],
                "v2_time": df.loc[j, "candle_date_time_kst"],
            }

            if best is None or candidate["score"] > best["score"]:
                best = candidate

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
button{width:100%;padding:15px;border:0;border-radius:12px;background:#111;color:white;font-size:17px;margin:15px 0}
#status{padding:8px;color:#555}
.card{background:white;border-radius:16px;padding:16px;margin:10px 0;
box-shadow:0 2px 10px #00000010}
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
<main style="padding:12px">
<button onclick="scan()">🔄 지금 전체 스캔</button>
<div id="status">스캔 버튼을 눌러주세요.</div>
<div id="results"></div>
</main>
<script>
async function scan(){
  const s=document.getElementById('status');
  s.textContent='⏳ 업비트 종목을 스캔하고 있습니다...';
  document.getElementById('results').innerHTML='';
  try{
    const r=await fetch('/scan');
    const data=await r.json();
    if(data.error){s.textContent='오류: '+data.error;return;}
    s.textContent=`✅ ${data.count}개 패턴 발견 · ${data.elapsed}초`;
    document.getElementById('results').innerHTML=data.results.map((x,i)=>`
      <div class="card">
        <span class="rank">${i+1}. ${x.market}</span>
        <span class="score">⭐ ${x.score}</span>
        <div class="row"><span class="label">V1</span><span class="value">${x.v1_mult}배</span></div>
        <div class="row"><span class="label">누적 R</span><span class="value">${x.R}배</span></div>
        <div class="row"><span class="label">V2 / V1</span><span class="value">${x.v2_ratio}</span></div>
        <div class="row"><span class="label">V1 이후 하락</span><span class="value">${x.drawdown}%</span></div>
        <div class="row"><span class="label">고점 돌파</span><span class="${x.breakout?'good':'bad'}">${x.breakout?'✅ YES':'❌ NO'}</span></div>
        <div class="row"><span class="label">V2 가격</span><span class="value">${Number(x.price).toLocaleString()}</span></div>
        <div class="small">${x.v1_time} → ${x.v2_time}</div>
      </div>`).join('');
  }catch(e){s.textContent='연결 오류: '+e}
}
</script>
</body>
</html>
"""

@app.route("/")
def home():
    return render_template_string(HTML)

@app.route("/scan")
def scan():
    start = time.time()
    try:
        markets = get_markets()
        results = []
        for idx, market in enumerate(markets):
            try:
                x = scan_market(market)
                if x:
                    results.append(x)
            except Exception:
                pass
            time.sleep(0.11)

        results.sort(key=lambda x: (x["score"], x["R"], x["v2_ratio"]),
                     reverse=True)
        return jsonify({
            "count": len(results),
            "elapsed": round(time.time() - start, 1),
            "results": results[:20]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
