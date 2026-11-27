local _, ns = ...
local NULL = ns.NULL
local function ordinary(value)
    if type(issecretvalue) ~= "function" then return false end
    local ok, secret = pcall(issecretvalue, value)
    return ok and secret == false
end
local function accessible(value)
    if not ordinary(value) then return false end
    if type(value) ~= "table" then return true end
    if type(canaccesstable) ~= "function" then return false end
    local ok, allowed = pcall(canaccesstable,value)
    return ok and allowed == true
end
local function call(fn, ...)
    if type(fn) ~= "function" then return nil,"unsupported" end
    local ok, value = pcall(fn,...)
    if not ok then return nil,"unavailable" end
    if not accessible(value) then return nil,"restricted" end
    return value
end
local function field(value,key)
    if not accessible(value) or type(value) ~= "table" then return nil end
    local item = rawget(value,key)
    if not ordinary(item) then return nil end
    return item
end
local function text(value)
    if not ordinary(value) or type(value) ~= "string" or #value > 1024 or value:find('[%z\1-\31\127]') then return NULL end
    return value
end
local function number(value)
    if not ordinary(value) or type(value) ~= "number" or value ~= value or value < 0 or value > 2147483647 then return NULL end
    return value
end
local function flag(value)
    if not ordinary(value) or type(value) ~= "boolean" then return NULL end
    return value
end
local function apiNumber(fn,...) return number(call(fn,...)) end
local function apiText(fn,...) return text(call(fn,...)) end
local function apiFlag(fn,...) return flag(call(fn,...)) end
local function record(status,data)
    -- All exported timestamps are source observations, never retransmission time.
    return {status=status,observedAt=ns.SafeTime(),data=data or NULL}
end
function ns.SafeTime()
    local value = call(GetServerTime)
    return number(value) ~= NULL and value or 0
end
local function character()
    return record("available", {name=apiText(UnitName,"player"),class=apiText(UnitClass,"player"),
        level=apiNumber(UnitLevel,"player"),location={mapId=apiNumber(C_Map and C_Map.GetBestMapForUnit,"player"),
        zone=apiText(GetZoneText),subzone=apiText(GetSubZoneText)}})
end
local function progress()
    return record("available", {money=apiNumber(GetMoney),xp=apiNumber(UnitXP,"player"),xpMax=apiNumber(UnitXPMax,"player"),
        rested=apiNumber(GetXPExhaustion),situation={inInstance=apiFlag(IsInInstance),
        instanceType=NULL,instanceName=NULL,difficulty=NULL,groupSize=apiNumber(GetNumGroupMembers),role=apiText(UnitGroupRolesAssigned,"player"),
        dead=apiFlag(UnitIsDeadOrGhost,"player"),ghost=apiFlag(UnitIsGhost,"player"),combat=apiFlag(UnitAffectingCombat,"player")}})
end
local function quests()
    local count, reason = call(C_QuestLog and C_QuestLog.GetNumQuestWatches)
    if number(count) == NULL then return record(reason == "unsupported" and reason or "unavailable") end
    local rows, partial = {}, count > 25
    for index=1,math.min(count,25) do
        local id = call(C_QuestLog.GetQuestIDForQuestWatchIndex,index)
        if number(id) ~= NULL then
            local objectives = call(C_QuestLog.GetQuestObjectives,id)
            local goalRows = {}
            if accessible(objectives) and type(objectives) == "table" then
                for goalIndex=1,4 do
                    local goal = field(objectives,goalIndex)
                    if not goal then break end
                    goalRows[#goalRows+1] = {text=text(field(goal,"text")),fulfilled=number(field(goal,"numFulfilled")),
                        required=number(field(goal,"numRequired")),finished=flag(field(goal,"finished"))}
                end
            else partial=true end
            rows[#rows+1] = {id=id,title=apiText(C_QuestLog.GetTitleForQuestID,id),
                complete=apiFlag(C_QuestLog.IsComplete,id),truncated=field(objectives,5)~=nil,objectives=goalRows}
        else partial=true end
    end
    return record(partial and "partial" or "available",{trackedCount=count,truncated=partial,quests=rows})
end
local professionIDs={129,164,165,171,182,185,186,197,202,333,356,393}
local function professions()
    local fn=C_SkillInfo and C_SkillInfo.GetSkillLineInfoByID
    if type(fn)~="function" then return record("unsupported") end
    local rows,partial={},false
    for _,id in ipairs(professionIDs) do
        local info,reason=call(fn,id)
        if reason then partial=true end
        if info then
            local rank=number(field(info,"rank"))
            if rank~=NULL and rank>0 then
                rows[#rows+1]={id=id,name=text(field(info,"name")),rank=rank,maxRank=number(field(info,"maxRank"))}
            end
        end
    end
    return record(partial and "partial" or "available",{professions=rows,truncated=false})
end
local function inventory()
    if not C_Container or type(C_Container.GetContainerItemInfo)~="function" then return record("unsupported") end
    local rows,partial={},false
    -- Forever's carried Reagent Bag is container 5; bank containers are excluded.
    for bag=0,5 do
        local count=call(C_Container.GetContainerNumSlots,bag)
        if number(count)==NULL then partial=true
        else
            if count>40 then partial=true end
            for slot=1,math.min(count,40) do
                local info,reason=call(C_Container.GetContainerItemInfo,bag,slot)
                if reason then partial=true end
                if info then
                    local id=number(field(info,"itemID"))
                    rows[#rows+1]={id=id,name=id~=NULL and apiText(C_Item and C_Item.GetItemNameByID,id) or NULL,
                        count=number(field(info,"stackCount")),bag=bag,slot=slot,equipped=false}
                end
            end
        end
    end
    for slot=1,19 do
        local id,reason=call(GetInventoryItemID,"player",slot)
        if reason then partial=true end
        if number(id)~=NULL then rows[#rows+1]={id=id,name=apiText(C_Item and C_Item.GetItemNameByID,id),
            count=1,bag=NULL,slot=slot,equipped=true} end
    end
    return record(partial and "partial" or "available",{items=rows,truncated=partial,scope="bags 0-5 including Reagent Bag, and equipment; bank and mail excluded"})
end
local function hearthstone()
    if not C_Item or type(C_Item.GetItemCooldown)~="function" or type(C_Item.GetItemCount)~="function" then return record("unsupported") end
    local count=apiNumber(C_Item.GetItemCount,6948)
    if count==NULL then return record("unavailable") end
    if count==0 then return record("available",{itemId=6948,count=0,remainingAtObservation=NULL,enabled=false}) end
    local values={pcall(C_Item.GetItemCooldown,6948)}
    if values[1]~=true then return record("unavailable") end
    local start,duration=number(values[2]),number(values[3])
    local enabled=flag(values[4])
    local now=apiNumber(GetTime)
    if start==NULL or duration==NULL or now==NULL or enabled==NULL then return record("unavailable") end
    return record("available",{itemId=6948,count=count,remainingAtObservation=math.max(0,start+duration-now),enabled=enabled})
end
local collectors={player=character,progress=progress,quests=quests,professions=professions,inventory=inventory,hearthstone=hearthstone}
function ns.Collect(domain,state)
    if domain=="events" then return record("available",{events=state.events,truncated=state.eventsTruncated}) end
    local ok,value=pcall(collectors[domain])
    if not ok then return record("unavailable") end
    -- Restricted/missing fields are explicit nulls, not fabricated zeros.
    local function incomplete(item)
        if item==NULL then return true end
        if type(item)=="table" then
            for _,child in pairs(item) do if incomplete(child) then return true end end
        end
        return false
    end
    if value.status=="available" and incomplete(value.data) then value.status="partial" end
    return value
end
