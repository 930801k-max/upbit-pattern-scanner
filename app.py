import threading
import statistics
import time
import requests
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)

API = "https://api.upbit.com/v1"
SCAN = {"running": False, "done": 0, "total": 0, "data": [], "error": None}

# 평가 기준
def score_pattern(v1_mult, drawdown, r, v2_ratio, breakout):
    score = 0

    # ① V1 강도
    if v1_mult >= 8: score += 15
    elif v1_mult >= 5: score += 10
    elif v1_mult >= 3: score += 5

    # ② V1 이후 가격 유지
    if drawdown >= -0.01: score += 15
    elif drawdown >= -0.03: score += 10
    elif drawdown >= -0.05: score += 5

    # ③ R = V1 이후 V2 직전 누적거래량 / V1
    if r >= 3 and r < 5: score += 20
    elif r >= 2 and r < 3: score += 15
    elif r >= 5 and r < 7: score += 10
    elif r >= 1 and r < 2: score += 5
    elif r >= 7: score += 3

    # ④ V2 / V1
    if v2_ratio >= 1.0 and v2_ratio < 1.3: score += 15
    elif v2_ratio >= 1.3: score += 10
    elif v2_ratio >= 0.7: score += 10
    elif v2_ratio >= 0.5: score += 5

    # ⑤ 신고점 돌파
    if breakout: score += 10

    if score >= 75: grade = "🔥 최상급"
    elif score >= 60: grade = "🟢 강한 패턴"
    elif score >= 45: grade = "🟡 관심 패턴"
    elif score >= 30: grade = "🟠 약한 패턴"
    else: grade = "🔴 제외"

    return score, grade


def get_krw_markets():
    r = requests.get(API + "/market/all",
                     params={"isDetails": "false"}, timeout=10)
    r.raise_for_status()
    return [x["market"] for x in r.json() if x["market"].startswith("KRW-")]


def analyze_market(market):
    try:
        r = requests.get(
            API + "/candles/minutes/5",
            params={"market": market, "count": 120},
            timeout=10
        )
        r.raise_for_status()
        candles = list(reversed(r.json()))

        # 마지막 봉은 진행 중일 수 있으므로 제외
        if len(candles) > 1:
            candles = candles[:-1]

        if len(candles) < 30:
            return None

        vol = [float(x["candle_acc_trade_volume"]) for x in candles]
        close = [float(x["trade_price"]) for x in candles]
        high = [float(x["high_price"]) for x in candles]

        best = None

        # V1 후보 탐색
        for i in range(20, len(candles) - 2):
            base = statistics.median(vol[i-20:i])
            recent3 = statistics.median(vol[i-3:i])

            if base <= 0 or recent3 <= 0:
                continue

            v1_mult = vol[i] / base
            recent_spike = vol[i] / recent3

            # 강한 V1
            if v1_mult < 5 or recent_spike < 2:
                continue

            p1 = close[i]

            # V1 이후 V2 탐색
            for j in range(i + 2, min(i + 17, len(candles))):
                # V1 이후 가격 최대 하락폭
                dd = min(close[i:j]) / p1 - 1

                if dd < -0.05:
                    break

                middle = vol[i+1:j]

                # V1 직후 거래량이 어느 정도 수축되는지
                if middle and statistics.mean(middle) >= v1_mult * base * 0.75:
                    continue

                # R: V2 제외, V1 다음 봉부터 V2 직전까지
                r_value = sum(middle) / vol[i] if vol[i] > 0 else 0

                if r_value < 1 or r_value > 7:
                    continue

                v2_ratio = vol[j] / vol[i] if vol[i] > 0 else 0
                if v2_ratio < 0.5:
                    continue

                # V2가 V1 이후 구간의 고점을 돌파하는지
                prior_high = max(high[i:j])
                breakout = high[j] > prior_high

                if not breakout:
                    continue

                # V2 자체도 최근 거래량보다 강한지
                local = vol[max(0, j-20):j]
                local_med = statistics.median(local) if local else 0
                if local_med <= 0 or vol[j] < local_med * 3:
                    continue

                score, grade = score_pattern(
                    v1_mult, dd, r_value, v2_ratio, breakout
                )

                item = {
                    "market": market,
                    "score": score,
                    "grade": grade,
                    "price": close[j],
                    "v1_mult": round(v1_mult, 2),
                    "recent_spike": round(recent_spike, 2),
                    "drawdown": round(dd * 100, 2),
                    "r": round(r_value, 2),
                    "v2_ratio": round(v2_ratio, 2),
                    "v1_volume": round(vol[i], 2),
                    "middle_volume": round(sum(middle), 2),
                    "v2_volume": round(vol[j], 2),
                    "time": candles[j]["candle_date_time_kst"]
                }

                if best is None or score > best["score"]:
                    best = item

        return best

    except Exception:
        return None


def run_scan():
    global SCAN
    try:
        markets = get_krw_markets()
        SCAN["total"] = len(markets)
        SCAN["done"] = 0
        SCAN["data"] = []
        SCAN["error"] = None

        results = []
        for market in markets:
            result = analyze_market(market)
            if result:
                results.append(result)
            SCAN["done"] += 1

        results.sort(key=lambda x: x["score"], reverse=True)
        SCAN["data"] = results[:30]

    except Exception as e:
        SCAN["error"] = str(e)
    finally:
        SCAN["running"] = False


@app.route("/")
def home():
    return render_template_string("""
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Upbit 5분봉 패턴 스캐너</title>
<style>
body{font-family:Arial,sans-serif;background:#f5f6f8;margin:0;padding:18px}
.card{background:white;border-radius:14px;padding:16px;margin-bottom:12px;box-shadow:0 2px 8px #ddd}
button{width:100%;padding:15px;border:0;border-radius:10px;background:#111;color:white;font-size:17px}
.small{color:#666;font-size:13px}
.row{display:flex;justify-content:space-between;gap:10px}
.score{font-size:24px;font-weight:bold}
</style>
</head>
<body>
<div class="card">
<h2>🔥 Upbit 5분봉 패턴 스캐너</h2>
<div class="small">원화(KRW) 마켓만 검사 · V1 → R → V2 → 신고점</div>
<br>
<button onclick="startScan()">🔄 지금 전체 스캔</button>
<p id="status">대기 중</p>
</div>
<div id="results"></div>

<script>
async function startScan(){
  document.getElementById("status").innerText="스캔 시작...";
  await fetch("/scan/start",{method:"POST"});
  poll();
}
async function poll(){
  const r=await fetch("/scan/status");
  const s=await r.json();
  document.getElementById("status").innerText =
    s.running ? `검사 중 ${s.done}/${s.total}` :
    s.error ? "오류: "+s.error : `완료 ${s.total}개`;

  if(!s.running) render(s.data);
  else setTimeout(poll,1000);
}
function render(data){
  const el=document.getElementById("results");
  if(!data.length){el.innerHTML='<div class="card">조건을 만족하는 종목이 없습니다.</div>';return;}
  el.innerHTML=data.map(x=>`
  <div class="card">
    <div class="row"><b>${x.market}</b><span class="score">${x.score}점</span></div>
    <b>${x.grade}</b><br><br>
    가격: ${x.price}<br>
    V1: ${x.v1_mult}배 (직전3봉 대비 ${x.recent_spike}배)<br>
    가격 최대하락: ${x.drawdown}%<br>
    <b>R: ${x.r}</b>　(V1→V2 직전 누적 ${x.middle_volume})<br>
    V2/V1: ${x.v2_ratio}배<br>
    V1 거래량: ${x.v1_volume}<br>
    V2 거래량: ${x.v2_volume}<br>
    신고점 돌파: ✅<br>
    <span class="small">${x.time}</span>
  </div>`).join("");
}
</script>
</body>
</html>
""")


@app.post("/scan/start")
def scan_start():
    if SCAN["running"]:
        return jsonify({"ok": True, "message": "이미 검사 중입니다."})
    SCAN["running"] = True
    threading.Thread(target=run_scan, daemon=True).start()
    return jsonify({"ok": True})


@app.get("/scan/status")
def scan_status():
    return jsonify(SCAN)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
