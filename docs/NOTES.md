# NOTES —— 硬事实与坑（改之前先读）

## 环境

| 项 | 值 |
|---|---|
| 控制机 | Linux `192.168.0.3`，Ubuntu 26.04 |
| 游戏机 | Windows `192.168.0.8`（DESKTOP-KBUN4TV，Endministrator），服务端目录 `F:\ES2Server` |
| 服务端 | Arclight NeoForge 1.21.1，RCON 25328，game port 25325，level `world_machine` |
| 本机 Python | 3.14.4，自带 `websockets 15.0.1`、`sqlite3`；**没有 pip** |
| npm | `registry.npmjs.org` **不通**，一律加 `--registry=https://registry.npmmirror.com` |
| gh | 2.46.0（apt），已登录 M1ynar325，**HTTPS + token**；SSH 公钥没注册，别用 `git@github.com:` |
| 端口 | bridge `8765`(ws) / `8080`(http)；CC 电脑连 `ws://192.168.0.3:8765` |

## Create 6.0.11 的 CC 外设（查源码确认，不是猜的）

源码：`/data/KimiWorks/Create/src/main/java/com/simibubi/create/compat/computercraft/`

- `Create_StockTicker`（仓储发报机）：`stock([detailed])`、`getStockItemDetail(slot)`、
  `requestFiltered(address, filter…)`、`list()`、`getItemDetail(slot)`
- `Create_RedstoneRequester`（红石请求器）：`setRequest({name=…,count=…}, …)`（**≤9 格，单格 ≤256**）、
  `request()`、`setAddress()`/`getAddress()`、`setConfiguration("strict"|"allow_partial")`
- 还有 `Create_Packager` / `Frogport` / `Postbox` / `Repackager` / `TableClothShop` /
  `SpeedGauge` / `StressGauge` / `CreativeMotor` / `SequencedGearshift` 等
- 红石请求器**只在信号边沿**发一次请求（`onRedstonePowerChanged`）；仓储发报机**没有红石输入**

结论：**不用装 cc:cbridge，也不用把物品写死在方块 GUI 里**。之前"CC 读不到转速/库存"的判断是错的。

## 已确认（CC 源码 + Create 源码）

1. **`requestFiltered` 的参数索引**：CC 的 `core/asm/Generator.java` 里 `IArguments`
   是 identity 原样传入，前面的类型化参数不会裁表。所以 `requestFiltered(address, f1, f2)`
   里 `filters[0]` 是地址，**真过滤器从索引 1 开始** —— Create 那句 `for (i = 1; …)` 是对的
2. **过滤器匹配模式**：`ComputerUtil.MatchMode.parse` 缺省是 `CONTAINS`，
   所以 `{name="mod:item", _requestCount=n}` 就够，不必写全物品详情
3. 红石请求器 `setRequest` 单格 `count` 上限 256、最多 9 格

## 仍待真机验证

1. 真机 `requestFiltered` / `setRequest` 的实际效果（本机只验到假 CC 层）
2. 送达无法确认：`request()` 只代表 Create 接受了请求

## 工程约定

- 分支：`demo` 是工作分支，`main` 保持空（只有 LICENSE）
- 推送：HTTPS（gh credential helper），**不要**改成 SSH
- 记忆：`~/.mnemon/runtime/*` 由 mnemon 插件管，**不要手改**那些文件
- 状态栏、订单流水 DB 都在 `data/`，已 gitignore
