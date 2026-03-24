from flask import Flask, jsonify, request, render_template_string, redirect
import requests, threading, time, os, re

app = Flask(__name__)

BASE_URL = "https://accrem.apps.cloud.comcast.net/api/v1"
TOKEN_FILE = "/data/token.txt"
REFRESH_INTERVAL = 45 * 60  # 45 min. Under the 50-min server rotation

lock = threading.Lock()
token = None
_last_refreshed = 0.0  # unix timestamp of last successful token set/refresh
_check_cache = {"valid": None, "ts": 0.0}  # caches last /check result for up to 1 min
CHECK_CACHE_TTL = 60
_JWT_RE = re.compile(r'^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$')
VERSION = "2026-03-23"


def load_token():
    """Load persisted token from disk on startup, then fall back to env var."""
    global token, _last_refreshed
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            t = f.read().strip()
        if t:
            with lock:
                token = t
            _last_refreshed = time.time()
            print(f"Loaded token from {TOKEN_FILE}", flush=True)
            return True
    env_token = os.environ.get("XFINITY_TOKEN", "").strip()
    if env_token:
        with lock:
            token = env_token
        save_token(env_token)
        _last_refreshed = time.time()
        print("Loaded token from XFINITY_TOKEN env var", flush=True)
        return True
    print("No token found. POST a token to /setup/token to get started.", flush=True)
    return False


def save_token(t):
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        f.write(t)


def clear_token():
    global token, _last_refreshed
    with lock:
        token = None
    _last_refreshed = 0.0
    _check_cache.update({"valid": None, "ts": 0.0})
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)
    print(f"Token cleared at {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)


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
                _last_refreshed = time.time()
                _check_cache.update({"valid": True, "ts": _last_refreshed})
                print(f"Token refreshed at {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
            else:
                print(f"Refresh failed: {resp.status_code} {resp.text}", flush=True)
        except Exception as e:
            print(f"Refresh error: {e}", flush=True)


PAGE = """<!DOCTYPE html>
<html lang="en" class="h-full bg-gray-50">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Xfinity Remote Proxy</title>
  <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23374151' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'><path d='M6 20.25h12m-7.5-3v3m3-3v3m-10.125-3h17.25c.621 0 1.125-.504 1.125-1.125V4.875c0-.621-.504-1.125-1.125-1.125H3.375c-.621 0-1.125.504-1.125 1.125v11.25c0 .621.504 1.125 1.125 1.125z'/></svg>">
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="min-h-full flex items-center justify-center p-4">
  <div class="w-full max-w-lg space-y-4">

    <div class="text-center">
      <h1 class="text-2xl font-semibold tracking-tight text-gray-900">Xfinity Remote Proxy</h1>
      <p class="mt-1 text-sm text-gray-500">Self-hosted token bridge for Home Assistant</p>
    </div>

    {# -- Status -- #}
    <div class="rounded-lg border bg-white shadow-sm px-5 py-4 flex items-center gap-3">
      {% if ready %}
      <span id="status-dot" class="w-2.5 h-2.5 rounded-full flex-shrink-0 bg-gray-300 animate-pulse"></span>
      <div>
        <span id="status-text" class="text-sm font-medium text-gray-400">Checking token&hellip;</span>
        <div id="status-sub" class="hidden text-xs text-gray-400 mt-0.5"></div>
      </div>
      {% else %}
      <span class="w-2.5 h-2.5 rounded-full flex-shrink-0 bg-red-400"></span>
      <span class="text-sm font-medium text-red-600">No token &mdash; setup required</span>
      {% endif %}
    </div>

    {# -- Flash message -- #}
    {% if msg == 'saved' %}
    <div class="rounded-lg bg-green-50 border border-green-200 text-green-800 px-4 py-3 text-sm">
      &#10003; Token saved. Proxy is ready.
    </div>
    {% elif msg == 'cleared' %}
    <div class="rounded-lg bg-sky-50 border border-sky-200 text-sky-800 px-4 py-3 text-sm">
      Token cleared. Paste a new one below.
    </div>
    {% elif msg == 'empty' %}
    <div class="rounded-lg bg-red-50 border border-red-200 text-red-800 px-4 py-3 text-sm">
      No token provided &mdash; please paste one below.
    </div>
    {% elif msg == 'invalid' %}
    <div class="rounded-lg bg-red-50 border border-red-200 text-red-800 px-4 py-3 text-sm">
      Invalid token &mdash; must be a JWT. Copy the <code class="bg-red-100 px-1 rounded text-xs">arToken</code> value from the Network tab request body.
    </div>
    {% endif %}

    {% if ready %}
    {# -- Test -- #}
    <div class="rounded-lg border bg-white shadow-sm px-5 py-4">
      <h2 class="text-sm font-semibold text-gray-900 mb-1">Test connection</h2>
      <p class="text-xs text-gray-500 mb-3">Sends an OK keypress to the TV to verify the token is working.</p>
      <button id="test-btn" onclick="testOk(this)"
        class="inline-flex items-center px-3 py-1.5 rounded-md bg-gray-900 text-white text-sm font-medium hover:bg-gray-700 disabled:opacity-50 disabled:cursor-wait transition-colors">
        Send OK
      </button>
      <div id="test-result" class="hidden mt-3 rounded-md px-3 py-2 text-sm"></div>
    </div>

    {# -- Clear -- #}
    <div class="rounded-lg border bg-white shadow-sm px-5 py-4">
      <h2 class="text-sm font-semibold text-gray-900 mb-1">Remove token</h2>
      <p class="text-xs text-gray-500 mb-3">Clear the stored token to start over with a fresh one.</p>
      <form method="POST" action="/setup/clear">
        <button type="submit"
          class="inline-flex items-center px-3 py-1.5 rounded-md bg-red-600 text-white text-sm font-medium hover:bg-red-700 transition-colors">
          Clear Token
        </button>
      </form>
    </div>
    {% endif %}

    {# -- Token form -- #}
    {% if ready %}
    <details class="rounded-lg border bg-white shadow-sm group">
      <summary class="px-5 py-4 cursor-pointer list-none flex items-center justify-between hover:bg-gray-50 rounded-lg transition-colors">
        <span class="text-sm font-semibold text-gray-900">Update token</span>
        <svg class="w-4 h-4 text-gray-400 group-open:rotate-180 transition-transform" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
      </summary>
      <div class="px-5 pb-4 pt-3 border-t border-gray-100">
        <ol class="text-sm text-gray-600 space-y-1 mb-4 list-decimal list-inside leading-relaxed">
          <li>Open <a href="https://accrem.apps.cloud.comcast.net" target="_blank" class="text-blue-600 hover:underline">accrem.apps.cloud.comcast.net</a> in a browser</li>
          <li>Open DevTools &rarr; Network tab</li>
          <li>Click any button on the remote</li>
          <li>Find the request to <code class="bg-gray-100 px-1 rounded text-xs">/api/v1/text</code></li>
          <li>Copy the <code class="bg-gray-100 px-1 rounded text-xs">arToken</code> value from the request body</li>
          <li><strong>Close the browser tab immediately</strong> after copying</li>
        </ol>
        <form method="POST" action="/setup/token" onsubmit="return validateToken(this)">
          <textarea name="token" placeholder="eyJhbGciOi..." required
            class="w-full h-24 rounded-md border border-gray-300 px-3 py-2 font-mono text-xs focus:outline-none focus:ring-2 focus:ring-gray-900 resize-none"></textarea>
          <div id="token-error" class="hidden mt-2 text-xs text-red-600"></div>
          <button type="submit"
            class="mt-2 inline-flex items-center px-3 py-1.5 rounded-md bg-gray-900 text-white text-sm font-medium hover:bg-gray-700 transition-colors">
            Save Token
          </button>
        </form>
      </div>
    </details>
    {% else %}
    <div class="rounded-lg border bg-white shadow-sm px-5 py-4">
      <h2 class="text-sm font-semibold text-gray-900 mb-3">Configure token</h2>
      <ol class="text-sm text-gray-600 space-y-1 mb-4 list-decimal list-inside leading-relaxed">
        <li>Open <a href="https://accrem.apps.cloud.comcast.net" target="_blank" class="text-blue-600 hover:underline">accrem.apps.cloud.comcast.net</a> in a browser</li>
        <li>Open DevTools &rarr; Network tab</li>
        <li>Click any button on the remote</li>
        <li>Find the request to <code class="bg-gray-100 px-1 rounded text-xs">/api/v1/text</code></li>
        <li>Copy the <code class="bg-gray-100 px-1 rounded text-xs">arToken</code> value from the request body</li>
        <li><strong>Close the browser tab immediately</strong> after copying</li>
      </ol>
      <form method="POST" action="/setup/token" onsubmit="return validateToken(this)">
        <textarea name="token" placeholder="eyJhbGciOi..." required
          class="w-full h-24 rounded-md border border-gray-300 px-3 py-2 font-mono text-xs focus:outline-none focus:ring-2 focus:ring-gray-900 resize-none"></textarea>
        <div id="token-error" class="hidden mt-2 text-xs text-red-600"></div>
        <button type="submit"
          class="mt-2 inline-flex items-center px-3 py-1.5 rounded-md bg-gray-900 text-white text-sm font-medium hover:bg-gray-700 transition-colors">
          Save Token
        </button>
      </form>
    </div>
    {% endif %}

    <p class="text-center text-xs text-gray-400">
      <a href="https://github.com/dimatx/xfinity-web-remote-proxy" target="_blank" class="hover:underline">dimatx/xfinity-web-remote-proxy</a>
      <span class="mx-1">&middot;</span>
      <span>{{ version }}</span>
    </p>
  </div>

  <script>
    {% if ready %}
    (function checkToken() {
      fetch('/check')
        .then(function(r) { return r.json(); })
        .then(function(data) {
          var dot = document.getElementById('status-dot');
          var txt = document.getElementById('status-text');
          dot.classList.remove('animate-pulse', 'bg-gray-300');
          if (data.valid) {
            dot.classList.add('bg-green-500');
            txt.className = 'text-sm font-medium text-green-700';
            txt.textContent = 'Token valid \u2014 proxy is ready';
            if (data.next_refresh_at) {
              var mins = Math.round((data.next_refresh_at - Date.now() / 1000) / 60);
              var sub = document.getElementById('status-sub');
              sub.textContent = 'Next refresh in ' + (mins > 0 ? mins + '\u202fmin' : 'less than a minute');
              sub.classList.remove('hidden');
            }
          } else {
            dot.classList.add('bg-orange-400');
            txt.className = 'text-sm font-medium text-orange-700';
            txt.textContent = 'Token expired \u2014 clear and re-paste a fresh one';
          }
        })
        .catch(function() {
          var dot = document.getElementById('status-dot');
          var txt = document.getElementById('status-text');
          dot.classList.remove('animate-pulse', 'bg-gray-300');
          dot.classList.add('bg-yellow-400');
          txt.className = 'text-sm font-medium text-yellow-700';
          txt.textContent = 'Could not verify token (network error)';
        });
    })();
    {% endif %}

    function validateToken(form) {
      var el = document.getElementById('token-error');
      var val = form.querySelector('textarea[name="token"]').value.trim();
      if (val.toLowerCase().startsWith('bearer ')) { val = val.slice(7).trim(); }
      var parts = val.split('.');
      if (parts.length !== 3 || !parts.every(function(p) { return /^[A-Za-z0-9_-]+$/.test(p); })) {
        el.textContent = 'Not a valid JWT \u2014 copy the arToken value from the Network tab request body.';
        el.classList.remove('hidden');
        return false;
      }
      el.classList.add('hidden');
      return true;
    }

    function testOk(btn) {
      btn.disabled = true;
      btn.textContent = 'Sending\u2026';
      var el = document.getElementById('test-result');
      el.className = 'mt-3 rounded-md px-3 py-2 text-sm';
      el.textContent = '';
      el.classList.remove('hidden');
      fetch('/key/ENTER', {method: 'POST'})
        .then(function(r) {
          return r.json().then(function(body) { return {ok: r.ok, status: r.status, body: body}; });
        })
        .then(function(res) {
          var upstream = (res.body && res.body.upstream_status) ? res.body.upstream_status : res.status;
          if (res.ok && upstream === 200) {
            el.classList.add('bg-green-50', 'border', 'border-green-200', 'text-green-800');
            el.textContent = '\u2713 Success \u2014 the TV should have responded.';
          } else {
            el.classList.add('bg-red-50', 'border', 'border-red-200', 'text-red-800');
            el.textContent = '\u2717 Upstream returned ' + upstream + '. Token may be expired \u2014 clear and re-paste it.';
          }
        })
        .catch(function(e) {
          el.classList.add('bg-red-50', 'border', 'border-red-200', 'text-red-800');
          el.textContent = '\u2717 Request failed: ' + e;
        })
        .finally(function() {
          btn.disabled = false;
          btn.textContent = 'Send OK';
        });
    }
  </script>
</body>
</html>"""


def render_page(msg=""):
    with lock:
        ready = token is not None
    return render_template_string(PAGE, ready=ready, msg=msg, version=VERSION)


@app.route("/", methods=["GET"])
def index():
    return render_page(msg=request.args.get("msg", ""))


@app.route("/setup/token", methods=["GET"])
def setup_token_get():
    return redirect("/")


@app.route("/setup/token", methods=["POST"])
def setup_token():
    global token
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
        return redirect("/?msg=empty")

    if new_token.lower().startswith("bearer "):
        new_token = new_token[7:].strip()

    if not _JWT_RE.match(new_token):
        if request.accept_mimetypes.best == "application/json":
            return jsonify({"error": "Token must be a valid JWT (three base64url parts separated by dots)"}), 400
        return redirect("/?msg=invalid")

    with lock:
        token = new_token
    save_token(new_token)
    _last_refreshed = time.time()
    _check_cache.update({"valid": None, "ts": 0.0})
    print(f"Token set via /setup/token at {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)

    if request.accept_mimetypes.best == "application/json":
        return jsonify({"status": "ok", "message": "Token saved. Proxy is ready."})
    return redirect("/?msg=saved")


@app.route("/setup/clear", methods=["POST"])
def setup_clear():
    clear_token()
    if request.accept_mimetypes.best == "application/json":
        return jsonify({"status": "ok", "message": "Token cleared."})
    return redirect("/?msg=cleared")


@app.route("/key/<vcode>", methods=["POST"])
def key(vcode):
    """Send a key press. E.g. POST /key/ENTER, /key/UP, /key/DOWN, /key/BACK"""
    with lock:
        t = token
    if not t:
        return jsonify({"error": "Not configured. POST a token to /setup/token first."}), 503
    resp = requests.post(
        f"{BASE_URL}/processKey",
        headers={"Authorization": f"Bearer {t}", "Content-Type": "application/json"},
        json={"vcode": vcode.upper()},
        timeout=10
    )
    return jsonify({"upstream_status": resp.status_code}), 200


@app.route("/tune/<channel>", methods=["POST"])
def tune(channel):
    """Tune to a channel number. E.g. POST /tune/3225"""
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
    return jsonify({"upstream_status": resp.status_code}), 200


@app.route("/health", methods=["GET"])
def health():
    with lock:
        ready = token is not None
    return jsonify({"ready": ready})


@app.route("/check", methods=["GET"])
def check():
    """Probe the Comcast refresh endpoint to confirm the token is still valid."""
    global token, _last_refreshed
    with lock:
        t = token
    if not t:
        return jsonify({"valid": False, "reason": "no_token"})
    now = time.time()
    if _check_cache["valid"] is not None and (now - _check_cache["ts"]) < CHECK_CACHE_TTL:
        return jsonify({"valid": _check_cache["valid"], "cached": True,
                        "next_refresh_at": _last_refreshed + REFRESH_INTERVAL})
    try:
        resp = requests.post(
            f"{BASE_URL}/auth/token/refresh",
            headers={"Authorization": f"Bearer {t}", "Content-Type": "application/json"},
            timeout=10
        )
        if resp.ok:
            new_token = resp.json().get("arToken", t)
            with lock:
                token = new_token
            save_token(new_token)
            _last_refreshed = now
            _check_cache.update({"valid": True, "ts": now})
            print(f"Token check: valid (refreshed) at {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
            return jsonify({"valid": True, "next_refresh_at": _last_refreshed + REFRESH_INTERVAL})
        else:
            _check_cache.update({"valid": False, "ts": now})
            return jsonify({"valid": False, "upstream_status": resp.status_code})
    except Exception as e:
        return jsonify({"valid": None, "reason": str(e)})


load_token()
threading.Thread(target=refresh_loop, daemon=True).start()

# Entry point for direct execution (dev only); production uses gunicorn
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8765)
