# 📺 Xfinity Web Remote Proxy

A small self-hosted proxy that lets you control an Xfinity X1 cable box from Home Assistant (or any HTTP client) by forwarding commands to the [Xfinity Adaptive Remote](https://accrem.apps.cloud.comcast.net) web API.

## ⚙️ How it works

The Xfinity web remote uses a short-lived JWT (`arToken`) that rotates every ~50 minutes. This proxy:

1. Holds the token in memory (and persists it to a Docker volume across restarts)
2. Proactively refreshes it every 45 minutes so it never goes stale
3. Exposes simple HTTP endpoints that Home Assistant can call

## 🚀 Setup

### 1. Deploy

```bash
docker compose up -d
```

Or deploy via Komodo (or any other Docker Compose host) pointing at this repo — no credentials needed since the repo is public.

### 2. Get a token

1. Open [accrem.apps.cloud.comcast.net](https://accrem.apps.cloud.comcast.net) in a browser and pair your TV if you haven't already
2. Open **DevTools → Network tab**
3. Click any button on the web remote
4. Find the request to `/api/v1/text` and copy the `arToken` value from the request body
5. **Close the browser tab immediately** — leaving it open causes the server to rotate the token every 50 min, invalidating yours

### 3. Paste the token

Open `http://<your-host>:8765` in a browser and paste the token into the setup form.

Once saved, the page automatically checks the token against Comcast's API and shows one of:
- 🟢 **Green** — token is valid and the proxy is ready
- 🟠 **Orange** — token is expired; clear it and paste a fresh one
- 🟡 **Yellow** — couldn't reach Comcast's API (network issue)

Use the **Send OK** button to also test that the TV actually responds.

> **Token persistence:** The token is saved to a Docker volume (`xfinity_data`). Container restarts will reload it automatically. The proxy refreshes the token every 45 minutes in the background — you should never need to re-paste it unless you clear it manually.

## 🔌 Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Setup UI (paste token, test, clear) |
| `POST` | `/setup/token` | Set token (JSON `{"token": "eyJ..."}` or plain text) |
| `POST` | `/setup/clear` | Clear the stored token |
| `POST` | `/tune/<channel>` | Tune to a channel number (e.g. `/tune/3225`) |
| `POST` | `/key/<vcode>` | Press a key (e.g. `/key/ENTER`, `/key/UP`, `/key/DOWN`, `/key/BACK`) |
| `GET` | `/health` | Returns `{"ready": true/false}` — token exists in memory |
| `GET` | `/check` | Probes Comcast API to verify token is still valid; refreshes it if so. Result cached 60 s. |

## 🏠 Home Assistant integration

Add to `configuration.yaml`:

```yaml
rest_command:
  xfinity_tune_rtvi:
    url: "http://<your-host>:8765/tune/3225"
    method: POST
  xfinity_tune_rtn:
    url: "http://<your-host>:8765/tune/3226"
    method: POST
  xfinity_ok:
    url: "http://<your-host>:8765/key/ENTER"
    method: POST
```

Then optionally expose them as button entities:

```yaml
template:
  - button:
      - name: "RTVi"
        unique_id: xfinity_button_rtvi
        press:
          action: rest_command.xfinity_tune_rtvi
      - name: "RTN"
        unique_id: xfinity_button_rtn
        press:
          action: rest_command.xfinity_tune_rtn
      - name: "TV OK"
        unique_id: xfinity_button_ok
        press:
          action: rest_command.xfinity_ok
```

Restart Home Assistant after editing `configuration.yaml`.

## 🔧 Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `XFINITY_TOKEN` | No | Optional token to pre-seed on first deploy (skips the UI setup step). Leave blank to use the web UI instead. |

## 🩺 Troubleshooting

**🟠 Status dot is orange ("Token expired")** — The token was invalidated (e.g. another browser session was left open). Click **Clear Token** and paste a fresh one.

**🟡 Status dot is yellow ("Could not verify")** — The container can't reach Comcast's servers. Check outbound internet access from the container.

**Send OK button shows "Upstream returned 401"** — Same as above; token is expired.

**`/health` returns `{"ready": false}`** — No token has been set yet. Open the setup UI and paste one.

**HA rest_command returns an error** — Check that the container is running and reachable from your HA host on port 8765. Confirm `/health` returns `{"ready": true}`.
