--[[
  PCS for Create —— CC:Tweaked 端驱动
  一行都不用改逻辑：连上服务端先收一张 tag 表，之后就是通用扫描 + 通用下发。
  想换机器？改 tags.json，不碰这个文件。

  用法：把这个文件放到电脑里，执行 client
  （文件名必须是 client.lua 放在根目录，或者 run client.lua 也行）
]]

local BRIDGE = "ws://192.168.0.3:8765"   -- ← 改成跑 bridge.py 那台机器的 IP

local ws            -- 当前连接
local outSide = {}  -- %Q0.0 -> "right"
local inSide  = {}  -- %I0.0 -> "back"
local lastIn  = {}  -- 上一次上报过的输入值，只在变化时发，别刷屏
local started = os.clock()

local function say(s)
  print("[PCS] " .. s)
end

local function send(obj)
  if not ws then return false end
  local ok, err = pcall(ws.send, textutils.serialiseJSON(obj))
  if not ok then
    say("发送失败: " .. tostring(err))
    return false
  end
  return true
end

local function applyOutput(topic, value)
  local side = outSide[topic]
  if not side then return end
  redstone.setOutput(side, value and true or false)
  -- 回执：HMI 上显示的是真实施加到红石上的电平，不是"我以为我下发了"
  send({ dir = "to-nr", topic = topic, type = "bool", value = value and true or false })
end

local function handle(raw)
  local ok, msg = pcall(textutils.unserialiseJSON, raw)
  if not ok or type(msg) ~= "table" then return end

  if msg.dir == "cfg" then
    outSide, inSide, lastIn = {}, {}, {}
    local no, ni = 0, 0
    for _, t in ipairs(msg.tags or {}) do
      if t.side then
        if t.io == "output" then
          outSide[t.id] = t.side
          no = no + 1
        elseif t.io == "input" then
          inSide[t.id] = t.side
          ni = ni + 1
        end
      end
    end
    say(("配置送达: %d 出 / %d 入"):format(no, ni))
    -- 开机就把真实红石电平报上去：HMI 显示的是现场，不是"我以为"的初值
    for topic, side in pairs(outSide) do
      local v = redstone.getOutput(side)
      send({ dir = "to-nr", topic = topic, type = "bool", value = v })
    end
  elseif msg.dir == "sync" then
    for topic, v in pairs(msg.values or {}) do
      if outSide[topic] then applyOutput(topic, v) end
    end
    say("已同步初始状态")
  elseif msg.dir == "to-mc" then
    applyOutput(msg.topic, msg.value)
  end
end

local function scanInputs()
  for topic, side in pairs(inSide) do
    local v = redstone.getInput(side)
    if lastIn[topic] ~= v then
      lastIn[topic] = v
      send({ dir = "to-nr", topic = topic, type = "bool", value = v })
    end
  end
end

local function pump()
  local msg, err = ws.receive(0.05)   -- 短暂阻塞，够扫一轮输入
  if msg then
    handle(msg)
    return true
  end
  if err == "closed" then return false end
  return true                          -- "timeout" 很正常
end

local function loop()
  while true do
    scanInputs()
    if not pump() then return end
  end
end

local function main()
  while true do
    local ok, sock = pcall(http.websocket, BRIDGE)
    if not ok then
      say("连不上 " .. BRIDGE .. " ——3 秒后重试")
      os.sleep(3)
    else
      ws = sock
      started = os.clock()
      say("已连接 " .. BRIDGE)
      loop()
      ws = nil
      say("连接断开，重连中")
      os.sleep(2)
    end
  end
end

main()
