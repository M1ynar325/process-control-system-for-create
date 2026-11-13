#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PCS-Bridge —— 给 Create 物流网络做一层「下单接口」的最小可跑骨架

    浏览器/上位机/外部系统  <--ws+http-->  本进程  <--ws-->  CC:Tweaked 电脑  --红石-->  红石请求器
                                                                                        ↓
                                                                                  物流网络发货

Create 6 的真实接口（不是编的）：
  · 红石请求器 Redstone Requester —— 收到一次红石信号就向绑定的仓储链接站发一次请求。
    请求哪些物品、送到哪个地址，是在它的 GUI 里配的，所以「一个通道 = 一个 SKU」。
  · 存量转信器 Threshold Switch —— 检测前方容器存量，只输出 15 或 0（不是模拟量），
    上下阈值自带迟滞。拿来当「库存够/不够」的反馈位。

本进程负责：把一次"下单"翻译成一次红石脉冲，把库存位翻译成人能看的东西，
顺便记流水、记拒绝、出报警。游戏里那段 Lua 是通用驱动，换机器不用改。

跑起来：  python3 bridge.py
浏览器：  http://<本机IP>:8080
CC 电脑： http.websocket("ws://<本机IP>:8765")  —— 连上会先收到一条 cfg
"""

import asyncio
import json
import os
import sqlite3
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from websockets.asyncio.server import serve

ROOT = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(ROOT, "tags.json"), encoding="utf-8"))
DB_PATH = os.path.join(ROOT, "data", "history.db")

TAGS = {t["id"]: t for t in CFG["tags"]}
AREA = CFG.get("area", "AREA")
COUNTERS = CFG.get("counters") or {}
HOST = CFG["bind"]["host"]
WS_PORT = CFG["bind"]["ws_port"]
HTTP_PORT = CFG["bind"]["http_port"]

# ---------------------------------------------------------------- 运行时状态

STATE = {}   # tag -> 当前值
TS = {}      # tag -> 最后一次变化的时间戳
WEB = set()  # 浏览器 ws 连接
MC = set()   # CC 电脑 ws 连接
LATCH = {}   # 联锁报警：code -> {code,prio,msg,tag}
STOCK = {}   # 物品 id -> 数量（来自仓储发报机 ticker.stock()）
CAPS = {}    # 电脑报告它挂了哪些 Create 外设
DB = None


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def coerce(tag_def, value):
    """按标签类型收口，别让 Lua 那边把 bool 发成 0/1 就乱套。"""
    kind = tag_def.get("type", "bool")
    try:
        if kind in ("bool", "pulse"):
            if isinstance(value, str):
                return value.lower() in ("true", "1", "on", "yes")
            return bool(value)
        if kind == "int":
            return int(round(float(value)))
        return float(value)
    except (TypeError, ValueError):
        return None


OPS = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def active_alarms():
    out = [dict(v) for v in LATCH.values()]
    for a in CFG.get("alarms", []):
        v = STATE.get(a["tag"])
        if v is None:
            continue                      # 还没拿到数据就不报，免得一开机满屏红
        try:
            hit = OPS[a["op"]](v, a["value"])
        except TypeError:
            hit = False
        if hit:
            out.append({"code": a["code"], "prio": a["prio"], "msg": a["msg"],
                        "tag": a["tag"]})
    out.sort(key=lambda x: x["prio"])
    return out


# ---------------------------------------------------------------- 持久化

def init_db():
    global DB
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    DB = sqlite3.connect(DB_PATH, check_same_thread=False)
    DB.execute("CREATE TABLE IF NOT EXISTS hist (ts REAL, tag TEXT, val TEXT)")
    DB.execute("CREATE INDEX IF NOT EXISTS hist_tag ON hist(tag, ts)")
    DB.execute("CREATE TABLE IF NOT EXISTS orders ("
               "ts REAL, items TEXT, address TEXT, ok INT, note TEXT)")
    DB.commit()


def hist(tag, value):
    try:
        DB.execute("INSERT INTO hist VALUES (?,?,?)", (time.time(), tag, json.dumps(value)))
        DB.commit()
    except sqlite3.Error as e:          # 历史库挂了不能拖垮实时控制
        log("[DB]", e)


def record_order(items, address, ok, note):
    try:
        DB.execute("INSERT INTO orders VALUES (?,?,?,?,?)",
                   (time.time(), items, address, 1 if ok else 0, note))
        DB.commit()
    except sqlite3.Error as e:
        log("[DB]", e)


# ---------------------------------------------------------------- 广播

async def push(clients, obj):
    if not clients:
        return
    payload = json.dumps(obj, ensure_ascii=False)
    dead = []
    for c in list(clients):
        try:
            await c.send(payload)
        except Exception:
            dead.append(c)
    for c in dead:
        clients.discard(c)


def state_msg():
    return {"dir": "state", "area": AREA, "tags": dict(STATE), "ts": time.time()}


def link_msg():
    return {"dir": "link", "mc": len(MC), "web": len(WEB)}


async def notify_link():
    await push(WEB, link_msg())


async def alarm_changed():
    await push(WEB, {"dir": "alarm", "active": active_alarms(), "ts": time.time()})
    log("[ALARM]", [a["code"] for a in active_alarms()] or "全部消除")


async def latch(code, prio, msg, tag):
    if code in LATCH:
        return
    LATCH[code] = {"code": code, "prio": prio, "msg": msg, "tag": tag}
    await alarm_changed()


async def unlatch_by_tag(tag):
    gone = [c for c, v in LATCH.items() if v.get("tag") == tag]
    if not gone:
        return
    for c in gone:
        LATCH.pop(c, None)
    await alarm_changed()


async def set_tag(tag, value, src):
    """写一个标签。返回是否真的变了。"""
    td = TAGS.get(tag)
    if td is None:
        return False
    v = coerce(td, value)
    if v is None:
        return False
    changed = (tag not in STATE) or STATE[tag] != v
    STATE[tag] = v
    if not changed:
        return False
    TS[tag] = time.time()
    hist(tag, v)
    await push(WEB, {"dir": "state", "tags": {tag: v}, "ts": TS[tag]})
    if v is True:                       # 库存恢复了，相关联锁自动复位
        await unlatch_by_tag(tag)
    return True


async def bump(counter_key, n=1):
    tag = COUNTERS.get(counter_key)
    if not tag:
        return None
    cur = STATE.get(tag)
    if cur is None:
        return None
    await set_tag(tag, int(cur) + n, "bridge")
    return int(cur) + n


# ---------------------------------------------------------------- 下单（外设路线）

async def place_order(ws, msg):
    """把一张订单发给游戏里的电脑，由它调 Create 外设的 setRequest/request。"""
    items = []
    for it in msg.get("items") or []:
        name = (it.get("name") or "").strip()
        if not name:
            continue
        try:
            n = int(it.get("count") or 1)
        except (TypeError, ValueError):
            n = 1
        items.append({"name": name, "count": max(1, min(256, n))})   # 源码里单条上限 256
    if not items:
        await ws.send(json.dumps({"dir": "warn", "msg": "订单为空"},
                                 ensure_ascii=False))
        return
    if len(items) > 9:                                                # 请求器只有 9 格
        items = items[:9]
    address = (msg.get("address") or "").strip()

    if not MC:
        await ws.send(json.dumps(
            {"dir": "warn", "msg": "CC 未连接，订单没有发出去"}, ensure_ascii=False))
        record_order(",".join(i["name"] for i in items), address, 0, "电脑离线")
        return

    short = ", ".join(f"{i['name']}×{i['count']}" for i in items)
    missing = [i["name"] for i in items
               if i["name"] in STOCK and STOCK[i["name"]] < i["count"]]

    await push(MC, {"dir": "to-mc", "kind": "order", "items": items, "address": address})
    await bump("requests")
    if missing:
        await bump("rejects")
    log(f"[ORDER] {short} -> {address or '(默认地址)'}")
    record_order(short, address, 0 if missing else 1, "库存不足" if missing else "")

    if missing:
        await latch("ALM-STOCK", "P2", "订单物品库存不足: " + ", ".join(missing), None)


# ---------------------------------------------------------------- 下单：一次请求 = 一个红石脉冲

async def fire_request(ws, td):
    """把一次下单翻译成一次红石脉冲，并记录结果。"""
    topic = td["id"]
    ms = int(td.get("pulse_ms", 150))
    watch = td.get("watch")

    await push(MC, {"dir": "to-mc", "topic": topic, "type": "pulse", "value": True})
    await asyncio.sleep(ms / 1000.0)
    await push(MC, {"dir": "to-mc", "topic": topic, "type": "pulse", "value": False})

    if not MC:
        # 没接电脑就地生效，方便纯软件自测；接了 CC 一律以回执为准
        await set_tag(topic, True, "local")
        await asyncio.sleep(ms / 1000.0)
        await set_tag(topic, False, "local")
        await ws.send(json.dumps({"dir": "warn", "msg": "CC 未连接，脉冲在本地仿真"},
                                 ensure_ascii=False))

    await bump("requests")
    log(f"[ORDER] {topic} 发请求 ({len(MC)} 路 CC)")

    if watch:
        stock = STATE.get(watch)
        if stock is False:
            await bump("rejects")
            await latch("ALM-STOCK-" + watch.replace("%", ""),
                        "P2", f"{td['desc']} 已发出，但{TAGS[watch]['desc']}不足",
                        watch)
            record_order(td["desc"], "", False, "库存位为上阈值以下")
            return False
    record_order(td["desc"], "", True, "")
    return True


# ---------------------------------------------------------------- CC 电脑端

async def mc_session(ws):
    # 只认最新一路。CC 断线重连时旧连接常常还挂在服务端，
    # 两路同时下发输出会互相打架，所以新来的顶掉旧的。
    for old in list(MC):
        log("[MC] 顶掉旧连接")
        try:
            await old.close()
        except Exception:
            pass
        MC.discard(old)
    MC.add(ws)
    log(f"[MC] 接入 ({len(MC)} 路)  {ws.remote_address}")
    # 先发配置：Lua 端拿到 tag -> 侧面的映射，之后就是纯通用驱动，不用为每台机器改代码
    await ws.send(json.dumps({
        "dir": "cfg", "area": AREA, "protocol": 1,
        "tags": [{"id": t["id"], "side": t.get("side"), "io": t["io"], "type": t.get("type")}
                 for t in CFG["tags"]],
    }, ensure_ascii=False))
    await ws.send(json.dumps({"dir": "sync", "values": dict(STATE)}, ensure_ascii=False))
    await notify_link()
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log("[MC] 非 JSON:", raw[:80])
                continue
            if msg.get("dir") in ("to-nr", "state"):
                topic = msg.get("topic")
                if topic:
                    await set_tag(topic, msg.get("value"), "mc")
            elif msg.get("dir") == "stock":
                STOCK.clear()
                for it in msg.get("items") or []:
                    name = it.get("name")
                    if name and name != "minecraft:air":
                        STOCK[name] = int(it.get("count") or 0)
                await push(WEB, {"dir": "stock", "items": STOCK, "ts": time.time()})
            elif msg.get("dir") == "caps":
                CAPS.clear()
                CAPS.update({k: bool(v) for k, v in (msg.get("caps") or {}).items()})
                log("[CAPS]", CAPS or "无")
                await push(WEB, {"dir": "caps", "caps": CAPS})
            elif msg.get("dir") == "order_result":
                ok = bool(msg.get("ok"))
                log("[ORDER]", ("成功 " if ok else "失败 ") + str(msg.get("msg") or ""))
                await push(WEB, {"dir": "result", "ok": ok,
                                 "msg": str(msg.get("msg") or "")})
    except Exception as e:
        log("[MC] 断开:", type(e).__name__)
    finally:
        MC.discard(ws)
        log(f"[MC] 离开 ({len(MC)} 路)")
        await notify_link()


# ---------------------------------------------------------------- 浏览器端

async def web_session(ws):
    WEB.add(ws)
    log(f"[WEB] 打开 {ws.remote_address}")
    await ws.send(json.dumps(state_msg(), ensure_ascii=False))
    await ws.send(json.dumps({"dir": "alarm", "active": active_alarms(), "ts": time.time()},
                             ensure_ascii=False))
    await ws.send(json.dumps({"dir": "meta", "area": AREA,
                              "tags": CFG["tags"], "alarms": CFG.get("alarms", [])},
                             ensure_ascii=False))
    await ws.send(json.dumps({"dir": "stock", "items": STOCK, "ts": time.time()},
                             ensure_ascii=False))
    await ws.send(json.dumps({"dir": "caps", "caps": CAPS}, ensure_ascii=False))
    await notify_link()
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("dir") == "order":
                await place_order(ws, msg)
                continue
            if msg.get("dir") != "cmd":
                continue
            td = TAGS.get(msg.get("topic"))
            if td is None:
                continue
            if td["io"] == "output":
                if td.get("type") == "pulse":
                    await fire_request(ws, td)
                else:
                    await push(MC, {"dir": "to-mc", "topic": td["id"],
                                    "type": td["type"], "value": msg.get("value")})
                    if not MC:
                        await set_tag(td["id"], msg.get("value"), "local")
            else:
                await set_tag(td["id"], msg.get("value"), "web")
    except Exception as e:
        log("[WEB] 断开:", type(e).__name__)
    finally:
        WEB.discard(ws)
        await notify_link()


async def handler(ws):
    path = ""
    try:
        path = ws.request.path
    except Exception:
        pass
    if "mc" in path:
        await mc_session(ws)
    else:
        await web_session(ws)


# ---------------------------------------------------------------- 静态页面 / REST

class Handler(SimpleHTTPRequestHandler):
    # 只放行 web/ 和 cc/：顺便让游戏里的电脑能 wget 驱动脚本下来，
    # 省得手打一百行 Lua。别的目录（比如 data/ 的流水库）不对外。
    ALLOW = ("/web/", "/cc/")

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def translate_path(self, path):
        if path.split("?")[0] in ("/", "/index.html"):
            path = "/web/index.html"
        return super().translate_path(path)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p.startswith("/api/state"):
            self._json({"state": STATE, "ts": TS, "alarms": active_alarms(), "mc": len(MC)})
            return
        if p.startswith("/api/stock"):
            self._json({"items": STOCK, "caps": CAPS, "mc": len(MC)})
            return
        if p.startswith("/api/orders"):
            rows = DB.execute("SELECT ts, items, address, ok, note FROM orders "
                              "ORDER BY ts DESC LIMIT 100").fetchall()
            self._json(rows)
            return
        if p.startswith("/api/hist"):
            rows = DB.execute(
                "SELECT ts, tag, val FROM hist ORDER BY ts DESC LIMIT 300").fetchall()
            self._json(rows)
            return
        if p not in ("/", "/index.html") and not p.startswith(self.ALLOW):
            self.send_error(404)
            return
        super().do_GET()

    def _json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):    # 别让静态请求把控制台淹了
        pass


def start_http():
    srv = ThreadingHTTPServer((HOST, HTTP_PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log(f"[HTTP] http://{HOST}:{HTTP_PORT}")


# ---------------------------------------------------------------- main

async def main():
    init_db()
    for t in CFG["tags"]:
        if "init" in t:
            STATE[t["id"]] = coerce(t, t["init"])
    start_http()
    # ping_interval=None：CC:Tweaked 的 websocket 不保证回 pong，
    # 开着心跳的话 30 秒就会被服务端当成死连接踢掉，那就成玄学掉线了。
    async with serve(handler, HOST, WS_PORT, ping_interval=None):
        log(f"[WS]   ws://{HOST}:{WS_PORT}   (CC 连 /ws/mc，浏览器连 /ws/web)")
        log(f"[CFG]  {AREA} / {len(TAGS)} 个标签 / "
            f"{sum(1 for t in CFG['tags'] if t.get('type') == 'pulse')} 个请求通道")
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("收工")
