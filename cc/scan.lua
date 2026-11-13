--[[
  产线普查 scan.lua —— 让电脑自己交代它能碰到什么

  跑法：先接 bridge，然后在电脑里执行
      wget http://192.168.0.3:8080/cc/scan.lua scan
      scan
  或者直接把这文件放进去跑。

  它做三件事：
    1. 列出所有外设（名字、类型、贴在哪个面）
    2. 对仓储发报机这类有库存的，数一下库存里有多少种物品
    3. 把电脑自己的六面红石输入电平一起报上去

  结果不用你抄，直接发到 bridge 的 /api/scan，也存进 data/scan.json。
  我拿这个就能生成 tags.json 和接线表。
]]

local BRIDGE = "ws://192.168.0.3:8765/ws/mc"   -- ← 改成跑 bridge.py 那台机器的 IP
local SIDES = { "top", "bottom", "left", "right", "front", "back" }

local function shortType(t)
  if type(t) == "table" then return table.concat(t, ",") end
  return tostring(t)
end

local function countKeys(tbl)
  local n = 0
  for _ in pairs(tbl or {}) do n = n + 1 end
  return n
end

local function surveyPeripheral(name)
  local entry = { name = name, type = shortType(peripheral.getType(name)) }

  -- 外设挂在哪个面（peripheral.getNames 给的是 "left" 这种名字时，面就是它自己）
  for _, s in ipairs(SIDES) do
    if name == s then entry.side = s end
  end

  -- 仓储发报机：顺手数一下库存种类
  if peripheral.hasType(name, "Create_StockTicker") then
    local ok, stock = pcall(peripheral.call, name, "stock")
    if ok and type(stock) == "table" then
      entry.stockKinds = countKeys(stock)
      local sample = {}
      for _, d in pairs(stock) do
        if d.name and d.name ~= "minecraft:air" and #sample < 5 then
          sample[#sample + 1] = d.name .. "×" .. tostring(d.count or 0)
        end
      end
      entry.stockSample = sample
    else
      entry.stockError = tostring(stock)
    end
  end

  -- 红石请求器：读一下它当前配的请求和地址
  if peripheral.hasType(name, "Create_RedstoneRequester") then
    local ok, addr = pcall(peripheral.call, name, "getAddress")
    entry.address = ok and tostring(addr) or nil
    local ok2, cfg = pcall(peripheral.call, name, "getConfiguration")
    entry.configuration = ok2 and tostring(cfg) or nil
    local ok3, req = pcall(peripheral.call, name, "getRequest")
    if ok3 and type(req) == "table" then
      local items = {}
      for _, d in pairs(req) do
        if d.name and d.name ~= "minecraft:air" then
          items[#items + 1] = d.name .. "×" .. tostring(d.count or 0)
        end
      end
      entry.request = items
    end
  end

  return entry
end

local function redstoneState()
  local st = {}
  for _, s in ipairs(SIDES) do
    local ok, v = pcall(redstone.getInput, s)
    st[s] = ok and v or nil
  end
  return st
end

local function main()
  local payload = {
    dir = "scan",
    computer = os.getComputerLabel() or ("#" .. tostring(os.getComputerID())),
    free = fs.getFreeSpace and fs.getFreeSpace("/") or nil,
    peripherals = {},
    redstone = redstoneState(),
  }
  for _, name in ipairs(peripheral.getNames()) do
    payload.peripherals[#payload.peripherals + 1] = surveyPeripheral(name)
  end

  print(("[scan] 电脑 %s，外设 %d 个"):format(payload.computer, #payload.peripherals))
  for _, e in ipairs(payload.peripherals) do
    print(("  %-10s %-32s %s"):format(e.name, e.type,
      e.stockKinds and ("库存 " .. e.stockKinds .. " 种") or
      (e.address and ("地址 " .. e.address) or "")))
  end

  local ok, ws = pcall(http.websocket, BRIDGE)
  if not ok then
    print("连不上 bridge：" .. BRIDGE)
    print("上面这些可以先抄给我")
    return
  end
  ws.send(textutils.serialiseJSON(payload))
  -- 等一下回执，让 bridge 记完再断
  local reply = ws.receive(3)
  if reply then print("bridge: " .. tostring(reply)) end
  ws.close()
  print("已上报")
end

main()
