"""Place a real call as a fake caller, over CDP.

Everything here is genuine except the mouth: real LiveKit, real STT, real
model, real TTS, the real station. The microphone is a WAV Chrome is pretending
is a capture device.

Deliberately says nothing destructive — no skip, no takeover, no segment. Every
station-wide permission is 'open' on this deployment, so a test caller asking
for a skip would really cut the record its listeners are hearing.
"""
import json
import os
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from websocket import create_connection      # websocket-client

# The front door and the guest code are the OPERATOR'S, and this repo is
# public — they come from the environment, never from here. See
# .claude/OPERATOR.local.md, which is gitignored and holds the real values.
CDP = os.environ.get("TALKWAVE_CDP") or "http://localhost:9333"
URL = os.environ.get("TALKWAVE_URL") or ""
CODE = os.environ.get("TALKWAVE_GUEST_CODE") or ""

if not URL:
    raise SystemExit(
        "Set TALKWAVE_URL (and TALKWAVE_GUEST_CODE if the line is gated).\n"
        "The real values are in .claude/OPERATOR.local.md.")


def tab():
    pages = json.load(urllib.request.urlopen(CDP + "/json"))
    for p in pages:
        if p.get("type") == "page":
            return p
    raise SystemExit("no page target")


class Session:
    def __init__(self, ws_url):
        self.ws = create_connection(ws_url, timeout=30)
        self.n = 0

    def send(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method,
                                 "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expr, await_promise=False):
        r = self.send("Runtime.evaluate", expression=expr, returnByValue=True,
                      awaitPromise=await_promise)
        res = r.get("result", {})
        if r.get("exceptionDetails"):
            return {"_error": str(r["exceptionDetails"])[:300]}
        return res.get("value")


def main():
    s = Session(tab()["webSocketDebuggerUrl"])
    s.send("Page.enable")
    s.send("Runtime.enable")

    # Seed the guest code before the page reads it, so the door is already
    # open and the run is not testing the sign-in form.
    s.send("Page.navigate", url=URL)
    time.sleep(4)
    s.js(f"localStorage.setItem('callinCallKey', '{CODE}');"
         f"localStorage.setItem('callinCallKeyAt', String(Date.now()));")
    s.send("Page.navigate", url=URL)
    time.sleep(8)

    print("tier   :", s.js("(window.__live && window.__live.callerTier) || 'unknown'"))
    print("button :", s.js("document.getElementById('callBtn').textContent.trim()"))
    print("gate up:", s.js("!document.getElementById('guestGate').hidden"))

    s.js("document.getElementById('callBtn').click()")
    print("\n--- call placed, watching ---")

    # Push to talk is ON, so the microphone stays shut until the bar is
    # pressed — the first run had the DJ correctly telling the caller it
    # could not hear them. Tap once, after the greeting, then leave it open:
    # tap-to-talk is a toggle, not a hold.
    opened = False

    seen = set()
    started = time.time()
    while time.time() - started < 175:
        time.sleep(5)
        state = s.js("""JSON.stringify({
            btn: document.getElementById('callBtn').textContent.trim(),
            state: (document.getElementById('stateText')||{}).textContent,
            status: (document.getElementById('statusText')||{}).textContent,
            onair: document.querySelector('.card').classList.contains('onair'),
            bar: (document.getElementById('pttMain')||{}).textContent,
            barOff: !!(document.getElementById('pttBtn')||{}).disabled,
            caps: [...document.querySelectorAll('#captions .cap')]
                    .map(e => e.textContent.replace(/\\s+/g,' ').trim()).slice(-2)
        })""")
        if state and state not in seen:
            seen.add(state)
            d = json.loads(state)
            print("%6.0fs %-13s onair=%-5s bar=%-42s %s"
                  % (time.time() - started, (d["state"] or "")[:13],
                     d["onair"], (d["bar"] or "")[:42],
                     (d["caps"][-1][:70] if d["caps"] else "")))
        if not opened:
            st = s.js("(document.getElementById('stateText')||{}).textContent")
            if (st or "") == "Listening":
                # The bar is bound to POINTER events, not click: press opens
                # the line, and a tap (under HOLD_MS) leaves it open. So a
                # quick down/up is exactly "tap to talk".
                s.js("""(() => {
                  const b = document.getElementById('pttBtn');
                  const ev = (t) => b.dispatchEvent(new PointerEvent(t, {
                    bubbles: true, cancelable: true, pointerId: 1,
                    pointerType: 'touch' }));
                  ev('pointerdown'); ev('pointerup');
                  return true;
                })()""")
                opened = True
                print("       -- mic opened (tap to talk) --")
        if s.js("document.getElementById('callBtn').textContent.trim()") == "Call":
            print("call ended")
            break

    s.js("var h=document.getElementById('hangBtn'); if(h && !h.hidden) h.click();")
    time.sleep(2)
    print("\ndone")


main()
