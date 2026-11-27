local _, ns = ...
ns.domains = { "player", "progress", "quests", "professions", "inventory", "hearthstone", "events" }
ns.NULL = {}

local function quote(text)
    return '"' .. text:gsub('[%z\1-\31\\"]', function(c)
        return string.format("\\u%04x", c:byte())
    end) .. '"'
end
function ns.JSON(value)
    if value == ns.NULL or value == nil then return "null" end
    local kind = type(value)
    if kind == "string" then return quote(value) end
    if kind == "boolean" then return value and "true" or "false" end
    if kind == "number" then assert(value == value and math.abs(value) < 2^53); return tostring(value) end
    assert(kind == "table")
    local pieces = {}
    if #value > 0 or next(value) == nil then
        for i = 1, #value do pieces[#pieces+1] = ns.JSON(value[i]) end
        return "[" .. table.concat(pieces, ",") .. "]"
    end
    local keys = {}; for key in pairs(value) do keys[#keys+1] = key end; table.sort(keys)
    for _, key in ipairs(keys) do pieces[#pieces+1] = quote(key) .. ":" .. ns.JSON(value[key]) end
    return "{" .. table.concat(pieces, ",") .. "}"
end

-- Pure synchronous encoding: reuse bounded scratch space rather than allocating
-- fifteen byte tables for every retransmitted packet. Explicit concat bounds
-- exclude bytes left over from a preceding wider integer.
local uintScratch = {}
local function uint(value, bytes)
    for i = bytes-1, 0, -1 do uintScratch[bytes-i] = string.char(math.floor(value / 256^i) % 256) end
    return table.concat(uintScratch, "", 1, bytes)
end
function ns.Adler(data)
    local a, b = 1, 0
    for i = 1, #data do a = (a + data:byte(i)) % 65521; b = (b + a) % 65521 end
    return b * 65536 + a
end
function ns.Packet(session, counter, code, revision, part, total, manifest, payload)
    assert(#payload > 0 and #payload <= 233 and total <= 211)
    local header = "AKW1" .. uint(session,4) .. uint(counter,4) .. uint(code,1)
        .. uint(revision,4) .. uint(part,2) .. uint(total,2) .. uint(#payload,2)
    for i = 1,7 do header = header .. uint(manifest[i],4) end
    local data = header .. payload .. string.rep("\0", 233 - #payload)
    return data .. uint(ns.Adler(data),4)
end
function ns.Colours(packet, result)
    assert(#packet == 288)
    result = result or {}
    local index, accumulator, bits = 0, 0, 0
    for i = 1, #packet do
        accumulator, bits = accumulator * 256 + packet:byte(i), bits + 8
        while bits >= 6 do
            bits = bits - 6
            local value = math.floor(accumulator / 2^bits)
            accumulator = accumulator % 2^bits
            index = index + 1
            local rgb = result[index]
            if not rgb then rgb = {}; result[index] = rgb end
            rgb[1], rgb[2], rgb[3] = math.floor(value/16)*85, math.floor(value/4)%4*85, value%4*85
        end
    end
    return result
end

-- Repeats cached serialized records for recovery. This timer never queries game
-- state or changes a domain revision/observation time.
local manifestScratch = {}
function ns.NextPacket(state)
    local code = state.code or 1
    local record = state.records[ns.domains[code]]
    if not record then return end
    local payload = record.text
    state.parts = state.parts or {}
    local part = state.parts[code] or 1
    local total = math.ceil(#payload/233)
    state.counter = state.counter + 1
    if state.counter >= 4294967295 then return end -- Requires reload, never wrap.
    for i, domain in ipairs(ns.domains) do manifestScratch[i] = state.records[domain] and state.records[domain].revision or 0 end
    local packet = ns.Packet(state.session,state.counter,code,record.revision,part,total,manifestScratch,
        payload:sub((part-1)*233+1,part*233))
    state.parts[code] = part >= total and 1 or part+1
    -- A full inventory must not hold up player/quest/event packets.
    state.code = code%7+1
    return packet
end
