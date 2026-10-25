"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict");
const {harness,flat,text,load}=require("./play-dom.cjs");

test("step_chip_projects_all_six_steps_current_room_only_in_expedition_and_both_languages",async()=>{
 const h=await harness({chips:true});const {setLanguage}=await load("i18n/index.js");try{
 for(const [phase,screen,number,english,portuguese]of [["project_open","project",1,"Project","Projeto"],["project_open","idea",2,"Idea","Ideia"],["planning","idea",3,"Clarify","Clarify"],["world_ready","idea",4,"Map","Mapa"],["running","castle",5,"Expedition","Expedição"],["finished","castle",6,"End","Fim"]]){
  const host=h.document.getElementById("screen-host");host.hidden=screen==="castle";host.dataset.screen=screen;
  h.message({kind:"state",phase,state:{active_room:"T03",status:phase==="finished"?"finished":"running"}});
  for(const [language,label]of [["en",english],["pt-BR",portuguese]]){setLanguage(language);assert.match(text(h.chip.bar),new RegExp(`${number}/6.*${label}`));assert.equal(text(h.chip.bar).includes("T03"),number===5);}
 }
 }finally{setLanguage("en");h.destroy();}
});

test("party_chip_draws_cast_portraits_stamina_hearts_and_waiting_marks_then_opens_full_drawer_by_mouse_keyboard_escape",async()=>{
 const h=await harness({chips:true});try{
 h.message({kind:"state",phase:"running",state:{token_budget:1000,tokens_spent:500,party:[{role:"warrior",model:"full/model",status:"waiting for player"}],bugs:[{bug_id:"b1",title:"Monster",status:"open",room_id:"T01"}]}});
 assert.deepEqual(flat(h.chip.partyChip).filter(n=>n.dataset.cast).map(n=>n.dataset.cast),["mage","warrior","paladin"]);assert.match(text(h.chip.partyChip),/♥♥♡/);assert.equal(flat(h.chip.partyChip).find(n=>n.dataset.role==="warrior").textContent,"!");
 assert.ok(h.chip.drawer.hidden);h.chip.partyChip.click();assert.equal(h.chip.drawer.hidden,false);assert.equal(h.chip.partyChip.getAttribute("aria-expanded"),"true");
 assert.ok(text(h.chip.drawer).includes("full/model"));assert.match(text(h.chip.drawer),/Monster/);assert.ok(flat(h.chip.drawer).some(n=>n.id==="party-hud"));assert.ok(flat(h.chip.drawer).some(n=>n.id==="bestiary-hud"));assert.ok(flat(h.chip.drawer).some(n=>n.id==="castle-hud"));assert.ok(flat(h.chip.drawer).some(n=>n.textContent==="PT"));
 h.key("Escape");assert.ok(h.chip.drawer.hidden);assert.equal(h.document.activeElement,h.chip.partyChip);
 h.key("Enter",h.chip.partyChip);assert.equal(h.chip.drawer.hidden,false);h.key(" ",h.chip.partyChip);assert.equal(h.chip.drawer.hidden,true);
 }finally{h.destroy();}
});

test("top_right_has_no_dead_chips_and_registered_handlers_respond",async()=>{
 const h=await harness({chips:true});try{const right=h.chip.bar.children.at(-1);assert.ok(right.hidden);assert.equal(right.children.length,0);let helps=0,mutes=0;
 h.chip.register("help",()=>helps++);assert.equal(right.children.length,1);right.children[0].click();assert.equal(helps,1);
 h.chip.register("mute",()=>mutes++);assert.equal(right.children.length,2);right.children[1].click();assert.equal(mutes,1);
 h.chip.register("help",null);assert.equal(right.children.length,1);h.chip.register("mute",null);assert.ok(right.hidden);assert.equal(right.children.length,0);
 }finally{h.destroy();}
});

test("replay_chips_are_below_controls_and_emit_reserved_camera_insets",async()=>{
 const h=await harness({chips:true});try{const insets=[];h.browser.on("specvania:castle-insets",({detail})=>insets.push(detail));
 h.message({kind:"state",phase:"replaying",replay:{speed:1,stream:"plan"},state:{}});assert.equal(h.chip.bar.style.top,"72px");assert.deepEqual(insets.at(-1),{top:112,bottom:128});assert.match(text(h.chip.bar),/3\/6/);
 h.message({kind:"event",event:{type:"spec_written",feature:"world"}});assert.match(text(h.chip.bar),/4\/6/);
 h.message({kind:"state",phase:"running",state:{}});assert.equal(h.chip.bar.style.top,"36px");assert.deepEqual(insets.at(-1),{top:76,bottom:128});
 }finally{h.destroy();}assert.equal(h.browser.listenerCount("keydown"),0);
});

test("drawer_keeps_keyboard_room_list_and_chamber_selection_opens_same_body_room_panel",async()=>{
 const h=await harness({chips:true});const {mountRoomPanel}=await load("hud/room-panel.js");
 const panel=mountRoomPanel(h.document.getElementById("castle-hud"),h.browser,{fetchCode:async()=>({ok:true,json:async()=>({checklist:[{text:"Mission evidence"}],files:[]})})});
 try{h.message({kind:"state",phase:"running",state:{rooms:[{id:"T01",area:"A1",title:"First room",state:"open",depends_on:[]}]}});h.chip.partyChip.click();assert.match(text(h.chip.drawer),/T01.*First room/);
 h.browser.dispatchEvent(new h.browser.CustomEvent("specvania:room-selected",{detail:{room_id:"T01"}}));await Promise.resolve();await Promise.resolve();
 assert.equal(panel.panel.parent,h.body);assert.equal(panel.panel.style.visibility,"visible");assert.match(text(panel.panel),/Mission evidence/);assert.ok(!flat(h.chip.drawer).includes(panel.panel));
 flat(panel.panel).find(n=>n.tagName==="button"&&n.textContent==="Close room panel").click();assert.equal(panel.panel.style.visibility,"hidden");assert.equal(h.chip.drawer.hidden,false);
 }finally{panel.destroy();h.destroy();}
});

test("replay_and_chips_share_wide_row_or_compact_second_row_and_reserve_gutter",async()=>{
 const {setLanguage}=await load("i18n/index.js");
 for(const width of [960,1280])for(const language of ["en","pt-BR"]){const h=await harness({chips:true});try{
 h.browser.innerWidth=width;setLanguage(language);const bar=h.document.createElement("nav");bar.id="replay-bar";bar.getBoundingClientRect=()=>({width:710,height:32,bottom:68});h.body.append(bar);
 const insets=[];h.browser.on("specvania:castle-insets",({detail})=>insets.push(detail));h.message({kind:"state",phase:"replaying",replay:{speed:1,stream:"run"},state:{active_room:"T03"}});
 assert.equal(h.chip.bar.style.height,"32px");assert.equal(h.chip.bar.style.top,width===1280?"36px":"72px");assert.equal(h.chip.bar.style.left,width===1280?"730px":"12px");assert.equal(Number(h.chip.bar.dataset.bottom),width===1280?76:112);
 h.browser.dispatchEvent(new h.browser.CustomEvent("specvania:dialogue-size",{detail:{height:122,bottom:146}}));assert.equal(insets.at(-1).bottom,146);assert.equal(h.chip.drawer.style.bottom,"146px");
 h.browser.innerWidth=width===1280?960:1280;h.browser.emit("resize");assert.equal(h.chip.bar.style.top,width===1280?"72px":"36px");
 }finally{h.destroy();setLanguage("en");}}
});
