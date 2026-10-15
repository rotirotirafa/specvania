"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const {pathToFileURL} = require("node:url");
const root = path.resolve(__dirname, "../../src/specvania/ui/static");
const load = name => import(pathToFileURL(path.join(root, name)));
const question = {question_id:"q1", text:"Which store?", options:["SQLite", "Postgres"], default:"Postgres"};
const approval = {request_id:"r1", command:"npm test", cwd:"/repo"};
const eventQuestion = {type:"clarify_question", question_id:"q1", question:question.text, options:question.options, default:question.default};
const freeze = value => {if (value && typeof value === "object") {Object.values(value).forEach(freeze); Object.freeze(value);} return value;};
async function api() {return {...await load("play/step.js"), ...await load("play/narrator.js"), ...await load("play/keys.js")};}

test("steps_cover_every_phase_and_screen_with_translated_ids", async () => {
  const {deriveStep} = await api();
  for (const [phase, number, id] of [["idle",1,"project"],["project_open",1,"project"],["planning",3,"clarify"],["world_ready",4,"map"],["running",5,"expedition"],["finished",6,"end"],["replaying",5,"expedition"]]) {
    assert.deepEqual(deriveStep({studio:{phase}}), {number,total:6,id,labelKey:`play.step.${id}`,roomId:null});
  }
  for (const [screen, number] of [["title",1],["project",1],["settings",1],["starting",1],["error",1],["probe",1],["idea",2],["clarify",3],["working",3],["map",4],["world",4],["saving",4],["castle",5],["running",5],["approval",5],["pause",5],["boss",5],["end",6]]) {
    assert.equal(deriveStep({screen}).number,number,screen);
  }
  for (const [phase,number] of [["planning",3],["world_ready",4]]) assert.equal(deriveStep({studio:{phase},screen:"idea"}).number,number);
  assert.equal(deriveStep({studio:{phase:"running"},screen:"settings"}).number,1);
  assert.equal(deriveStep({studio:{phase:"running"},state:{active_room:"T07"}}).roomId,"T07");
  assert.equal(deriveStep({studio:{phase:"running"},state:{party_room:"T06"}}).roomId,"T06");
  assert.equal(deriveStep({studio:{phase:"finished"},state:{party_room:"T06"}}).roomId,null);
  const {createI18n} = await load("i18n/index.js");
  for (const [language, labels] of [["en",["Project","Idea","Clarify","Map","Expedition","End"]],["pt-BR",["Projeto","Ideia","Clarify","Mapa","Expedição","Fim"]]]) {
    const service = createI18n({navigator:{language}});
    for (const [index,id] of ["project","idea","clarify","map","expedition","end"].entries()) assert.equal(service.t(`play.step.${id}`),labels[index]);
  }
});

test("replayed_plan_and_run_follow_the_recorded_moment_and_room", async () => {
  const {deriveStep} = await api();
  const events = [];
  for (const [event,number,roomId] of [[eventQuestion,3,null],[{type:"clarify_answer",answer:"SQLite"},3,null],[{type:"spec_written",feature:"store"},4,null],[{type:"world_loaded"},4,null],[{type:"run_started"},5,null],[{type:"room_entered",room_id:"T02"},5,"T02"],[{type:"gate_result"},5,"T02"],[{type:"room_entered",room_id:"T03"},5,"T03"],[{type:"run_finished"},6,null]]) {
    events.push(event);
    const result = deriveStep({studio:{phase:"replaying"},screen:"title",events,replay:true});
    assert.equal(result.number,number,event.type); assert.equal(result.roomId,roomId,event.type);
  }
  assert.equal(deriveStep({studio:{phase:"replaying",replay:{stream:"plan"}}}).number,3);
  assert.equal(deriveStep({studio:{phase:"replaying",replay:{stream:"plan",dialogue:eventQuestion}}}).number,3);
  assert.equal(deriveStep({studio:{phase:"replaying",replay:{stream:"run"}},state:{status:"finished"}}).number,6);
  assert.equal(deriveStep({studio:{phase:"replaying",replay:{stream:"run"}},state:{status:"paused",party_room:"T03"}}).roomId,"T03");
});

// Literal expected fragments check event payloads survive translation, as well
// as templates resolving in both languages (no untranslated identifiers/JSON).
const lineCases = [
  [{type:"run_started",feature:"store"},"narrator","store"],
  [eventQuestion,"mage","Which store?"],
  [{type:"clarify_answer",answer:"SQLite"},"mage","SQLite"],
  [{type:"spec_written",feature:"store"},"mage","store"],
  [{type:"room_entered",room_id:"T02"},"warrior","T02"],
  [{type:"attempt_started",room_id:"T02",attempt:2},"warrior","2"],
  [{type:"action",action:"write_file",thought:"Add totals.",args_summary:"src/total.py"},"warrior","Add totals."],
  [{type:"review",feedback:"Missing a case.",verdict:"fail"},"paladin","Missing a case."],
  [{type:"gate_result",command:"pytest",exit_code:1,output_summary:"one failure"},"paladin","one failure"],
  [{type:"bug_opened",bug_id:"B2",title:"Wrong total",body:"Overflow"},"narrator","Wrong total"],
  [{type:"bug_closed",bug_id:"B2",status:"fixed"},"narrator","B2"],
  [{type:"boss_started",area_id:"A2"},"paladin","A2"],
  [{type:"boss_scenario",area_id:"A2",scenario:"S5",passed:false,reason:"Missing zero"},"paladin","Missing zero"],
  [{type:"boss_result",area_id:"A2",passed:true},"paladin","A2"],
  [{type:"approval_requested",...approval},"warrior","npm test"],
  [{type:"approval_answered",request_id:"r1",answer:true,responder:"terminal"},"warrior","terminal"],
  [{type:"approval_answered",request_id:"r1",answer:false,responder:"desktop window",reason:"Wrong directory"},"warrior","Wrong directory"],
  [{type:"room_cleared",room_id:"T02",commit:"abcd123"},"narrator","abcd123"],
  [{type:"room_failed",room_id:"T02",reason:"No lives"},"narrator","No lives"],
  [{type:"room_skipped",room_id:"T02",reason:"Keep partial work"},"narrator","Keep partial work"],
  [{type:"run_paused",reason:"stamina exhausted"},"narrator",null],
  [{type:"run_finished",status:"aborted"},"narrator",null],
  [{type:"stamina_restored",token_budget:250000},"narrator","250000"],
  [{type:"respawn",room_id:"T02",attempt:3,feedback:"Handle zero"},"paladin","Handle zero"],
  [{type:"tool_refused",tool:"read_file",reason:"Outside repo"},"warrior","Outside repo"],
  [{type:"fallback",role:"mage",to_model:"next/model",reason:"Unavailable"},"mage","next/model"],
  [{type:"cooldown",role:"warrior",seconds:5,reason:"429"},"warrior","5"],
];

test("every_line_event_has_its_speaker_and_payload_in_both_languages", async () => {
  const {narrate,renderLine} = await api();
  const {createI18n} = await load("i18n/index.js");
  for (const [event,speaker,fragment] of lineCases) {
    const result = narrate({events:[event]}); assert.equal(result.speaker,speaker,event.type); assert.ok(result.line.key,event.type);
    const rendered = [];
    for (const language of ["en","pt-BR"]) {
      const service = createI18n({navigator:{language},console:{warn(message){assert.fail(message);}}});
      const line = renderLine(result.line,service.t); rendered.push(line);
      assert.ok(line.trim(),event.type); assert.doesNotMatch(line,/\{\w+\}|(?:play|dialogue|approval|pause)\./,event.type);
      if (fragment) assert.ok(line.includes(fragment),`${event.type}: ${line}`);
    }
    if (!["clarify_question","review"].includes(event.type)) assert.notEqual(rendered[0],rendered[1],event.type);
  }
});

test("warrior_actions_are_readable_and_keep_the_latest_real_line", async () => {
  const {narrate,renderLine} = await api(); const {createI18n} = await load("i18n/index.js");
  const services = ["en","pt-BR"].map(language=>createI18n({navigator:{language}}));
  for (const [action,en,pt] of [["read_file","Read file","Ler arquivo"],["list_dir","List directory","Listar diretório"],["search","Search","Buscar"],["write_file","Write file","Escrever arquivo"],["edit_file","Edit file","Editar arquivo"],["run_shell","Run command","Executar comando"],["finish","Room finished.","Terminei a sala."]]) {
    const result = narrate({events:[{type:"action",action,thought:"Thought",args_summary:"src/a.py"},{type:"model_requested",role:"warrior"},{type:"model_call"},{type:"world_loaded"}]});
    for (const [i,expected] of [en,pt].entries()) {const text=renderLine(result.line,services[i].t); assert.ok(text.includes(expected),text); assert.ok(text.includes("Thought")); assert.ok(text.includes("src/a.py")); assert.doesNotMatch(text,/\b(?:read_file|list_dir|write_file|edit_file|run_shell)\b/);}
  }
  assert.equal(narrate({}).line,null);
});

test("live_approval_clarify_and_pause_turns_restore_from_snapshots_and_events", async () => {
  const {narrate} = await api();
  for (const options of [{approvalRequest:approval},{state:{dialogue:{type:"approval_requested",...approval}}},{events:[{type:"approval_requested",...approval}]}]) {
    const {turn}=narrate(options); assert.equal(turn.kind,"approval"); assert.equal(turn.command,"npm test"); assert.equal(turn.cwd,"/repo");
    assert.deepEqual(turn.choices.map(c=>c.id),["approve","deny","view"]);
  }
  for (const options of [{studio:{phase:"planning",clarify_request:question}},{events:[eventQuestion]}]) {
    const {turn}=narrate(options); assert.equal(turn.kind,"clarify"); assert.equal(turn.question_id,"q1");
    assert.deepEqual(turn.choices.map(c=>c.value),["SQLite","Postgres"]); assert.equal(turn.selected,1);
  }
  const free = narrate({studio:{clarify_request:{...question,options:[],default:"Other"}}}).turn;
  assert.deepEqual(free.choices,[]); assert.equal(free.default,"Other");
  for (const options of [{studio:{pause_request:"stamina exhausted"}},{state:{status:"paused",reason:"stamina exhausted"}},{events:[{type:"run_paused",reason:"stamina exhausted"}]}]) {
    const {turn}=narrate(options); assert.equal(turn.kind,"pause"); assert.deepEqual(turn.choices.map(c=>[c.id,c.value]),[["rest","retry"],["abort","abort"]]);
  }
  for (const reason of ["out of lives","boss failed","model chain exhausted"]) assert.deepEqual(narrate({events:[{type:"run_paused",reason}]}).turn.choices.map(c=>c.value),["retry","skip","abort"]);
  assert.equal(narrate({approvalRequest:{...approval,command:"x".repeat(400)},events:[{type:"approval_requested",...approval}]}).turn.command,"x".repeat(400));
});

test("resolved_turns_clear_without_losing_an_unrelated_pending_approval", async () => {
  const {narrate} = await api();
  const requested={type:"approval_requested",...approval};
  assert.equal(narrate({events:[requested,{type:"approval_answered",request_id:"other",answer:true,responder:"terminal"}]}).turn.request_id,"r1");
  assert.equal(narrate({events:[requested,{type:"approval_answered",request_id:"r1",answer:true,responder:"terminal"}]}).turn,null);
  assert.equal(narrate({events:[eventQuestion,{type:"clarify_answer",question_id:"q1",answer:"SQLite"}]}).turn,null);
  for (const type of ["stamina_restored","attempt_started","room_entered","room_skipped","boss_started","run_finished"]) assert.equal(narrate({events:[{type:"run_paused",reason:"boss failed"},{type}]}).turn,null,type);
  assert.equal(narrate({state:{status:"aborted",dialogue:requested}}).turn,null);
  assert.equal(narrate({studio:{phase:"finished"},approvalRequest:approval}).turn,null);
});

test("replay_never_has_a_turn_and_recorded_answers_are_lines", async () => {
  const {narrate,renderLine} = await api(); const {createI18n}=await load("i18n/index.js");
  for (const replaySource of [{replay:true},{studio:{phase:"replaying"}},{studio:{replay:{stream:"plan"}}}]) {
    for (const [event] of lineCases) assert.equal(narrate({...replaySource,events:[event]}).turn,null,event.type);
    for (const options of [{approvalRequest:approval},{studio:{clarify_request:question}},{state:{status:"paused",reason:"stamina exhausted"}}]) assert.equal(narrate({...options,...replaySource}).turn,null);
  }
  for (const language of ["en","pt-BR"]) {
    const {t}=createI18n({navigator:{language}});
    assert.ok(renderLine(narrate({replay:true,events:[eventQuestion,{type:"clarify_answer",answer:"SQLite"}]}).line,t).includes("SQLite"));
    for (const [type,choiceKey] of [["attempt_started","pause.retry"],["room_skipped","pause.skip"],["stamina_restored","pause.rest"],["run_finished","pause.abort"]]) {
      const result=narrate({replay:true,events:[{type:"run_paused",reason:"stamina exhausted"},{type,status:"aborted",room_id:"T01",token_budget:250000,attempt:2}]});
      assert.ok(renderLine(result.line,t).includes(t(choiceKey)),type);
    }
    const restored=narrate({studio:{replay:{dialogue:{type:"clarify_answer",answer:"Postgres"}}}});
    assert.ok(renderLine(restored.line,t).includes("Postgres")); assert.equal(restored.turn,null);
  }
});

test("keys_map_approval_and_numbered_choices_to_exact_existing_messages", async () => {
  const {narrate,mapKey} = await api(); const options={controlToken:"control"};
  for (const language of ["en","pt-BR"]) {
    const approvalState=narrate({approvalRequest:approval,studio:{language},controlToken:"control"});
    for (const key of ["a","A"]) assert.deepEqual(mapKey({key},approvalState,options),{control:{kind:"approval_answer",request_id:"r1",answer:true,control_token:"control"}});
    for (const key of ["n","N"]) assert.deepEqual(mapKey({key},approvalState,options),{action:"deny-reason",request_id:"r1"});
    for (const key of ["v","V"]) assert.deepEqual(mapKey({key},approvalState,options),{action:"view-command",command:"npm test",cwd:"/repo"});
    const clarify=narrate({studio:{clarify_request:question}});
    assert.deepEqual(mapKey({key:"1"},clarify,options),{control:{kind:"clarify_answer",question_id:"q1",answer:"SQLite",control_token:"control"}});
    assert.deepEqual(mapKey({key:"Enter"},clarify,options),{control:{kind:"clarify_answer",question_id:"q1",answer:"Postgres",control_token:"control"}});
    const many=narrate({studio:{clarify_request:{...question,options:["one","two","three","four","five","six","seven"]}}});
    assert.deepEqual(mapKey({key:"7"},many,options),{control:{kind:"clarify_answer",question_id:"q1",answer:"seven",control_token:"control"}});
    const pause=narrate({studio:{pause_request:"boss failed"}});
    for (const [i,choice] of ["retry","skip","abort"].entries()) assert.deepEqual(mapKey({key:String(i+1)},pause,options),{control:{kind:"pause_decision",choice,control_token:"control"}});
    const rest=narrate({studio:{pause_request:"stamina exhausted"}});
    assert.deepEqual(mapKey({key:"1"},rest,options),{control:{kind:"pause_decision",choice:"retry",control_token:"control"}});
    assert.equal(mapKey({key:"9"},pause,options),null); assert.equal(mapKey({key:"0"},pause,options),null);
  }
});

test("arrows_select_options_then_enter_confirms_and_wraps", async () => {
  const {narrate,mapKey}=await api();
  for (const source of [{studio:{clarify_request:question}},{studio:{pause_request:"boss failed"}}]) {
    const state=narrate(source), count=state.turn.choices.length;
    const up=mapKey({key:"ArrowUp"},state); assert.deepEqual(up,{selection:(state.turn.selected+count-1)%count});
    const down=mapKey({key:"ArrowDown"},state); assert.deepEqual(down,{selection:(state.turn.selected+1)%count});
    const last={...state,turn:{...state.turn,selected:count-1}}; assert.deepEqual(mapKey({key:"ArrowDown"},last),{selection:0});
    const first={...state,turn:{...state.turn,selected:0}}; assert.deepEqual(mapKey({key:"ArrowUp"},first),{selection:count-1});
    const selected={...state,turn:{...state.turn,selected:up.selection}};
    const result=mapKey({key:"Enter"},selected,{controlToken:"control"});
    assert.equal(result.control.answer ?? result.control.choice,state.turn.choices[up.selection].value);
  }
  const free=narrate({studio:{clarify_request:{...question,options:[]}}});
  assert.equal(mapKey({key:"Enter"},free,{controlToken:"control"}).control.answer,"Postgres");
});

test("help_close_overview_camera_and_context_keys_follow_D31", async () => {
  const {narrate,mapKey}=await api(); const castle=narrate({screen:"castle"});
  for (const key of ["?","F1"]) assert.deepEqual(mapKey({key},castle),{action:"help"});
  assert.deepEqual(mapKey({key:"Tab"},castle,{helpOpen:true}),{action:"all-keys"});
  assert.equal(mapKey({key:"Tab"},castle),null);
  assert.deepEqual(mapKey({key:"Escape"},castle),{action:"close"});
  for (const key of ["m","M"]) assert.deepEqual(mapKey({key},castle),{action:"overview"});
  for (const key of ["ArrowUp","ArrowDown","ArrowLeft","ArrowRight"]) assert.deepEqual(mapKey({key},castle),{action:"camera",direction:key.slice(5).toLowerCase()});
  const approvalState=narrate({approvalRequest:approval,screen:"castle",controlToken:"control"});
  assert.deepEqual(approvalState.keys,["?","F1","Escape","M","ArrowLeft","ArrowRight","ArrowUp","ArrowDown","A","N","V"]);
  assert.ok(!narrate({approvalRequest:approval}).keys.includes("A"));
  assert.equal(mapKey({key:"A"},castle,{controlToken:"control"}),null);
  assert.equal(mapKey({key:"M"},narrate({screen:"idea"})),null);
  assert.equal(mapKey({key:"ArrowLeft"},narrate({screen:"title"})),null);
  assert.equal(mapKey({key:"a"},approvalState,{controlToken:"control",helpOpen:true}),null);
});

test("text_fields_composition_and_modifiers_suppress_every_shortcut", async () => {
  const {narrate,mapKey}=await api(); const state=narrate({approvalRequest:approval,screen:"castle"});
  const keys=["a","n","v","1","2","ArrowUp","ArrowDown","ArrowLeft","ArrowRight","Enter","m","?","F1","Escape","Tab"];
  for (const target of [{tagName:"INPUT"},{tagName:"textarea"},{tagName:"Select"},{isContentEditable:true},{contentEditable:"true"},{contentEditable:"plaintext-only"}]) {
    for (const key of keys) assert.equal(mapKey({key,target},state,{controlToken:"control",helpOpen:true}),null,key);
  }
  for (const flags of [{isComposing:true},{keyCode:229},{ctrlKey:true},{metaKey:true},{altKey:true},{repeat:true}]) for (const key of keys) assert.equal(mapKey({key,...flags},state,{controlToken:"control",helpOpen:true}),null,key);
  assert.deepEqual(mapKey({key:"?",shiftKey:true},state),{action:"help"});
});

test("no_control_message_without_token_and_replay_keys_remain_owned_by_replay", async () => {
  const {narrate,mapKey}=await api();
  for (const source of [{approvalRequest:approval},{studio:{clarify_request:question}},{studio:{pause_request:"boss failed"}}]) {
    const state=narrate(source);
    for (const key of ["a","1","2","3","Enter"]) for (const options of [{},{controlToken:""}]) assert.equal(mapKey({key},state,options)?.control,undefined,key);
    for (const key of ["a","n","v","1","2","3","4","Enter"," ","Space"]) {
      assert.equal(mapKey({key},state,{replay:true,controlToken:"control"}),null,key);
      assert.equal(mapKey({key},narrate({...source,replay:true}),{controlToken:"control"}),null,key);
    }
  }
});

test("projections_are_deterministic_plain_data_and_do_not_mutate_inputs", async () => {
  const {deriveStep,narrate,mapKey}=await api();
  const input=freeze({studio:{phase:"running"},state:{active_room:"T02"},screen:"castle",events:[{type:"action",action:"finish",thought:"Done",args_summary:""},{type:"approval_requested",...approval}],controlToken:"control"});
  assert.deepEqual(deriveStep(input),deriveStep(input)); assert.deepEqual(narrate(input),narrate(input));
  const state=freeze(narrate(input)); const key=freeze({key:"a",target:{tagName:"BUTTON"}});
  const result=mapKey(key,state,{controlToken:"control"}); assert.deepEqual(result,JSON.parse(JSON.stringify(result)));
  assert.deepEqual(state,JSON.parse(JSON.stringify(state)));
  assert.ok(!JSON.stringify(state).includes('"control"'),"The narrator retains no control secret");
  assert.deepEqual(narrate({}).keys,["?","F1","Escape"]);
});

test("advertised_keys_respect_choice_count_control_and_replay_ownership", async () => {
  const {narrate,keysFor}=await api();
  const watch=narrate({studio:{clarify_request:question}});
  assert.ok(!watch.keys.includes("Enter")); assert.ok(!watch.keys.includes("1"));
  assert.ok(watch.keys.includes("ArrowUp"));
  const free=narrate({studio:{clarify_request:{...question,options:[]}},controlToken:"control"});
  assert.ok(!free.keys.includes("ArrowUp")); assert.ok(!free.keys.includes("ArrowDown")); assert.ok(free.keys.includes("Enter"));
  const choice=narrate({studio:{clarify_request:question},controlToken:"control"});
  for (const key of ["1","2","ArrowUp","ArrowDown","Enter"]) assert.ok(choice.keys.includes(key),key);
  assert.deepEqual(keysFor({helpOpen:true}),["?","F1","Escape","Tab"]);
  const replay=narrate({replay:true,screen:"castle"});
  for (const key of ["1","2","4","Space"]) assert.ok(replay.keys.includes(key),key);
});

test("result_lines_preserve_verdicts_commands_exit_codes_and_denial_reasons", async () => {
  const {narrate,renderLine}=await api(); const {createI18n}=await load("i18n/index.js");
  const cases = [
    [{type:"boss_result",area_id:"A1",passed:true},"Boss A1: passed","Chefe A1: aprovado"],
    [{type:"boss_result",area_id:"A1",passed:false},"Boss A1: failed","Chefe A1: reprovado"],
    [{type:"bug_closed",bug_id:"B2",status:"fixed"},"Bug B2: fixed","Bug B2: corrigido"],
    [{type:"gate_result",command:"pytest",exit_code:7,output_summary:"Failed zero"},"pytest · exit 7\nFailed zero","pytest · saída 7\nFailed zero"],
    [{type:"approval_answered",answer:true,responder:"desktop window"},"approved in desktop window","aprovado em janela desktop"],
    [{type:"approval_answered",answer:false,responder:"terminal"},"denied in terminal","negado em terminal"],
    [{type:"approval_answered",answer:false,responder:"desktop window",reason:"Wrong directory"},"denied in desktop window: Wrong directory","negado em janela desktop: Wrong directory"],
    [{type:"boss_scenario",area_id:"A1",scenario:"S2",passed:true,reason:"All covered"},"Boss A1 · S2: passed\nAll covered","Chefe A1 · S2: aprovado\nAll covered"],
    [{type:"boss_scenario",area_id:"A1",scenario:"S2",passed:false,reason:"Missing zero"},"Boss A1 · S2: failed\nMissing zero","Chefe A1 · S2: reprovado\nMissing zero"],
    [{type:"bug_closed",bug_id:"B2",status:"wontfix"},"Bug B2: will not be fixed","Bug B2: não será corrigido"],
    [{type:"run_finished",status:"finished"},"Expedition finished.","Expedição finalizada."],
    [{type:"run_finished",status:"aborted"},"Expedition aborted.","Expedição abortada."],
  ];
  for (const [event,en,pt] of cases) for (const [language,expected] of [["en",en],["pt-BR",pt]]) {
    assert.equal(renderLine(narrate({events:[event]}).line,createI18n({navigator:{language}}).t),expected,event.type);
  }
});

test("pause_reason_templates_and_inert_parameters_follow_existing_wording", async () => {
  const {narrate,renderLine}=await api(); const {createI18n}=await load("i18n/index.js");
  for (const language of ["en","pt-BR"]) {
    const {t}=createI18n({navigator:{language}});
    for (const [reason,key,params] of [["stamina exhausted","pause.stamina",{}],["out of lives","pause.lives",{}],["boss failed","pause.boss",{}],["model chain exhausted","pause.chain",{}],["Out of lives in T03: Missing zero","pause.livesDetail",{room:"T03",feedback:"Missing zero"}],["warrior model chain exhausted; retry or change the chain.","pause.chainDetail",{role:t("role.warrior")}],["Boss A2: Missing zero","pause.bossDetail",{area:"A2",feedback:"Missing zero"}],["New reason","pause.reason",{reason:"New reason"}],["constructor","pause.reason",{reason:"constructor"}]]) {
      assert.equal(renderLine(narrate({events:[{type:"run_paused",reason}]}).line,t),t("play.runPaused",{reason:t(key,params)}));
    }
    const result=narrate({events:[{type:"action",action:"write_file",thought:"<b>{second}</b>",args_summary:"{first}.py"}]});
    const line=renderLine(result.line,t); assert.ok(line.includes("<b>{second}</b>")); assert.ok(line.includes("{first}.py"));
  }
});

test("reconnect_after_spec_written_restores_map_despite_older_recorded_answer", async () => {
  const {deriveStep,narrate,renderLine}=await api(); const {createI18n}=await load("i18n/index.js");
  // Real Studio.snapshot: RunState.feature is set by spec_written; ReplayStatus
  // retains the last clarify_answer, because spec_written is not dialogue there.
  const input={studio:{phase:"replaying",replay:{stream:"plan",dialogue:{type:"clarify_answer",question_id:"q1",answer:"SQLite"}}},state:{status:"pending",feature:"store",active_room:null}};
  assert.equal(deriveStep(input).number,4);
  const output=narrate(input); assert.equal(output.turn,null); assert.equal(output.speaker,"mage");
  assert.equal(renderLine(output.line,createI18n({navigator:{language:"en"}}).t),"Your map is ready: store");
});
