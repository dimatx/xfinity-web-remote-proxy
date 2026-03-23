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


# ── Setup endpoint ───────────────────────────────────────────────────────────

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
    data = request.get_json(silent=True)
    if data and "token" in data:
        new_token = data["token"].strip()
    else:
        new_token = (request.data or b"").decode().strip()
    if not new_token:
        return jsonify({"error": "Provide {\"token\": \"eyJ...\"} in the request body"}), 400
    # Strip "Bearer " prefix if someone pastes the full Authorization header value
    if new_token.lower().startswith("bearer "):
        new_token = new_token[7:].strip()
    with lock:
        token = new_token
    save_token(new_token)
    print(f"Token set via /setup/token at {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    return jsonify({"status": "ok", "message": "Token saved. Proxy is ready. Use POST /tune/<channel>"})


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

