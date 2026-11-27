// Service-free Lua execution. No native APIs, screen reads, sound or game input.
const fs=require('node:fs');
const path=require('node:path');
const {lua,lauxlib,lualib,to_luastring,to_jsstring}=require(process.argv[3]);
// Count Lua table allocations, not Node heap bytes or WoW's native allocator.
const ltable=require(path.join(process.argv[3],'src/ltable.js'));
const newTable=ltable.luaH_new;let tableAllocations=0;
ltable.luaH_new=function(...args){tableAllocations++;return newTable(...args);};
const L=lauxlib.luaL_newstate();lualib.luaL_openlibs(L);
function run(source){const status=lauxlib.luaL_dostring(L,to_luastring(source));
 if(status!==lua.LUA_OK)throw Error(to_jsstring(lua.lua_tostring(L,-1)));}
run(`
ns={}; unpack=unpack or table.unpack
timers={}; calls={}; xp=10; combat=false; dead=false
function issecretvalue(value)return type(value)=='table' and value.secret==true end
function canaccesstable(value)return not issecretvalue(value) end
function GetServerTime()return 1000000 end
function GetTime()return 100 end
function UnitName()return 'Quentin'end
function UnitClass()return 'Paladin'end
function UnitLevel()return 8 end
function UnitXP()return xp end
function UnitXPMax()return 400 end
function GetMoney()return 100 end
function GetXPExhaustion()return 0 end
function GetZoneText()return 'Elwynn Forest'end
function GetSubZoneText()return 'Goldshire'end
function IsInInstance()return false end
function GetNumGroupMembers()return 0 end
function UnitGroupRolesAssigned()return 'NONE'end
function UnitIsDeadOrGhost()return dead end
function UnitIsGhost()return false end
function UnitAffectingCombat()return combat end
function GetInventoryItemID()return nil end
function GetPhysicalScreenSize()return 1920,1080 end
UIParent={GetEffectiveScale=function()return 1 end}
C_Map={GetBestMapForUnit=function()return 1429 end}
C_QuestLog={GetNumQuestWatches=function()return 1 end,GetQuestIDForQuestWatchIndex=function()return 7 end,
 GetTitleForQuestID=function()return 'A test quest'end,IsComplete=function()return false end,
 GetQuestObjectives=function()return {{text='Kobolds slain',numFulfilled=2,numRequired=10,finished=false}}end}
C_SkillInfo={GetSkillLineInfoByID=function(id)calls.professions=(calls.professions or 0)+1
 if id==185 then return {name='Cooking',rank=25,maxRank=75}end end}
linenCount=38;bagReads={}
C_Container={GetContainerNumSlots=function(bag)
 assert(bag>=0 and bag<=5,'bank container was scanned');bagReads[bag]=true
 return bag==0 and 2 or bag==5 and 1 or 0 end,
 GetContainerItemInfo=function(bag,slot)
 if bag==5 then return {itemID=2589,stackCount=linenCount}end
 return {itemID=slot==1 and 6291 or 6948,stackCount=slot==1 and 5 or 1}end}
C_Item={GetItemNameByID=function(id)return id==6291 and 'Raw Brilliant Smallfish' or id==2589 and 'Linen Cloth' or 'Hearthstone'end,
 GetItemCount=function()return 1 end,GetItemCooldown=function()return 0,0,true end}
C_Timer={After=function(_,fn)timers[#timers+1]=fn end}
function drain()local work=timers;timers={};for _,fn in ipairs(work)do fn()end end
SlashCmdList={}
local noop=function()end
function CreateFrame()
 local frame={SetScale=noop,ClearAllPoints=noop,SetPoint=noop,SetSize=noop,SetFrameStrata=noop,SetAlpha=noop,Show=noop,Hide=noop,
 RegisterEvent=noop,CreateTexture=function()return {SetSize=noop,SetPoint=noop,SetColorTexture=noop}end,
 SetScript=function(self,key,fn)self[key]=fn end}
 testFrame=frame;return frame
end
`);
for(const file of ['Protocol.lua','Collector.lua','AkaneData.lua'])
 run(`(function(...)\n${fs.readFileSync(path.join(process.argv[2],file),'utf8')}\nend)('AkaneData',ns)`);
run(`
testFrame:OnEvent('PLAYER_LOGIN')
baseline=ns.state.records.professions.text
observed=ns.state.records.professions.revision
reads=calls.professions
packets={};colours={}
for i=1,80 do
 local packet=ns.NextPacket(ns.state)
 local bytes={};for j=1,#packet do bytes[#bytes+1]=string.format('%02x',packet:byte(j))end
 packets[#packets+1]=table.concat(bytes)
 if i==1 then for _,rgb in ipairs(ns.Colours(packet))do for _,value in ipairs(rgb)do colours[#colours+1]=value end end end
end
-- Warm the caller-owned colour buffer, then measure the steady-state sender.
testFrame:OnUpdate(0.2)
function retainedTables()
 local seen={};local count=0
 local function walk(value)
  if type(value)~='table' or seen[value]then return end
  seen[value]=true;count=count+1
  for _,child in pairs(value)do walk(child)end
 end
 walk(ns.state);return count
end
local retainedBefore=retainedTables()
retainedIdleBefore=retainedBefore
`);
const beforeIdle=tableAllocations;
run('for i=1,500 do testFrame:OnUpdate(0.2)end');
const idleTableAllocations=tableAllocations-beforeIdle;
lua.lua_pushinteger(L,idleTableAllocations);lua.lua_setglobal(L,'idleTableAllocations');
run(`
retainedIdleAfter=retainedTables()
assert(calls.professions==reads,'transport queried professions')
assert(ns.state.records.professions.text==baseline and ns.state.records.professions.revision==observed)
assert(bagReads[5],'Reagent Bag was never scanned')
reagentInventory=ns.state.records.inventory.text
local inventoryRevision=ns.state.records.inventory.revision
linenCount=41;testFrame:OnEvent('BAG_UPDATE_DELAYED');drain()
assert(ns.state.records.inventory.revision==inventoryRevision+1,'Reagent Bag event did not update inventory')
assert(ns.state.records.inventory.text:find('"count":41',1,true))
updatedReagentInventory=ns.state.records.inventory.text
combat=true;testFrame:OnEvent('PLAYER_REGEN_DISABLED');drain()
local before=ns.state.counter;testFrame:OnUpdate(0.2)
assert(ns.state.counter>before,'combat stopped export')
xp=20;testFrame:OnEvent('PLAYER_XP_UPDATE');drain()
assert(ns.state.records.progress.text:find('"xp":20',1,true))
local revisions={};for _,domain in ipairs(ns.domains)do revisions[domain]=ns.state.records[domain].revision end
SlashCmdList.AKANEDATA('refresh')
for _,domain in ipairs(ns.domains)do assert(ns.state.records[domain].revision==revisions[domain]+1,'manual refresh omitted '..domain)end
dead=true;testFrame:OnEvent('PLAYER_DEAD');drain()
assert(#ns.state.events==1 and ns.state.events[1].kind=='death')
dead=false;testFrame:OnEvent('PLAYER_ALIVE');drain();assert(#ns.state.events==1)
local secret=setmetatable({secret=true},{__tostring=function()error('secret stringified')end})
xp=secret;testFrame:OnEvent('PLAYER_XP_UPDATE');drain()
assert(ns.state.records.progress.text:find('"xp":null',1,true),'secret crossed boundary')
local skill=C_SkillInfo;C_SkillInfo=nil
assert(ns.Collect('professions',ns.state).status=='unsupported')
C_SkillInfo=skill
result=ns.JSON({packets=packets,colours=colours,professions=baseline,
 inventory=ns.state.records.inventory.text,eventCount=#ns.state.events,combatCounterAdvanced=true,
 reagentInventory=reagentInventory,updatedReagentInventory=updatedReagentInventory,
 idleTableAllocations=idleTableAllocations,retainedIdleBefore=retainedIdleBefore,retainedIdleAfter=retainedIdleAfter})
`);
lua.lua_getglobal(L,'result');
process.stdout.write(to_jsstring(lua.lua_tostring(L,-1)));
lua.lua_close(L);
