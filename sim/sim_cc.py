#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
假 CC 电脑 —— 没进游戏也能把整条链路跑通 / 排错。

行为跟 cc/client.lua 一模一样：连上 -> 收 cfg/sync -> 循环上报输入、接收输出。
只是把 redstone 换成了内存里的假电平，另外会随机拨动两个输入来模拟有人踩按钮。

    python3 sim/sim_cc.py            # 默认连本机 8765
    python3 sim/sim_cc.py ws://192.168.0.3:8765
"""

import asyncio
import json
import random
import sys
import time

from websockets.asyncio.client import connect

URL = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8765/ws/mc"
if not URL.endswith("/ws/mc"):
    URL = URL.rstrip("/") + "/ws/mc"

out_side, in_side = {}, {}
coils = {}        # 假红石输出
last_in = {}
cfg_ready = asyncio.Event()


def ts():
    return time.strftime("%H:%M:%S")


async def sender(ws):
    while True:
        await asyncio.sleep(0.05)
        if not cfg_ready.is_set():
            continue
        try:
            for topic, side in in_side.items():
                v = coils.get("in:" + side, False)
                if last_in.get(topic) is not v:
                    last_in[topic] = v
                    await ws.send(json.dumps({"dir": "to-nr", "topic": topic,
                                              "type": "bool", "value": v}))
        except Exception:
            return


async def flicker():
    """模拟操作员动作：每 6~14 秒拨一下启动/停止/炉门。"""
    await cfg_ready.wait()
    while True:
        await asyncio.sleep(random.uniform(6, 14))
        if not in_side:
            continue
        topic = random.choice(list(in_side))
        side = in_side[topic]
        key = "in:" + side
        coils[key] = not coils.get(key, False)
        print(f"{ts()} [模拟] 拨动 {topic} ({side}) -> {coils[key]}")


async def main():
    while True:
        try:
            async with connect(URL) as ws:
                print(f"{ts()} 已连接 {URL}")
                asyncio.create_task(sender(ws))
                asyncio.create_task(flicker())
                async for raw in ws:
                    msg = json.loads(raw)
                    d = msg.get("dir")
                    if d == "cfg":
                        for t in msg.get("tags", []):
                            if t.get("io") == "output":
                                out_side[t["id"]] = t["side"]
                            elif t.get("io") == "input":
                                in_side[t["id"]] = t["side"]
                        cfg_ready.set()
                        print(f"{ts()} 配置: {len(out_side)} 出 / {len(in_side)} 入")
                        # 跟真 CC 一样，开机把当前红石电平报上去
                        for topic, side in out_side.items():
                            await ws.send(json.dumps({"dir": "to-nr", "topic": topic,
                                                      "type": "bool",
                                                      "value": bool(coils.get("out:" + side, False))}))
                    elif d == "sync":
                        for topic, v in (msg.get("values") or {}).items():
                            if topic in out_side:
                                coils["out:" + out_side[topic]] = bool(v)
                                print(f"{ts()} 同步输出 {topic} -> {bool(v)}")
                    elif d == "to-mc":
                        topic = msg.get("topic")
                        v = bool(msg.get("value"))
                        if topic in out_side:
                            coils["out:" + out_side[topic]] = v
                            print(f"{ts()} 施加输出 {topic} ({out_side[topic]}) -> {v}")
                            await ws.send(json.dumps({"dir": "to-nr", "topic": topic,
                                                      "type": "bool", "value": v}))
        except (OSError, asyncio.TimeoutError) as e:
            print(f"{ts()} 断了: {type(e).__name__}，2 秒后重连")
        except Exception as e:
            print(f"{ts()} 异常: {type(e).__name__}: {e}")
        await asyncio.sleep(2)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("收工")
