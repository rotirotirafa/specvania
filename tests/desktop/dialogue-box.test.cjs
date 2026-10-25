"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict");
const {harness,flat,text,load}=require("./play-dom.cjs");

test("box_draws_real_narrator_lines_speaker_portraits_names_and_wraps_in_both_languages",async()=>{
 const cases=[
  [{type:"action",action:"write_file",args_summary:"src/total.py",thought:"Long prose ".repeat(80)},"warrior",/src\/total.py/],
  [{type:"review",feedback:"Check failed"},"paladin",/Check failed/],
  [{type:"gate_result",command:"pytest",exit_code:0,output_summary:"all passed"},"paladin",/pytest/],
  [{type:"bug_opened",bug_id:"bug-1",title:"Monster",body:"Details"},"banner",/Monster/],
  [{type:"bug_closed",bug_id:"bug-1",status:"fixed"},"banner",/bug-1/],
  [{type:"boss_scenario",area_id:"A1",scenario:"S1",passed:true,reason:"Verified"},"paladin",/S1/],
  [{type:"approval_answered",request_id:"a",answer:false,reason:"Fix script",responder:"terminal"},"warrior",/Fix script/],
 ];
 const {setLanguage,t}=await load("i18n/index.js");
 const h=await harness();try{for(const language of ["en","pt-BR"]){setLanguage(language);for(const [event,cast,pattern]of cases){h.event(event);assert.match(text(h.box),pattern);assert.ok(flat(h.box).some(n=>n.dataset.cast===cast));assert.ok(text(h.box).includes(t(event.type.startsWith("bug_")?"play.narrator":`role.${cast}`)));assert.equal(h.box.style.overflowWrap,"anywhere");assert.equal(h.box.style.overflow,"hidden");assert.equal(h.box.children[1].style.overflow,"auto");assert.equal(h.box.getAttribute("aria-live"),"polite");}}}finally{setLanguage("en");h.destroy();}
});

test("turn_focus_never_steals_typing_and_returns_to_castle_when_answered",async()=>{
 const h=await harness();try{const field=h.document.createElement("textarea");h.body.append(field);field.focus();field.value="Draft";
 h.event({type:"approval_requested",request_id:"a",command:"echo ok",cwd:"/repo"});assert.equal(h.document.activeElement,field);h.key("A",field);assert.equal(h.sent.length,0);
 h.box.focus();h.key("N");h.input.value="Keep me";const input=h.input;
 h.event({type:"model_requested",role:"paladin",model:"model",ts:"2026-10-15T00:00:00Z"});assert.equal(h.document.activeElement,h.input);assert.equal(h.input.value,"Keep me");
 h.message({kind:"approval_rejected",reason:"request pending"});assert.equal(h.input.value,"Keep me");assert.equal(h.document.activeElement,input);
 h.event({type:"approval_answered",request_id:"a",answer:false,responder:"terminal"});assert.equal(h.document.activeElement,h.document.getElementById("world-map"));
 h.event({type:"run_paused",reason:"out of lives"});assert.equal(h.document.activeElement,h.button("1 · Retry"));
 }finally{h.destroy();}
});

test("box_clarify_number_arrow_enter_click_free_text_and_default_use_existing_messages",async()=>{
 for(const method of ["number","arrows","click","text","default"]){const h=await harness();try{
  h.message({kind:"studio_state",phase:"running",clarify_request:{question_id:"q",text:"Which?",options:method==="default"?[]:["Blue","Green"],default:"Blue"}});
  if(method==="number")h.key("2");else if(method==="arrows"){h.key("ArrowDown");h.key("Enter");}else if(method==="click")h.button("2 · Green").click();else if(method==="text"){const field=flat(h.box).find(n=>n.tagName==="textarea");field.focus();field.value="Free answer";h.key("1",field);assert.equal(h.sent.length,0);h.button("Send answer").click();}else h.key("Enter");
  assert.deepEqual(h.sent,[{kind:"clarify_answer",question_id:"q",answer:method==="text"?"Free answer":method==="default"?"Blue":"Green",control_token:"control"}]);
 }finally{h.destroy();}}
});

test("box_preserves_thinking_elapsed_time_on_snapshot_and_uses_running_indicator_timer",async()=>{
 let now=0;const callbacks=new Map(),timer={setInterval(fn){callbacks.set(1,fn);return 1;},clearInterval(id){callbacks.delete(id);}};
 const h=await harness({approval:false,timing:{clock:{now:()=>now},timer}});try{
 const state={run_id:"r",party:[{role:"warrior",model:"full/model",status:"thinking"}]};h.message({kind:"state",state});now=12000;callbacks.get(1)();assert.match(text(h.box),/thinking.*12 s.*full\/model/);
 h.message({kind:"state",state});callbacks.get(1)();assert.match(text(h.box),/12 s/);h.event({type:"action",action:"finish"});assert.equal(callbacks.size,0);assert.equal(h.box.children.at(-1).textContent,"");assert.equal(h.box.children.at(-1).style.minHeight,"14px");
 }finally{h.destroy();}assert.equal(callbacks.size,0);
});

test("replay_restores_recorded_answer_without_turn_or_notifications_and_stale_live_snapshot_cannot_restore_answered_request",async()=>{
 const h=await harness();try{
 h.event({type:"approval_requested",run_id:"r",seq:3,request_id:"a",command:"echo ok",cwd:"/repo"});h.event({type:"approval_answered",run_id:"r",seq:4,request_id:"a",answer:true,responder:"terminal"});
 h.message({kind:"state",state:{run_id:"r",seq:2},approval_request:{request_id:"a",command:"echo stale",cwd:"/repo"}});assert.equal(h.box.dataset.turn,"");assert.match(text(h.box),/approved/);
 h.message({kind:"state",phase:"replaying",replay:{speed:1,dialogue:{type:"clarify_answer",answer:"Green",question_id:"q"}},state:{}});assert.equal(h.box.dataset.turn,"");assert.match(text(h.box),/Recorded answer: Green/);h.key("1");assert.equal(h.sent.length,0);
 }finally{h.destroy();}
});

test("pause_selection_survives_repeated_snapshot_and_focused_choice_enter_matches_click",async()=>{
 const h=await harness();try{const snapshot={kind:"state",phase:"running",state:{status:"paused",reason:"out of lives"}};h.message(snapshot);h.key("ArrowDown");const skip=h.button("2 · Skip");assert.equal(h.document.activeElement,skip);
 h.message(snapshot);assert.equal(h.document.activeElement,skip);h.key("Enter");assert.equal(h.sent.at(-1).choice,"skip");
 h.event({type:"attempt_started",room_id:"T01"});h.event({type:"run_paused",reason:"boss failed"});const abort=h.button("3 · Abort");abort.focus();abort.dispatch("focus");h.key("Enter");assert.equal(h.sent.at(-1).choice,"abort");
 }finally{h.destroy();}
});

test("compact_box_fits_one_to_three_lines_at_both_sizes_and_turns_scroll_at_45_percent",async()=>{
 const {setLanguage}=await load("i18n/index.js");
 for(const [width,height]of [[960,540],[1280,800]])for(const language of ["en","pt-BR"]){const h=await harness();try{
  h.browser.innerWidth=width;h.browser.innerHeight=height;setLanguage(language);
  for(const line of ["Short line", "A long wrapped line ".repeat(80)]){
   h.event({type:"review",feedback:line});assert.equal(h.box.style.height,"auto");assert.equal(h.box.style.minHeight,"104px");assert.equal(h.box.style.maxHeight,"124px");assert.ok(Number(h.box.dataset.height)>=104&&Number(h.box.dataset.height)<=124);
   assert.equal(h.box.children[1].style.maxHeight,"50px");assert.equal(h.box.children[1].style.overflow,"auto");assert.equal(h.box.style.lineHeight,"14px");assert.equal(h.box.style.overflowWrap,"anywhere");
  }
  h.event({type:"approval_requested",request_id:"long",command:"x".repeat(500),cwd:"/repo"});assert.ok(Number(h.box.dataset.height)>124);assert.ok(Number(h.box.dataset.height)<=height*.45);assert.equal(h.box.style.maxHeight,"45vh");
  h.message({kind:"state",state:{},phase:"running",approval_request:null});h.message({kind:"studio_state",phase:"running",clarify_request:{question_id:"q",text:"Long question ".repeat(1000),options:Array.from({length:30},(_,i)=>`Option ${i}`),default:"Option 0"}});
  assert.equal(Number(h.box.dataset.height),Math.floor(height*.45));assert.equal(h.box.children[1].style.maxHeight,"none");assert.equal(h.box.children[2].style.overflow,"auto");
 }finally{h.destroy();setLanguage("en");}}
});

test("narrator_uses_neutral_kenney_banner_for_run_lines_never_a_party_face",async()=>{
 const h=await harness();try{for(const event of [{type:"run_started",feature:"world"},{type:"run_paused",reason:"out of lives"},{type:"run_finished",status:"aborted"}]){
 h.event(event);const icons=flat(h.box).filter(node=>node.dataset.cast);assert.equal(icons.length,1);assert.equal(icons[0].dataset.cast,"banner");assert.equal(icons[0].style.backgroundPosition,"-160px -64px");
 }}finally{h.destroy();}
});

test("measured_box_resize_updates_castle_insets_instead_of_reserving_a_fixed_bottom",async()=>{
 const h=await harness({chips:true});try{let height=118;h.box.getBoundingClientRect=()=>({height});const insets=[];h.browser.on("specvania:castle-insets",({detail})=>insets.push(detail));
 h.browser.emit("resize");assert.equal(h.box.dataset.height,"118");assert.equal(insets.at(-1).bottom,142);
 height=124;h.browser.emit("resize");assert.equal(h.box.dataset.height,"124");assert.equal(insets.at(-1).bottom,148);
 h.message({kind:"studio_state",phase:"project_open"});assert.equal(h.box.hidden,true);assert.equal(insets.at(-1).bottom,0);
 }finally{h.destroy();}assert.equal(h.browser.listenerCount("resize"),0);
});
