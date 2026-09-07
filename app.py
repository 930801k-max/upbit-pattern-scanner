from flask import Flask

app = Flask(__name__)

@app.route("/")
def home():
    return """
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Upbit Pattern Scanner</title>
    </head>
    <body style="font-family:sans-serif; padding:30px;">
        <h1>🔥 Upbit 패턴 스캐너</h1>
        <p>서버 정상 작동 중입니다.</p>
    </body>
    </html>
    """

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
