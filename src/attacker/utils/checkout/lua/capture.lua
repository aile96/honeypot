-- Transparent proxy + request shadowing to prova.dmz
local MAX = tonumber(os.getenv("MAX_BODY_BYTES") or "65536")
local PATH = os.getenv("PATH_LOG") or "/log"
local CLUSTER = os.getenv("SHADOW_ADDR") or "/prova.dmz"

local function esc(s)
  s = tostring(s or "")
  s = s:gsub("\\", "\\\\"):gsub('"', '\\"'):gsub("\n", "\\n"):gsub("\r", "\\r")
  return s
end

local function hget(h, name)
  local ok, val = pcall(function() return h:get(name) end)
  if ok and val then return val else return "" end
end

local function pick_headers(h)
  local keys = { ":authority", ":scheme", ":method", ":path", "content-type", "user-agent" }
  local t = {}
  for _, k in ipairs(keys) do
    local v = hget(h, k)
    if v ~= "" then
      t[#t + 1] = '"' .. esc(k) .. '":"' .. esc(v) .. '"'
    end
  end
  return "{" .. table.concat(t, ",") .. "}"
end

function envoy_on_request(handle)
  local headers = handle:headers()
  local body = ""
  local data = handle:body()

  if data ~= nil then
    local n = math.min(data:length(), MAX)
    body = data:getBytes(0, n) or ""
  end

  local payload = body

  handle:logInfo("Traffic to : " .. CLUSTER)
  handle:logInfo("Shadowing to shadow_cluster: " .. payload)

  handle:httpCall(
    "shadow_cluster",
    {
      [":method"] = "POST",
      [":path"] = PATH,
      [":authority"] = CLUSTER,
      ["content-type"] = "application/json"
    },
    payload,
    5000,
    true
  )
end
