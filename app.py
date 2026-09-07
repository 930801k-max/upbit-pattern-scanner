from flask import Flask, jsonify
import requests
import statistics
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)

UPBIT = "https://api.upbit.com/v1"

BASE_BARS = 20
V1_MULT = 5.0
MIN_R = 1.0
MAX_R = 7.0
MIN_V2_RATIO = 0.70
MAX_LOOKAHEAD = 16
MAX_DRAWDOWN = 0.05

SCAN = {
    "running": False,
    "done": False,
    "data": [],
    "error": None,
    "started": 0,
    "finished": 0,
    "progress": 0,
    "total": 0,
}
LOCK = threading.Lock()


def get_markets():
    r = requests.get(
        f"{UPBIT}/market/all",
        params={"isDetails": "false"},
        timeout=10,
    )
    r.raise_for_status()
    return [x["market"] for x in r.json()
            if x["market"].startswith("KRW-")]


def analyze(market):
    try:
        r = requests.get(
            f"{UPBIT}/candles/minutes/15",
            params={"market": market, "count": 120},
            timeout=10,
        )
        r.raise_for_status()
        candles = list(reversed(r.json()))

        if len(candles) < BASE_BARS + 8:
            return None

        volume = [float(x["candle_acc_trade_volume"]) for x in candles]
        close = [float(x["trade_price"]) for x in candles]
        high = [float(x["high_price"]) for x in candles]
        times = [x["candle_date_time_kst"] for x in candles]

        best = None

        for i in range(BASE_BARS, len(candles) - 3):
            base = statistics.median(volume[i-BASE_BARS:i])
            if base <= 0:
                continue

            v1_mult = volume[i] / base
            if v1_mult < V1_MULT:
                continue

            v1 = volume[i]
            p1 = close[i]

            for j in range(i + 2,
                           min(i + MAX_LOOKAHEAD + 1, len(candles))):

                dd = min(close[i:j]) / p1 - 1
                if dd < -MAX_DRAWDOWN:
                    break

                mid = volume[i+1:j]
                if not mid:
                    continue

                if sum(mid) / len(mid) >= v1 * 0.75:
                    continue

                v2 = volume[j]
                v2_ratio = v2 / v1

                if v2_ratio < MIN_V2_RATIO:
                    continue

                if high[j] <= max(high[i:j]):
                    continue

                R = sum(mid) / v1
                if not (MIN_R <= R <= MAX_R):
                    continue

                local_start = max(0, j - BASE_BARS)
                local = statistics.median(volume[local_start:j])

                if local <= 0 or v2 < local * 3:
                    continue

                score = (
                    min(v1_mult, 12) * 2
                    + min(R, 5) * 1.5
                    + min(v2_ratio, 1.6) * 2
                )

                if dd >= -0.03:
                    score += 1
                if v2_ratio >= 1:
                    score += 1

                result = {
                    "market": market,
                    "time_v1": times[i],
                    "time_v2": times[j],
                    "price": close[j],
                    "v1_mult": round(v1_mult, 2),
                    "r": round(R, 2),
                    "v2_ratio": round(v2_ratio, 2),
                    "drawdown_pct": round(dd * 100, 2),
                    "breakout": True,
                    "score": round(score, 2),
                }

                if best is None or result["score"] > best["score"]:
                    best = result
                break

        return best

    except Exception:
        return None


def background_scan():
    try:
        markets = get_markets()

        with LOCK:
            SCAN["total"] = len(markets)
            SCAN["progress"] = 0

        results = []

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(analyze, market)
                       for market in markets]

            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result:
                        results.append(result)
                except Exception:
                    pass

                with LOCK:
                    SCAN["progress"] += 1

        results.sort(key=lambda x: x["score"], reverse=True)

        with LOCK:
            SCAN["data"] = results[:10]
            SCAN["done"] = True
            SCAN["running"] = False
            SCAN["finished"] = time.time()

    except Exception as e:
        with LOCK:
            SCAN["error"] = str(e)
            SCAN["done"] = True
            SCAN["running"] = False
            SCAN["finished"] = time.time()


@app.route("/")
def home():
    return """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Upbit 15분봉 패턴 스캐너</title>
<style>
body{font-family:sans-serif;background:#f5f5f5;margin:0;padding:18px}
h1{font-size:25px}
button{width:100%;padding:16px;border:0;border-radius:12px;
background:#111;color:#fff;font-size:18px;font-weight:bold}
button:disabled{opacity:.6}
#status{margin:14px 0;white-space:pre-line}
.card{background:#fff;border-radius:14px;padding:15px;margin:10px 0;
box-shadow:0 2px 8px #ddd}
.small{font-size:13px;color:#666;line-height:1.6}
</style>
</head>
<body>
<h1>🔥 Upbit 15분봉 패턴 스캐너</h1>
<p>V1 → 거래량 수축/가격 유지 → R → V2 → 신고점 돌파</p>
<button id="scanButton" onclick="startScan()">🔄 지금 전체 스캔</button>
<div id="status">대기 중입니다.</div>
<div id="results"></div>

<script>
let timer = null;

async function startScan(){
    const s = document.getElementById("status");
    const r = document.getElementById("results");
    const b = document.getElementById("scanButton");

    b.disabled = true;
    r.innerHTML = "";
    s.textContent = "⏳ 전체 KRW 종목 스캔을 시작합니다...";

    try{
        const q = await fetch("/scan/start", {cache:"no-store"});
        const d = await q.json();

        if(!q.ok){
            throw new Error(d.error || ("HTTP " + q.status));
        }

        pollStatus();
    }catch(e){
        b.disabled = false;
        s.textContent = "❌ 스캔 시작 오류\\n" + e;
    }
}

async function pollStatus(){
    const s = document.getElementById("status");

    try{
        const q = await fetch("/scan/status", {cache:"no-store"});
        const d = await q.json();

        if(d.running){
            s.textContent =
                "⏳ 전체 종목 분석 중...\\n" +
                d.progress + " / " + d.total + " 종목 처리";
            timer = setTimeout(pollStatus, 1000);
            return;
        }

        if(d.error){
            document.getElementById("scanButton").disabled = false;
            s.textContent = "❌ 서버 오류\\n" + d.error;
            return;
        }

        showResults(d.data);

    }catch(e){
        timer = setTimeout(pollStatus, 2000);
    }
}

function showResults(data){
    const s = document.getElementById("status");
    const r = document.getElementById("results");
    const b = document.getElementById("scanButton");

    b.disabled = false;

    if(!data.length){
        s.textContent = "⚠️ 현재 조건에 맞는 종목이 없습니다.";
        return;
    }

    s.textContent = "✅ 스캔 완료 — 상위 " + data.length + "개";

    r.innerHTML = data.map((x,i)=>`
        <div class="card">
            <b>#${i+1} ${x.market}</b>
            <div>가격: ${Number(x.price).toLocaleString()}</div>
            <div>V1: ${x.v1_mult}배</div>
            <div>R: ${x.r}</div>
            <div>V2/V1: ${x.v2_ratio}배</div>
            <div>V1→V2 최대하락: ${x.drawdown_pct}%</div>
            <div>V2 신고점 돌파: ✅</div>
            <div>점수: ${x.score}</div>
            <div class="small">
                V1 ${x.time_v1}<br>
                V2 ${x.time_v2}
            </div>
        </div>
    `).join("");
}
</script>
</body>
</html>"""


@app.route("/scan/start")
def scan_start():
    with LOCK:
        if SCAN["running"]:
            return jsonify({"ok": True, "message": "이미 스캔 중입니다."})

        SCAN["running"] = True
        SCAN["done"] = False
        SCAN["data"] = []
        SCAN["error"] = None
        SCAN["started"] = time.time()
        SCAN["finished"] = 0
        SCAN["progress"] = 0
        SCAN["total"] = 0

    threading.Thread(target=background_scan, daemon=True).start()

    return jsonify({"ok": True, "message": "스캔을 시작했습니다."})


@app.route("/scan/status")
def scan_status():
    with LOCK:
        return jsonify({
            "running": SCAN["running"],
            "done": SCAN["done"],
            "data": SCAN["data"],
            "error": SCAN["error"],
            "progress": SCAN["progress"],
            "total": SCAN["total"],
        })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
