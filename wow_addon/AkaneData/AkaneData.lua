local _,ns=...
local state={records={},events={},eventsTruncated=false,counter=0,code=1,parts={},eventID=0}
ns.state=state
local dirty,scheduled={},false
local function printStatus(message) if DEFAULT_CHAT_FRAME then DEFAULT_CHAT_FRAME:AddMessage("|cff76dbd3AkaneData:|r "..message) end end
local function flush()
    scheduled=false
    for _,domain in ipairs(ns.domains) do
        if dirty[domain] then
            dirty[domain]=nil
            local previous=state.records[domain]
            local value=ns.Collect(domain,state)
            local data=ns.JSON(value)
            -- Observation timestamps change only on actual API captures; the
            -- transport below just repeats these cached bytes.
            if #data>49152 then value={status="unavailable",observedAt=ns.SafeTime(),data=ns.NULL};data=ns.JSON(value) end
            state.records[domain]={text=data,status=value.status,revision=(previous and previous.revision or 0)+1}
            for code,name in ipairs(ns.domains) do if name==domain then state.parts[code]=1 end end
        end
    end
end
local function mark(domains)
    for _,domain in ipairs(domains) do dirty[domain]=true end
    if not scheduled then scheduled=true;C_Timer.After(0.25,flush) end
end
local function event(kind)
    state.eventID=state.eventID+1
    state.events[#state.events+1]={id=state.eventID,kind=kind,observedAt=ns.SafeTime()}
    if #state.events>32 then table.remove(state.events,1);state.eventsTruncated=true end
    dirty.events=true
end
local mapping={
    PLAYER_LEVEL_UP={"player","progress"},PLAYER_XP_UPDATE={"progress"},UPDATE_EXHAUSTION={"progress"},PLAYER_MONEY={"progress"},
    ZONE_CHANGED={"player"},ZONE_CHANGED_INDOORS={"player"},ZONE_CHANGED_NEW_AREA={"player","progress"},
    QUEST_LOG_UPDATE={"quests"},QUEST_WATCH_UPDATE={"quests"},QUEST_WATCH_LIST_CHANGED={"quests"},
    QUEST_ACCEPTED={"quests"},QUEST_REMOVED={"quests"},QUEST_TURNED_IN={"quests","progress"},
    BAG_UPDATE_DELAYED={"inventory","hearthstone"},PLAYER_EQUIPMENT_CHANGED={"inventory"},
    GET_ITEM_INFO_RECEIVED={"inventory"},BAG_UPDATE_COOLDOWN={"hearthstone"},
    SKILL_LINES_CHANGED={"professions"},TRADE_SKILL_UPDATE={"professions"},
    PLAYER_DEAD={"progress"},PLAYER_ALIVE={"progress"},PLAYER_UNGHOST={"progress"},
    PLAYER_REGEN_DISABLED={"progress"},PLAYER_REGEN_ENABLED={"progress","hearthstone"},
    GROUP_ROSTER_UPDATE={"progress"},PLAYER_ENTERING_WORLD={"player","progress"},
}
local noteworthy={PLAYER_DEAD="death",PLAYER_LEVEL_UP="level_up",ZONE_CHANGED_NEW_AREA="zone_changed",ZONE_CHANGED="zone_changed"}
local frame=CreateFrame("Frame",nil,UIParent)
local cells={}
local function placeStrip()
    local _,height=GetPhysicalScreenSize()
    local unit=3*(768/math.max(1,height))/UIParent:GetEffectiveScale()
    frame:SetScale(1)
    frame:ClearAllPoints();frame:SetPoint("TOPLEFT",UIParent,"TOPLEFT",0,-8*unit)
    frame:SetSize(64*unit,6*unit);frame:SetFrameStrata("TOOLTIP");frame:SetAlpha(1)
    for index,cell in ipairs(cells) do
        cell:SetSize(unit,unit);cell:SetPoint("TOPLEFT",frame,"TOPLEFT",((index-1)%64)*unit,-math.floor((index-1)/64)*unit)
    end
end
for row=0,5 do for column=0,63 do
    local cell=frame:CreateTexture(nil,"OVERLAY")
    cell:SetColorTexture(0,0,0,1);cells[#cells+1]=cell
end end
placeStrip()
frame:SetScript("OnEvent",function(_,name)
    if name=="PLAYER_LOGIN" then
        state.session=math.random(1,2147483646)
        for _,domain in ipairs(ns.domains) do dirty[domain]=true end
        flush();printStatus("Event-driven telemetry enabled. /akanedata refresh recollects everything; /akanedata off stops export.")
    elseif name=="UI_SCALE_CHANGED" or name=="DISPLAY_SIZE_CHANGED" then placeStrip()
    else
        if noteworthy[name] then event(noteworthy[name]) end
        mark(mapping[name] or {})
    end
end)
frame:RegisterEvent("PLAYER_LOGIN")
for name in pairs(mapping) do pcall(frame.RegisterEvent,frame,name) end
pcall(frame.RegisterEvent,frame,"UI_SCALE_CHANGED");pcall(frame.RegisterEvent,frame,"DISPLAY_SIZE_CHANGED")
local elapsed=0
local colourBuffer={}
frame:SetScript("OnUpdate",function(_,delta)
    elapsed=elapsed+delta
    if elapsed<0.15 or not state.session or state.disabled then return end
    elapsed=0
    local packet=ns.NextPacket(state)
    if not packet then return end
    for index,rgb in ipairs(ns.Colours(packet,colourBuffer)) do cells[index]:SetColorTexture(rgb[1]/255,rgb[2]/255,rgb[3]/255,1) end
end)
SLASH_AKANEDATA1="/akanedata"
SlashCmdList.AKANEDATA=function(input)
    local command=(input or ""):lower():match("^%s*(.-)%s*$")
    if command=="refresh" then
        for _,domain in ipairs(ns.domains) do dirty[domain]=true end
        flush();state.code,state.parts=1,{}
        local counts={available=0,partial=0,unsupported=0,unavailable=0}
        for _,domain in ipairs(ns.domains) do local status=state.records[domain].status;counts[status]=(counts[status]or 0)+1 end
        printStatus(string.format("Full collection: %d available, %d partial, %d unsupported, %d unavailable. Exporting updated records.",
            counts.available,counts.partial,counts.unsupported,counts.unavailable))
    elseif command=="off" then state.disabled=true;frame:Hide();printStatus("Export off.")
    elseif command=="on" then state.disabled=false;frame:Show();printStatus("Export on.")
    else printStatus("/akanedata refresh | on | off. No periodic data refresh; optical retransmission only.") end
end
