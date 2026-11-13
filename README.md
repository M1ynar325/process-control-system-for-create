# PCS for Create · demo v2（外设下单）

浏览器下单 → 本机 Python → CC:Tweaked 电脑 → **Create 自带外设** → 物流网络发货。

```
┌──────────────┐  ws://  ┌──────────────┐  ws://  ┌───────────────┐  外设调用  ┌──────────────────┐
│ 浏览器 / MES │ ──────▶ │  bridge.py   │ ──────▶ │ CC:Tweaked    │ ────────▶ │ 仓储发报机/请求器 │
│ 下单+库存看板 │ ◀────── │ 订单/流水/报警│ ◀────── │ order.lua     │ ◀──────── │ → Create 物流网络 │
└──────────────┘         └──────────────┘         └───────────────┘            └──────────────────┘
```

## 关键事实（查的是本地 Create 6.0.11 源码，不是猜的）

`src/main/java/com/simibubi/create/compat/computercraft/implementation/peripherals/`

Create 从 6.0 起就给物流方块加了 CC:Tweaked 外设，**不用装任何额外模组**：

| 外设类型名 | 方块 | 常用 Lua 方法 |
|---|---|---|
| `Create_StockTicker` | 仓储发报机 | `stock()` `getStockItemDetail(slot)` `requestFiltered(地址, 过滤器…)` `list()` `getItemDetail(slot)` |
| `Create_RedstoneRequester` | 红石请求器 | `setRequest({name=…,count=…}, …)` `request()` `setAddress()` `setConfiguration("strict"/"allow_partial")` `getRequest()` |
| `Create_Packager` | 打包机 | 见源码 |
| `Create_Frogport` / `Create_Postbox` | 蛙港 / 邮箱 | 收发包裹 |
| `Create_Repackager` / `Create_TableClothShop` | 理包机 / 商店 | — |

细节（都来自源码）：
- `setRequest` 最多 **9 格**，单格 `count` 上限 **256**，物品用 `{name="mod:item", count=n}`，空位自动填空气
- `requestFiltered` 的过滤器是「深度匹配物品详情表」，支持 `_requestCount`、`_op`/`value` 比较、
  以及 `name="iron_ingot"` 这种省略命名空间的写法
- 红石请求器收到**一次**红石信号发**一次**请求（`onRedstonePowerChanged` 只在边沿触发）
- 仓储发报机**没有红石输入**，它只认 GUI 或外设调用 —— 所以走电脑是唯一自动化路径

## 两个驱动，二选一

| 文件 | 路线 | 需要什么 |
|---|---|---|
| `cc/order.lua` | **外设版（推荐）** | 电脑贴着红石请求器或仓储发报机；物品运行时随便换 |
| `cc/client.lua` | 红石版（兜底） | 每个 SKU 一个红石请求器，靠 `tags.json` 的通道脉冲 |

`order.lua` 启动会自己找外设，把结果上报（`仓储发报机=True 红石请求器=True`），
网页右上角那行提示就是它报的。

## 文件

| 文件 | 作用 |
|---|---|
| `bridge.py` | 服务端：ws + 网页 + `/api/state` `/api/stock` `/api/orders` `/api/hist`，订单流水写 `data/history.db` |
| `web/index.html` | HMI：下单面板 + 库存快照 + 通道 + 事件，原生 JS 零依赖 |
| `cc/order.lua` | 外设版驱动：读库存、按订单调 `setRequest`/`request` |
| `cc/client.lua` | 红石版驱动：扫输入、施脉冲 |
| `sim/sim_cc.py` | 假 CC 电脑，没进游戏也能跑通全链路 |
| `tags.json` | 红石通道 + 计数器 + 端口配置 |
| `run.sh` / `stop.sh` | 起 / 停（8765 + 8080） |

## 先自测（不用开游戏）

```bash
bash run.sh                 # 终端 A
python3 sim/sim_cc.py       # 终端 B（红石版假电脑）
# 浏览器 http://127.0.0.1:8080
```

外设版没有现成的假电脑，用 `sim/sim_cc.py` 也能看到链路通；要测「下单」这条路，
把 `order_test.py` 那种假 CC（发 `caps`/`stock`、收 `to-mc` 的 `kind:"order"`）跑起来即可。

## 进游戏

1. 电脑贴着红石请求器或仓储发报机放（同一个有线网络里也行，用 `peripheral.find` 找）
2. 请求器要先右键仓储链接站绑好网络，并在 GUI 里设一次地址（之后 Lua 会 `setAddress` 覆盖）
3. 电脑里：
   ```
   wget http://192.168.0.3:8080/cc/order.lua order
   order
   ```
4. 网页右上角变绿；下单面板会列出全网库存，点芯片自动填物品 id

## 已验证 / 未验证

已验证（本机跑过）：ws 协议、库存快照上报与展示、下单转发、回执、订单流水落库、
库存不足时拒单计数 + `ALM-STOCK` 联锁报警、红石版脉冲+回执、空连接 55 秒不断线。

未验证：**真机外设调用**（`setRequest`/`requestFiltered` 的实际行为），要等你游戏里跑一次。
特别待确认的一点：`requestFiltered(address, …)` 的 `IArguments` 索引方式我是按
`for (i = 1; i < count; i++)` 反推的（第 0 位是地址），真机第一次跑请先试一格。

已知限制：
- 送达无法确认。`request()` 只代表 Create 接了这个请求，包裹到没到得靠别的信号
- 库存是快照，`stock()` 有刷新周期（我们 2 秒拉一次）
- `strict` 模式下缺件整单不发；默认给的是 `allow_partial`
- 这套 API 只在 6.0+ 有；1.20.1 的 Create 6.0 之前版本没有

## 下一步

1. 真机验证外设调用，把 `_requestCount` / 过滤器写法钉死
2. 把「一单」做成多步工序（BOM 展开），bridge 侧派工
3. MQTT 上报给外部系统（缺 `paho-mqtt`，要装 pip）
4. 库存低水位自动补货（现在只是报警）
