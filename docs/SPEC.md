# PCS4C 接口规范 v0.1（草案）

Process Control System for Create —— 用 CC:Tweaked + Create 6 在 Minecraft 里复刻工业过程控制。
语法、地址、数据类型对齐西门子 S7 / TIA Portal SCL，目标是让有西门子杯背景的人零学习成本开工。

## 0. 设计原则

1. **语法抄 S7，语义做子集**：只保证 SCL 常用子集，不追求全兼容；LAD/FBD 图形编程不做。
2. **控制闭环在现场**：L1（游戏内 CC 电脑）独立运行，断网不影响机器。
3. **上云只做监控**：L2/L3 默认只读，写命令要白名单。
4. **一切可版本化**：tag 表、程序、协议都带版本号。

## 1. 分层

| 层 | 位置 | 职责 |
|---|---|---|
| L0 现场 | Create 机器、红石、传感器 | 物理过程 |
| L1 控制 | 游戏内 CC:Tweaked 电脑 | 跑 SCL 程序，扫描周期 50 ms |
| L2 边缘桥 | 主机上的小脚本（Python/Node） | HTTP ↔ MQTT 转换、缓存、鉴权 |
| L3 监控 | ESP32 三色灯 / Web | 状态显示、报警 |

## 2. 地址空间（S7 风格）

### 2.1 区域

| 区域 | 含义 | CC 侧来源 |
|---|---|---|
| `%I` | 数字输入（BOOL） | `redstone.getInput(side)` |
| `%IW` | 模拟输入（INT 0–15） | `redstone.getAnalogInput(side)` |
| `%Q` | 数字输出（BOOL） | `redstone.setOutput(side, v)` |
| `%QW` | 模拟输出（INT 0–15） | `redstone.setAnalogOutput(side, v)` |
| `%M` | 位/字存储 | Lua 变量 |
| `%DB` | 数据块（FB 实例） | Lua table |

### 2.2 符号表（tag 表）

`tags.csv` 每行一个标签：

```
Name,AbsAddr,Type,Default,Source,Comment
Line1.MotorStart,%I0.0,BOOL,FALSE,redstone:left,1号线主电机启动
Line1.MotorRun,%Q0.0,BOOL,FALSE,redstone:right,1号线主电机运行
Line1.TankLevel,%IW64,INT,0,analog:back,储罐液位 0-15
Line1.SetPoint,%DB1.DBD0,REAL,50.0,memory,液位设定值 %
```

规则：
- 符号区分大小写；关键字不区分。
- 地址必须唯一；`%I/%Q/%M/%DB` 不允许重叠。
- 类型：`BOOL / INT / DINT / REAL / TIME / STRING(32)`。
- 未在表中的地址禁止使用（编译期报错）。

## 3. 程序组织

| 块 | 说明 |
|---|---|
| `OB1` | 主循环，默认 50 ms 扫描（可配 20–200 ms） |
| `OB35` | 定时中断 100 ms（可选） |
| `OB100` | 启动块，上电执行一次 |
| `FC` | 无状态函数 |
| `FB` | 带实例数据块的功能块 |
| 标准库 | `TON / TOF / TP / CTU / CTD / R_TRIG / F_TRIG` |

### 3.1 执行模型

- 扫描：`读输入 → 执行 OB1 → 写输出`
- 每个扫描周期只读一次 `%I`，写一次 `%Q`（与 S7 一致）
- 程序超时保护：单次扫描 > 200 ms 报警 `ALM-SYS-002`，不强制停机

### 3.2 部署链路

```
.scl 源码 → 主机侧转译器（Python）→ program.lua → 下发到 CC 电脑 /pcs/
```

转译器首版只支持第 4 节的子集；超集语法直接报错，不做静默降级。

## 4. SCL 子集

### 4.1 语法对照

| 语法 | 支持 |
|---|---|
| `IF ... THEN ... ELSIF ... ELSE ... END_IF;` | ✅ |
| `CASE ... OF ... END_CASE;` | ✅ |
| `FOR ... TO ... BY ... DO ... END_FOR;` | ✅ |
| `WHILE ... DO ... END_WHILE;` | ✅ |
| `REPEAT ... UNTIL ... END_REPEAT;` | ✅ |
| `EXIT; CONTINUE; RETURN;` | ✅ |
| `AND OR XOR NOT`、`= <> < > <= >=`、`+ - * / MOD` | ✅ |
| `:=` 赋值、`:=` 形参赋值 | ✅ |
| `VAR_INPUT / VAR_OUTPUT / VAR_IN_OUT / VAR / VAR_TEMP / VAR_STAT` | ✅ |
| `FUNCTION_BLOCK / END_FUNCTION_BLOCK`、`FUNCTION / END_FUNCTION` | ✅ |
| `TON TOF TP CTU CTD R_TRIG F_TRIG` | ✅ |
| 数组、指针、STRING 运算、UDT、多重实例、SFC/GRAPH、LAD/FBD | ❌ v0.1 不支持 |
| `R_TRIG` 等背景数据块 | 用实例名，如 `Edge1(CLK := ...)` |

### 4.2 字面量

`TRUE / FALSE`、`16#FF`、`2#1010`、`1.5`、`T#2s500ms`、`"符号名"`

### 4.3 示例

```scl
FUNCTION_BLOCK FB_Motor
VAR_INPUT
    Start : BOOL;
    Stop  : BOOL;
END_VAR
VAR_OUTPUT
    Run   : BOOL;
END_VAR
VAR
    T1    : TON;
    StartEdge : R_TRIG;
END_VAR
BEGIN
    StartEdge(CLK := Start);
    T1(IN := StartEdge.Q AND NOT Stop, PT := T#1s500ms);
    Run := T1.Q;
END_FUNCTION_BLOCK

// OB1
Line1.MotorStart := %I0.0;
Line1.MotorStop  := %I0.1;
"FB_Motor_DB"(Start := Line1.MotorStart, Stop := Line1.MotorStop);
%Q0.0 := "FB_Motor_DB".Run;
```

### 4.4 命名约定

- 实例名：`<块名>_DB`，如 `FB_Motor_DB`
- 报警块：`FB_Alarm`
- 区域前缀：`Line1.` `Line2.` `Site.`
- 标签名不用中文，注释用中文

## 5. 运行时 API（Lua 层，转译目标）

转译产物依赖一个固定运行时 `pcs.lua`：

```lua
local pcs = require("pcs")
pcs.tag("Line1.MotorStart", "I", 0, 0, "BOOL")   -- 名字, 区域, 字, 位, 类型
pcs.scan(50)                                      -- ms
pcs.on_startup(function() end)
pcs.on_scan(function()
  -- 转译后的程序体
end)
```

- `pcs.i(side)` / `pcs.q(side, v)` / `pcs.ai(side)` / `pcs.aq(side, v)`：现场 I/O
- `pcs.ton(inst, IN, PT)` / `pcs.ctu(...)`：标准库
- `pcs.alarm(code, prio, msg)`：报警
- 所有 tag 读写走统一表，便于快照上报

## 6. 现场 I/O 映射（Create）

| 信号 | 做法 | 备注 |
|---|---|---|
| DI | 电脑侧面 `redstone.getInput` | 红石信号 / 红石链路 |
| DO | `redstone.setOutput` | 驱动离合器、齿轮、活塞 |
| AI | `redstone.getAnalogInput` 0–15 | 存量开关 / 阈值开关这类模拟红石源 |
| AO | `redstone.setAnalogOutput` 0–15 | 可驱动比较器链；能控的对象有限 |
| 无线 | Create 红石链路 | 远距离 DI/DO，无需拉线 |

**已知限制**：CC:Tweaked 官方没有 Create 外设，拿不到转速、应力、流体精确值。
后续若加桥接 mod（如 CC:C Bridge），可扩 `%IW` 映射，v0.1 不承诺。

## 7. CC ↔ 边缘桥（HTTP）

CC 的 `http` API 不支持 https，需在 `computercraft-server.toml` 配置白名单：

```toml
[http]
enabled = true
whitelist = ["192.168.0.3"]
```

接口：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/state` | 1 Hz 上报快照 |
| GET | `/api/v1/cmd?since=<seq>` | 拉取命令（长轮询 10 s） |
| POST | `/api/v1/alarm` | 报警事件 |
| POST | `/api/v1/heartbeat` | 心跳，15 s |

`state` 载荷：

```json
{
  "server": "es2",
  "area": "line1",
  "ts": 1763031000,
  "seq": 1024,
  "tags": { "Line1.MotorRun": true, "Line1.TankLevel": 7 }
}
```

`cmd` 载荷：

```json
{ "seq": 1025, "cmds": [ { "tag": "Line1.MotorStart", "value": true } ] }
```

- 鉴权：`Authorization: Bearer <token>`，token 在桥的配置文件里
- 命令队列：桥侧维护，CC 断线不丢，重连后按 seq 补发
- 熔断：CC 连不上桥时本地继续跑，缓存最近 60 s 状态

## 8. 边缘桥 ↔ ESP32（MQTT）

| 主题 | 方向 | QoS | 说明 |
|---|---|---|---|
| `pcs/<server>/<area>/state` | ↑ | 1 | 保留最后一条 |
| `pcs/<server>/<area>/alarm` | ↑ | 1 | 不保留 |
| `pcs/<server>/<area>/cmd` | ↓ | 1 | 需白名单 |
| `pcs/<server>/<area>/hb` | ↑ | 0 | 15 s 心跳 + LWT |

`state`：

```json
{ "ts": 1763031000, "seq": 1024, "tags": { "MotorRun": true, "TankLevel": 7 } }
```

`alarm`：

```json
{ "code": "ALM-LINE1-001", "prio": 1, "msg": "储罐液位过低", "ts": 1763031005 }
```

## 9. 报警规范

- 编码：`ALM-<区域>-<3位>`，如 `ALM-LINE1-001`
- 优先级：`P1` 紧急（停机/爆炸风险）、`P2` 警告、`P3` 提示
- 状态机：`发生 → 确认(ACK) → 消失`；P1 未确认前蜂鸣器持续响

三色灯语义：

| 灯 | 含义 |
|---|---|
| 绿常亮 | 正常 |
| 黄慢闪 | P2 警告 |
| 红快闪 | P1 报警（同时蜂鸣） |
| 蓝常亮 | 维护模式 |
| 全灭 | 离线 / 桥断开 |

## 10. 安全边界

- 默认只读：L3 不直接写 `%Q`
- 写命令走白名单 + 速率限制（≤ 10 条/秒）
- 公网只暴露 MQTT 的 state/alarm，控制类主题不出内网
- 断网本地闭环，不允许因网络问题停机

## 11. 版本

- 本文 v0.1 草案，接口冻结前可变
- `tags.csv` 带 `# version=1` 头注释；程序与 tag 表版本绑定，不匹配拒绝启动

## 12. 待定

1. SCL 用转译器（推荐）还是 CC 内解释器
2. 是否引入 CC-Create 桥 mod 以支持模拟量
3. 仓库归属：`M1ynar325/process-control-system-for-create` 还是另开
4. 桥的语言：Python（与现有 RCON 脚本一致）还是 Node
