# PCS4C IO Server —— 暴露 Create 及其附属 Mod 接口的 NeoForge 模组

目标：把 Create 6 和其附属 mod 的机器状态、库存、流体、应力、转速等，通过标准协议暴露给外部（ESP32 / PLC / 上位机）。

## 0. 现状调研（别重复造轮子）

| 项目 | 能做什么 | 不满足的地方 |
|---|---|---|
| [CC:C Bridge](https://github.com/cccbridge/cccbridge) | 把 Create 组件暴露给 CC:Tweaked 外设 | 只给 CC，外部设备用不了 |
| [Create: WebCode](https://modrinth.com/mod/createwebcode) | 游戏内发 HTTP GET、解析 JSON、驱动红石 | **反方向**：游戏去读网，不是把游戏数据发出去 |
| [S7-1500 OPC UA → RCON](https://industrialmonitordirect.com/zh-hans/blogs/knowledgebase/s7-1500-opc-ua-rcon-bridge-for-minecraft-process-simulation) | 真 PLC 经 OPC UA + RCON 控制 Minecraft | 只做通断，读不到 Create 数据 |
| [Create-Tweaked-Controllers](https://github.com/getItemFromBlock/Create-Tweaked-Controllers) | 高级控制器控 contraption | 游戏内玩法，非外部接口 |

结论：**没有现成的**，需要自己写一个。

## 1. 架构

```
外部客户端（ESP32 / PLC / 上位机）
   │  Modbus TCP / HTTP / MQTT
   ▼
[IO Server]  ── 运行在服务器进程里的 TCP/HTTP 监听线程
   │  (只读快照 / 写队列)
   ▼
[Snapshot Cache] ── 每 tick（20Hz）由主线程刷新，网络线程只读
   │
   ▼
[Adapter Registry] ── 按方块位置找到适配器，读 Create / Capability 数据
   │
   ▼
Minecraft 世界（Create 机器 + 附属 mod）
```

**线程模型是命门**：网络线程绝不直接碰世界，只读快照；写命令进队列，主线程下个 tick 应用。

## 2. 暴露的数据（适配器）

| 适配器 | 来源 | 字段 |
|---|---|---|
| Kinetic | `KineticBlockEntity` | 转速（RPM）、应力占用、应力上限、超载 |
| Item | `IItemHandler` capability | 每格物品 id、数量 |
| Fluid | `IFluidHandler` capability | 流体 id、mB |
| Energy | `IEnergyStorage` capability | FE |
| Redstone | 世界红石 | 输入/输出等级 0–15 |
| Addon | 附属 mod 自定义 BE | 走适配器注册表，v1 先留接口 |

**通用适配器覆盖大部分附属**：Create 的附属大多继承 `KineticBlockEntity` 并用 Forge Capability，所以 Kinetic/Item/Fluid 三个适配器能吃下大头；特殊附属再单独写。

## 3. 寻址

配置驱动，不改客户端：

```json
{
  "devices": [
    { "name": "Line1.Mixer", "pos": [120, 64, -330], "type": "kinetic" },
    { "name": "Line1.Chest", "pos": [121, 64, -330], "type": "inventory" }
  ]
}
```

地址规则（Modbus 兼容）：
- 40001–40999：保持寄存器（只读数值，转速 ×100 存 INT）
- 10001–10999：输入寄存器
- 00001–00999：线圈（写，虚拟红石输出，v2）
- 每个设备按类型分固定偏移，`/api/v1/map` 返回 JSON 地址表

**v1 不新增方块**，所以**客户端不用装 mod**——这对服主是最大的卖点。

## 4. 协议

| 协议 | 端口 | v1 | 说明 |
|---|---|---|---|
| HTTP JSON | 8080 | ✅ | GET /api/v1/devices，GET /api/v1/values，GET /api/v1/map |
| Modbus TCP | 5020 | ✅ | 读写寄存器，PLC 直接用 |
| MQTT | — | v2 | 发布 state/alarm，接三色灯 |

## 5. 安全

- 默认绑定 `127.0.0.1`，配置里显式开 LAN
- 写操作默认关闭，白名单 + token
- 速率限制：读 20 Hz，写 10 条/秒

## 6. v1 范围（建议）

- 只读：Kinetic / Item / Fluid / Energy / Redstone
- HTTP + Modbus TCP
- 配置驱动，无自定义方块
- 不做：写入方块数据、contraption 内部、附属 mod 特殊逻辑

## 7. 风险

- Create 版本升级会改内部类 → 适配器要跟着改
- Arclight 混合端：NeoForge mod 正常加载，但和 Bukkit 插件的混用要注意线程/事件顺序
- 快照频率与 TPS：设备多时快照开销要压，按 tick 分片刷新
- Modbus 端口 502 在 Linux 上要 root，用 5020 避开

## 8. 后续

- v2：虚拟 I/O 方块（需客户端装 mod）、MQTT、报警
- v3：contraption 数据、附属 mod 适配器
