--[[
  PCS for Create —— 外设版驱动（推荐）
  
  跟 client.lua 的区别：这个不靠红石，直接把 Create 6.0.11 自带的外设 API
  接过来用。Create 从 6.0 起就给物流方块加了 CC:Tweaked 外设，
  源码位置：compat/computercraft/implementation/peripherals/

    仓储发报机 Create_StockTicker
      stock()                      -> { {name=..., count=...}, ... }   全网库存
      requestFiltered(地址, 过滤器...)  -> 请求匹配的物品数
      list() / getItemDetail(slot)     收货款（商店用）

    红石请求器 Create_RedstoneRequester
      setRequest({name=..., count=...}, ...)   最多 9 格，单格上限 256
      request()                                发出去
      setAddress(地址) / getAddress()
      setConfiguration("strict" | "allow_partial")

  用法：放电脑里，执行 order
]]

local BRIDGE = "ws://192.168.0.3:8765/ws/mc"   -- ← 改 IP；/ws/mc 不能少，不然会被当成浏览器
local STOCK_EVERY = 2.0                  -- 库存快照周期（秒）

local ws
local ticker, req
local unpack = table.unpack or unpack
local lastStock = 0

local function say(s)
  print("[PCS] " .. s)
end

local function send(obj)
  if not ws then return false end
  local ok = pcall(ws.send, textutils.serialiseJSON(obj))
  return ok
end

local function findPeriphs()
  ticker = peripheral.find("Create_StockTicker")
  req = peripheral.find("Create_RedstoneRequester")
  local caps = { ticker = ticker ~= nil, requester = req ~= nil }
  say(("peri: ticker=%s requester=%s")
    :format(tostring(caps.ticker), tostring(caps.requester)))
  send({ dir = "caps", caps = caps })
end

local function reportStock()
  if not ticker then return end
  local ok, stock = pcall(ticker.stock)
  if not ok then
    say("stock read failed: " .. tostring(stock))
    return
  end
  local items = {}
  for _, d in pairs(stock) do
    if d.name and d.name ~= "minecraft:air" then
      items[#items + 1] = { name = d.name, count = d.count or 0 }
    end
  end
  send({ dir = "stock", items = items })
  say(("stock: %d kinds"):format(#items))
end

local function execOrder(msg)
  local items = msg.items or {}
  local address = msg.address or ""

  if req then
    -- 请求器路线：运行时把物品写进去，再触发一次
    req.setAddress(address)
    pcall(req.setConfiguration, "allow_partial")
    local args = {}
    for i, it in ipairs(items) do
      if i > 9 then break end
      args[i] = { name = it.name, count = math.min(it.count or 1, 256) }
    end
    local ok, err = pcall(req.setRequest, unpack(args))
    if not ok then
      say("setRequest failed: " .. tostring(err))
      send({ dir = "order_result", ok = false, msg = tostring(err) })
      return
    end
    local ok2, err2 = pcall(req.request)
    if ok2 then
      say(("order sent: %d slots -> %s"):format(#args, address ~= "" and address or "(default)"))
      send({ dir = "order_result", ok = true, msg = ("%d 格"):format(#args) })
    else
      say("request failed: " .. tostring(err2))
      send({ dir = "order_result", ok = false, msg = tostring(err2) })
    end
    return
  end

  if ticker then
    -- 发报机路线：按过滤器下单，库里有的才发。
    --
    -- 参数索引已从 CC 源码确认（projects/core/src/main/java/dan200/computercraft/core/
    -- asm/Generator.java）：IArguments 是原样拿到整张参数表，前面的类型化参数
    -- 不会把它裁掉。所以 requestFiltered(address, f1, f2) 里 filters[0] 是地址，
    -- 真过滤器从下标 1 开始 —— Create 那句 `for (i = 1; i < count; i++)` 是对的。
    -- 过滤器默认 _mode = "contains"（ComputerUtil.MatchMode.parse），
    -- 所以只写 name + _requestCount 就能匹配，不用写全物品详情。
    local args = {}
    for i, it in ipairs(items) do
      args[i] = { name = it.name, _requestCount = it.count or 1 }
    end
    local ok, res = pcall(ticker.requestFiltered, address, unpack(args))
    if ok then
      say(("filtered order -> %d items"):format(tonumber(res) or 0))
      send({ dir = "order_result", ok = (tonumber(res) or 0) > 0,
             msg = ("%d 个"):format(tonumber(res) or 0) })
    else
      say("filtered order failed: " .. tostring(res))
      send({ dir = "order_result", ok = false, msg = tostring(res) })
    end
    return
  end

  say("no order peripheral")
  send({ dir = "order_result", ok = false, msg = "没有下单外设" })
end

local function handle(raw)
  local ok, msg = pcall(textutils.unserialiseJSON, raw)
  if not ok or type(msg) ~= "table" then return end
  if msg.dir == "to-mc" then
    if msg.kind == "order" then
      execOrder(msg)
    end
  elseif msg.dir == "cfg" then
    say("cfg ok")
  end
end

local function loop()
  while true do
    local msg, err = ws.receive(0.1)
    if msg then
      handle(msg)
    elseif err == "closed" then
      return
    end
    if os.clock() - lastStock >= STOCK_EVERY then
      lastStock = os.clock()
      reportStock()
    end
  end
end

local function main()
  while true do
    local ok, sock = pcall(http.websocket, BRIDGE)
    if not ok then
      say("cannot reach " .. BRIDGE .. ", retry in 3s")
      os.sleep(3)
    else
      ws = sock
      say("connected " .. BRIDGE)
      findPeriphs()
      reportStock()
      lastStock = os.clock()
      loop()
      ws = nil
      say("link down, reconnecting")
      os.sleep(2)
    end
  end
end

main()
