"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const modulePath = pathToFileURL(path.resolve(__dirname, "../../src/specvania/ui/static/hud/party.js"));

test("HUD stamina uses run tokens and budget and ignores cooldown recovery", async () => {
  const { partyFromState, projectParty, partyView } = await import(modulePath);
  let state = partyFromState({token_budget: 200000, tokens_spent: 100000,
    tokens_spent_by_role: {mage: 10000, warrior: 90000, paladin: 0}});
  assert.equal(partyView(state).stamina, 50);
  assert.equal(partyView(state).staminaText, "100000 / 200000 tokens");
  assert.equal(partyView(state).members[1].tokens_spent, 90000);
  state = projectParty(state, {type:"cooldown",role:"warrior",model:"shared",seconds:10}, 0);
  assert.equal(partyView(state, 0).stamina, 50);
  assert.equal(partyView(state, 5000).stamina, 50);
  state = projectParty(state, {type:"model_call",role:"paladin",model:"shared",tokens_in:40000,tokens_out:60000});
  assert.equal(partyView(state).stamina, 0);
  assert.equal(partyView(state).members[2].tokens_spent, 100000);
  state = projectParty(state, {type:"stamina_restored",token_budget:250000});
  assert.equal(partyView(state).stamina, 20);
  assert.equal(partyView(state).staminaText, "200000 / 250000 tokens");
});

test("HUD old streams and plan show only tokens and run_started resets spending", async () => {
  const { partyFromState, projectParty, partyView } = await import(modulePath);
  let state = partyFromState({models:[{model:"shared",tokens_in:20,tokens_out:30}]});
  assert.equal(partyView(state).staminaText, "50 tokens");
  state = projectParty(state, {type:"run_started",token_budget:200000});
  assert.equal(partyView(state).staminaText, "0 / 200000 tokens");
  state = projectParty(state, {type:"model_call",role:"mage",model:"shared",tokens_in:300000,tokens_out:0});
  assert.equal(partyView(state).stamina, 0);
  assert.equal(partyView(state).staminaText, "300000 / 200000 tokens");
});


test("model_requested changes acting to thinking and preserves spending and coins", async () => {
  const { partyFromState, projectParty, partyView, eventStatuses } = await import(modulePath);
  for (const role of ["mage", "warrior", "paladin"]) {
    let state = partyFromState({token_budget: 1000});
    state = projectParty(state, {type:"model_call",role:"warrior",model:"old",tokens_in:10,tokens_out:20});
    state = projectParty(state, {type:"action",room_id:"T01",action:"read_file"});
    assert.equal(partyView(state).members[1].status, "acting");
    const before = state;
    state = projectParty(state, {type:"model_requested",role,model:"next",room_id:"T01"});
    assert.deepEqual(eventStatuses({type:"model_requested",role}), {[role]: "thinking"});
    const member = partyView(state).members.find(member => member.role === role);
    assert.equal(member.status, "thinking"); assert.equal(member.model, "next");
    assert.equal(state.tokens_spent, 30); assert.deepEqual(state.models, before.models);
    assert.equal(state.members[1].tokens_spent, 30);
    const snapshot = partyFromState({party:[{role,model:"next",status:"thinking"}],tokens_spent:30});
    assert.equal(partyView(snapshot).members.find(member => member.role === role).status, member.status);
  }
});
