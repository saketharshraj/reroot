"""Drive the live ReRoot Studio in Chrome and record a high-res CDP screencast plus an event log.

    uv run --with playwright python record.py OUT_DIR

Frames: OUT_DIR/frames/NNNNNN.jpg (2x device pixels) + frames.json [{i, t}]
Events: OUT_DIR/events.json  (marks, element boxes in CSS px, cursor moves/clicks, page text)
"""

import asyncio
import base64
import json
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

URL = "https://reroot-studio-alpha.vercel.app/"
W, H, DSF = 1440, 810, 2
OUT = Path(sys.argv[1])
(OUT / "frames").mkdir(parents=True, exist_ok=True)
frames, events = [], []
cursor = [W * 0.6, H * 0.55]


def log(kind, name=None, **kw):
    e = {"t": time.time(), "kind": kind, "name": name} | kw
    events.append(e)
    print(f"{e['t']:.2f} {kind} {name or ''} {json.dumps(kw)[:140]}", flush=True)
    return e


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel="chrome", headless=False, args=["--window-position=40,40"])
        ctx = await browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=DSF)
        page = await ctx.new_page()
        cdp = await ctx.new_cdp_session(page)

        async def on_frame(p):
            i = len(frames)
            (OUT / "frames" / f"{i:06d}.jpg").write_bytes(base64.b64decode(p["data"]))
            frames.append({"i": i, "t": p["metadata"]["timestamp"]})
            try:
                await cdp.send("Page.screencastFrameAck", {"sessionId": p["sessionId"]})
            except Exception:  # noqa: BLE001
                pass

        state = {"capturing": False}

        async def capture_loop():
            """2x device-pixel screenshots (~16 fps): crisp enough to zoom into."""
            while state["capturing"]:
                t0 = time.time()
                try:
                    data = await page.screenshot(type="jpeg", quality=90)
                except Exception:  # noqa: BLE001
                    await asyncio.sleep(0.05)
                    continue
                i = len(frames)
                (OUT / "frames" / f"{i:06d}.jpg").write_bytes(data)
                frames.append({"i": i, "t": (t0 + time.time()) / 2})

        async def box(name, sel, nth=0):
            loc = page.locator(sel).nth(nth)
            try:
                await loc.wait_for(state="visible", timeout=8000)
                b = await loc.bounding_box()
            except Exception as e:  # noqa: BLE001
                print("box miss", name, sel, e)
                return None
            log("box", name, box=b, sel=sel)
            return b

        async def text(name, sel):
            try:
                t = await page.locator(sel).last.inner_text(timeout=4000)
            except Exception:  # noqa: BLE001
                t = ""
            log("text", name, text=t)

        async def move_to(x, y, dur=0.7):
            x0, y0 = cursor
            t0 = time.time()
            steps = max(8, int(dur * 40))
            for k in range(1, steps + 1):
                f = k / steps
                f = f * f * (3 - 2 * f)
                await page.mouse.move(x0 + (x - x0) * f, y0 + (y - y0) * f)
                await asyncio.sleep(dur / steps)
            log("move", None, frm=[x0, y0], to=[x, y], t0=t0, t1=time.time())
            cursor[:] = [x, y]

        async def click(name, sel, nth=0, dx=0.5, dy=0.5):
            loc = page.locator(sel).nth(nth)
            await loc.wait_for(state="visible", timeout=20000)
            await loc.scroll_into_view_if_needed()
            b = await loc.bounding_box()
            x, y = b["x"] + b["width"] * dx, b["y"] + b["height"] * dy
            await move_to(x, y)
            await asyncio.sleep(0.15)
            log("click", name, at=[x, y], box=b)
            await page.mouse.click(x, y)

        async def wait_turn(name, timeout=300):
            try:
                await page.wait_for_selector('button[aria-label="Stop"]', state="attached", timeout=15000)
            except Exception:  # noqa: BLE001
                pass
            t_end = time.time() + timeout
            while await page.locator('button[aria-label="Stop"]').count() and time.time() < t_end:
                try:  # where the step list is right now (it scrolls up once the reply text lands)
                    b = await page.locator(".msg.assistant").last.locator(".steps").bounding_box(timeout=500)
                    if b and b["y"] > 60:
                        log("box", f"{name}_steps", box=b)
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.sleep(0.8)
            log("mark", f"{name}_done")
            err = await page.locator(".msg.assistant").last.locator(".msg-error").count()
            return err == 0

        async def ask(name, prompt):
            for attempt in range(2):
                await click(f"{name}_input", ".composer textarea")
                log("mark", f"{name}_type")
                await page.keyboard.type(prompt, delay=28)
                await asyncio.sleep(0.4)
                log("mark", f"{name}_send", prompt=prompt)
                await page.keyboard.press("Enter")
                if await wait_turn(name):
                    await text(f"{name}_reply", ".msg.assistant .msg-body")
                    return True
                print("turn failed, retrying", name)
            return False

        async def reveal(name, sel, nth=0, block="start"):
            await page.locator(sel).nth(nth).evaluate(f"e => e.scrollIntoView({{behavior: 'smooth', block: '{block}'}})")
            log("mark", f"{name}_reveal")
            await asyncio.sleep(1.4)

        async def pause(s):
            await asyncio.sleep(s)

        async def wheel(name, sel, dy, times=3):
            b = await page.locator(sel).first.bounding_box()
            await move_to(b["x"] + b["width"] * 0.5, b["y"] + min(b["height"] * 0.6, 500))
            log("mark", f"{name}_scroll")
            for _ in range(times):
                await page.mouse.wheel(0, dy)
                await asyncio.sleep(0.45)

        await page.goto(URL)
        await page.wait_for_selector(".roles .role", timeout=60000)
        state["capturing"] = True
        cap = asyncio.ensure_future(capture_loop())
        await pause(5)  # map tiles settle
        log("mark", "start")
        await box("welcome", ".welcome")
        await box("roles", ".roles")
        await box("map", ".canvas")
        await pause(2.5)

        # 1. Developer: role card sends the bypass brief
        log("mark", "dev_start")
        await click("dev_role", ".roles .role", nth=0)
        log("mark", "dev_send")
        await wait_turn("dev")
        await text("dev_reply", ".msg.assistant .msg-body")
        await pause(3)
        await box("dev_steps", ".msg.assistant .steps")
        await box("dev_side", ".side")
        await box("dev_assess", ".side .card", 0)
        await box("dev_sites", ".side .card", 1)
        await text("dev_side_text", ".side")
        await pause(2)
        await click("dev_site1", ".site-row", 0)
        await pause(2.5)
        await box("dev_site1_detail", ".sitelist li.open")
        await pause(2)

        # 2. Plan
        await ask("plan", "Write the compensatory planting plan")
        await pause(3)
        await box("plan_doc", ".plan")
        await box("plan_kpis", ".plan-kpis")
        await text("plan_text", ".plan")
        await wheel("plan", ".plan-wrap", 380, 4)
        await pause(1.5)
        await wheel("plan2", ".plan-wrap", 380, 4)
        await pause(2)

        # 3. Monitoring + field alerts
        await ask("mon", "How are our plantations doing? Alert the caretakers where action is needed.")
        try:
            await page.wait_for_selector(".phone .pmsg.in audio", timeout=120000)
        except Exception:  # noqa: BLE001
            print("no voice note arrived")
        log("mark", "mon_alert")
        await pause(2)
        await box("mon_list", ".side .card", 0)
        await box("mon_phone", ".phone")
        await text("mon_side_text", ".side")
        await text("mon_phone_text", ".phone")
        await pause(1)
        try:
            await click("mon_english", ".phone button.link", 0)
            await pause(2.5)
            await box("mon_phone_msg", ".phone .pmsg.in", 0)
        except Exception as e:  # noqa: BLE001
            print("english toggle miss", e)
        await pause(2)
        await click("mon_close_phone", ".phone button.x")
        await pause(1)

        # 4. Data centre screening
        await ask("dc", "Screen a 50 MW AI data centre near Gwal Pahari for water risk and show me its resource loop.")
        await pause(4)
        await box("dc_card", ".side .card", 0)
        await box("dc_risk", ".risk-grid")
        await box("dc_bars", ".side .card h4.mini-h", 0)
        await text("dc_side_text", ".side")
        await pause(2.5)
        await reveal("dc_loopview", ".loop", 0, "start")
        await box("dc_loop", ".loop")
        await box("dc_flows", ".flows")
        await pause(3.5)

        # 5. Water replenishment verification
        await ask("water", "Our data centre funds lake projects to be water positive. Are they actually holding water? Show me the ledger.")
        await pause(3)
        await box("water_ledger", ".side .card", 0)
        await box("water_bars", ".ledger-bars")
        await box("water_list", ".side .card", 1)
        await text("water_side_text", ".side")
        await pause(2)
        await click("water_badkhal", ".plist button", 0)
        await pause(2.5)
        await reveal("water_detail", ".side .card", 2, "start")
        await box("water_detail_head", ".side .card >> nth=2")
        await pause(2.5)
        await box("water_chart", ".side .chart", 0)
        await box("water_season", ".season")
        await box("water_table", ".wtable")
        await text("water_detail_text", ".side .card >> nth=2")
        await pause(1.5)
        await reveal("water_table_view", ".wtable", 0, "center")
        await box("water_table2", ".wtable")
        await box("water_season2", ".season")
        await pause(3)
        await ask("tl", "Show a timelapse of Badkhal Lake revival")
        await page.wait_for_selector(".media-card img", timeout=60000)
        log("mark", "tl_shown")
        await box("tl_card", ".media-card")
        await pause(10)  # let the GIF play through 2019-2026
        log("mark", "end")
        await pause(1)
        state["capturing"] = False
        await cap
        await asyncio.sleep(1)
        await browser.close()

    (OUT / "frames.json").write_text(json.dumps(frames))
    (OUT / "events.json").write_text(json.dumps(events, indent=1))
    print("frames", len(frames), "events", len(events))


asyncio.run(main())
