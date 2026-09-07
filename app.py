from flask import Flask, jsonify
import requests, statistics, time
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)
UPBIT = "https://api.upbit.com/v1"
BASE_BARS, V1_MULT, MIN_R, MAX_R = 20, 5.0, 1.0, 7.0
MIN_V2_RATIO, MAX_LOOKAHEAD, MAX_DRAWDOWN = 0.70, 16, 0.05
CACHE = {"time": 0, "data": None}

def markets():
    r = requests.get(f"{UPBIT}/market/all", params={"isDetails":"false"}, timeout=7)
    r.raise_for_status()
    return [x["market"] for x in r.json() if x["market"].startswith("KRW-")]

def analyze(market):
    try:
        r = requests.get(f"{UPBIT}/candles/minutes/15",
                         params={"market":market,"count":200}, timeout=7)
        r.raise_for_status()
        c = list(reversed(r.json()))
        if len(c) < BASE_BARS + 8: return None
        v = [float(x["candle_acc_trade_volume"]) for x in c]
        close = [float(x["trade_price"]) for x in c]
        high = [float(x["high_price"]) for x in c]
        t = [x["candle_date_time_kst"] for x in c]
        best = None

        for i in range(BASE_BARS, len(c)-3):
            base = statistics.median(v[i-BASE_BARS:i])
            if base <= 0 or v[i] / base < V1_MULT: continue
            v1, p1 = v[i], close[i]

            for j in range(i+2, min(i+MAX_LOOKAHEAD+1, len(c))):
                mid = v[i+1:j]
                dd = min(close[i:j]) / p1 - 1
                if dd < -MAX_DRAWDOWN: break
                if sum(mid)/len(mid) >= v1*0.75: continue

                v2, ratio = v[j], v[j]/v1
                if ratio < MIN_V2_RATIO: continue
                if high[j] <= max(high[i:j]): continue

                R = sum(mid)/v1
                if not (MIN_R <= R <= MAX_R): continue

                local = statistics.median(v[max(0,j-BASE_BARS):j])
                if local <= 0 or v2 < local*3: continue

                score = min(v[i]/base,12)*2 + min(R,5)*1.5 + min(ratio,1.6)*2
                score += 1 if dd >= -0.03 else 0
                score += 1 if ratio >= 1 else 0

                x = {"market":market, "time_v1":t[i], "time_v2":t[j],
                     "price":close[j], "v1_mult":round(v[i]/base,2),
                     "r":round(R,2), "v2_ratio":round(ratio,2),
                     "drawdown_pct":round(dd*100,2), "breakout":True,
                     "score":round(score,2)}
                if best is None or x["score"] > best["score"]: best = x
                break
        return best
    except Exception:
        return None

def run_scan():
    out = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        jobs = [ex.submit(analyze,m) for m in markets()]
        for f in as_completed(jobs):
            x = f.result()
            if x: out.append(x)
    out.sort(key=lambda x:x["score"], reverse=True)
    return out[:10]

@app.route("/")
def home():
    return '''<!doctype html><html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Upbit 15분봉 패턴 스캐너</title>
<style>
body{font-family:sans-serif;background:#f5f5f5;margin:0;padding:18px}
h1{font-size:25px}button{width:100%;padding:16px;border:0;border-radius:12px;background:#111;color:#fff;font-size:18px;font-weight:bold}
#status{margin:14px 0;white-space:pre-line}.card{background:#fff;border-radius:14px;padding:15px;margin:10px 0;box-shadow:0 2px 8px #ddd}.small{font-size:13px;color:#666;line-height:1.6}
</style></head><body>
<h1>🔥 Upbit 15분봉 패턴 스캐너</h1>
<p>V1 → 거래량 수축/가격 유지 → R → V2 → 신고점 돌파</p>
<button onclick="scan()">🔄 지금 전체 스캔</button><div id="status">대기 중입니다.</div><div id="results"></div>
<script>
async function scan(){
 const s=document.getElementById("status"),r=document.getElementById("results");
 s.textContent="⏳ 전체 KRW 종목 분석 중...";r.innerHTML="";
 try{
  const q=await fetch("/scan",{cache:"no-store"}),txt=await q.text();
  if(!q.ok){s.textContent="❌ 서버 오류 HTTP "+q.status+"\n"+txt.slice(0,300);return}
  let d;try{d=JSON.parse(txt)}catch(e){s.textContent="❌ JSON 오류\n"+txt.slice(0,300);return}
  if(!d.length){s.textContent="⚠️ 조건에 맞는 종목이 없습니다.";return}
  s.textContent="✅ 스캔 완료 — 상위 "+d.length+"개";
  r.innerHTML=d.map((x,i)=>`<div class="card"><b>#${i+1} ${x.market}</b>
  <div>가격: ${Number(x.price).toLocaleString()}</div><div>V1: ${x.v1_mult}배</div>
  <div>R: ${x.r}</div><div>V2/V1: ${x.v2_ratio}배</div>
  <div>V1→V2 최대하락: ${x.drawdown_pct}%</div><div>V2 신고점 돌파: ✅</div>
  <div>점수: ${x.score}</div><div class="small">V1 ${x.time_v1}<br>V2 ${x.time_v2}</div></div>`).join("")
 }catch(e){s.textContent="❌ 접속 오류\n"+e}
}
</script></body></html>'''

@app.route("/scan")
def scan():
    now = time.time()
    if CACHE["data"] is not None and now-CACHE["time"] < 60:
        return jsonify(CACHE["data"])
    data = run_scan()
    CACHE.update(time=now, data=data)
    return jsonify(data)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
