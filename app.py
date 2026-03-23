from flask import Flask, jsonify, request
import requests, threading, time, os

app = Flask(__name__)

BASE_URL = "https://accrem.apps.cloud.comcast.net/api/v1"
TOKEN_FILE = "/data/token.txt"
REFRESH_INTERVAL = 45 * 60  # 45 min — under the 50-min server rotation

lock = threading.Lock()
token = None


def load_token():
    """Load persisted token from disk on startup, then fall back to env var."""
    global token
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            t = f.read().strip()
        if t:
            with lock:
                token = t
            print(f"Loaded token from {TOKEN_FILE}", flush=True)
            return True
    env_token = os.environ.get("XFINITY_TOKEN", "").strip()
    if env_token:
        with lock:
            token = env_token
        save_token(env_token)
        print("Loaded token from XFINITY_TOKEN env var", flush=True)
        return True
    print("No token found. POST a token to /setup/token to get started.", flush=True)
    return False


def save_token(t):
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        f.write(t)


def refresh_loop():
    global token
    while True:
        time.sleep(REFRESH_INTERVAL)
        with lock:
            current = token
        if not current:
            continue
        try:
            resp = requests.post(
                f"{BASE_URL}/auth/token/refresh",
                headers={"Authorization": f"Bearer {current}", "Content-Type": "application/json"},
                timeout=10
            )
            if resp.ok:
                new_token = resp.json()["arToken"]
                with lock:
                    token = new_token
                save_token(new_token)
                print(f"Token refreshed at {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
            else:
                print(f"Refresh failed: {resp.status_code} {resp.text}", flush=True)
        except Exception as e:
            print(f"Refresh error: {e}", flush=True)


# ── Setup UI + endpoint ───────────────────────────────────────────────────────

SETUP_PAGE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Xfinity Proxy Setup</title>
  <style>
    body {{ font-family: sans-serif; max-width: 600px; margin: 60px auto; padding: 0 20px; color: #222; }}
    h1 {{ font-size: 1.4rem; margin-bottom: 4px; }}
    .status {{ display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 0.85rem; font-weight: bold; margin-bottom: 24px; }}
    .ready {{ background: #d4edda; color: #155724; }}
    .not-ready {{ background: #f8d7da; color: #721c24; }}
    ol {{ padding-left: 20px; line-height: 1.8; }}
    code {{ background: #f4f4f4; padding: 2px 6px; border-radius: 4px; font-size: 0.9em; }}
    textarea {{ width: 100%; height: 120px; font-family: monospace; font-size: 0.85rem; padding: 8px; box-sizing: border-box; border: 1px solid #ccc; border-radius: 4px; margin-top: 12px; }}
    button {{ margin-top: 10px; padding: 10px 24px; background: #0056b3; color: white; border: none; border-radius: 4px; font-size: 1rem; cursor: pointer; }}
    button:hover {{ background: #004494; }}
    .msg {{ margin-top: 16px; padding: 10px 14px; border-radius: 4px; font-size: 0.95rem; }}
    .msg.ok {{ background: #d4edda; color: #155724; }}
    .msg.err {{ background: #f8d7da; color: #721c24; }}
  </style>
</head>
<body>
  <h1>Xfinity Web Remote Proxy</h1>
  <span class="status {cls}">{status_label}</span>
  {msg_html}
  <p>Paste a token obtained from the Xfinity web remote:</p>
  <ol>
    <li>Open <a href="https://accrem.apps.cloud.comcast.net" target="_blank">accrem.apps.cloud.comcast.net</a> in a browser</li>
    <li>Open DevTools &rarr; Network tab</li>
    <li>Click any remote button (e.g. channel up)</li>
    <li>Find the request to <code>/api/v1/text</code></li>
    <li>Copy the <code>arToken</code> value from the request body</li>
    <li>Paste it below, then <strong>close the browser tab immediately</strong></li>
  </ol>
  <form method="POST" action="/setup/token">
    <textarea name="token" placeholder="eyJhbGciOi..." required></textarea><br>
    <button type="submit">Save Token</button>
  </form>
</body>
</html>"""


@app.route("/", methods=["GET"])
def index():
    with lock:
        ready = token is not None
    cls = "ready" if ready else "not-ready"
    label = "Ready" if ready else "Not configured"
    return SETUP_PAGE.format(cls=cls, status_label=label, msg_html="")


@app.route("/setup/token", methods=["GET"])
def setup_token_get():
    return index()


@app.route("/setup/token", methods=["POST"])
def setup_token():
    """
    Seed the proxy with a token obtained from DevTools.

    Accepts either JSON: {"token": "eyJ..."}
    or plain text body: eyJ...

    How to get the token:
      1. Open https://accrem.apps.cloud.comcast.net in a browser
      2. DevTools → Network → click any remote button → find a request to /api/v1/text
      3. Copy the `arToken` value from the request body (or the Authorization header value
         after stripping the "Bearer " prefix — both are the same token)
      4. POST that value here, then close the browser tab immediately so it
         stops rotating the token
    """
    global token
    # Accept form submission (from UI) or raw JSON/text (from API clients)
    new_token = ""
    if request.content_type and "application/json" in request.content_type:
        data = request.get_json(silent=True)
        if data and "token" in data:
            new_token = data["token"].strip()
    elif request.form.get("token"):
        new_token = request.form["token"].strip()
    else:
        new_token = (request.data or b"").decode().strip()

    if not new_token:
        if request.accept_mimetypes.best == "application/json":
            return jsonify({"error": "Provide {\"token\": \"eyJ...\"} in the request body"}), 400
        msg = '<div class="msg err">No token provided.</div>'
        return SETUP_PAGE.format(cls="not-ready", status_label="Not configured", msg_html=msg), 400

    # Strip "Bearer " prefix if someone pastes the full Authorization header value
    if new_token.lower().startswith("bearer "):
        new_token = new_token[7:].strip()

    with lock:
        token = new_token
    save_token(new_token)
    print(f"Token set via /setup/token at {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)

    if request.accept_mimetypes.best == "application/json":
        return jsonify({"status": "ok", "message": "Token saved. Proxy is ready."})
    msg = '<div class="msg ok">Token saved. Proxy is ready. Use POST /tune/&lt;channel&gt; to send commands.</div>'
    return SETUP_PAGE.format(cls="ready", status_label="Ready", msg_html=msg)


# ── Command endpoint ─────────────────────────────────────────────────────────

@app.route("/tune/<channel>", methods=["POST"])
def tune(channel):
    with lock:
        t = token
    if not t:
        return jsonify({"error": "Not configured. POST a token to /setup/token first."}), 503
    resp = requests.post(
        f"{BASE_URL}/text",
        headers={"Content-Type": "application/json"},
        json={"cmd": channel, "arToken": t},
        timeout=10
    )
    return jsonify({"status": resp.status_code}), resp.status_code


@app.route("/health", methods=["GET"])
def health():
    with lock:
        ready = token is not None
    return jsonify({"ready": ready})


# ── Startup ──────────────────────────────────────────────────────────────────

load_token()
threading.Thread(target=refresh_loop, daemon=True).start()
app.run(host="0.0.0.0", port=8765)

