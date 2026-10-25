"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {EventEmitter} = require("node:events");
const {createApiKeyStore, launchStudio} = require("../../apps/desktop/studio.cjs");
const key = "fake-T50-app-key";
function storage(directory, options = {}) {
  const calls = [];
  const ciphertext = Buffer.from([0x91, 0x80, 0x75, 0x10]);
  const safeStorage = {
    isEncryptionAvailable: () => true,
    encryptString(value) {calls.push(["encrypt", value]);return ciphertext;},
    decryptString(value) {assert.deepEqual(value, ciphertext);calls.push(["decrypt"]);return key;},
    ...options,
  };
  return {store:createApiKeyStore({userData:directory, safeStorage}), calls, ciphertext};
}
test("api_key_ciphertext_atomic_private_storage_survives_reopen_status_never_decrypts", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "sv-t50-"));
  try {
    const h = storage(directory);
    assert.deepEqual(h.store.status(), {stored:false, encryption:true});
    h.store.set(` ${key}\n`);
    assert.deepEqual(h.calls, [["encrypt", key]]);
    const files = fs.readdirSync(directory);assert.equal(files.length, 1);
    assert.deepEqual(fs.readFileSync(path.join(directory, files[0])), h.ciphertext);
    assert.equal(fs.statSync(path.join(directory, files[0])).mode & 0o777, 0o600);
    assert.ok(!fs.readFileSync(path.join(directory, files[0])).includes(key));
    const reopened = storage(directory);
    assert.deepEqual(reopened.store.status(), {stored:true, encryption:true});
    assert.deepEqual(reopened.calls, []);
    assert.equal(reopened.store.forSpawn(), key);
    reopened.store.remove();assert.deepEqual(reopened.store.status(), {stored:false, encryption:true});
    assert.deepEqual(fs.readdirSync(directory), []);
  } finally {fs.rmSync(directory, {recursive:true, force:true});}
});
test("api_key_refuses_unencrypted_backends_and_invalid_values_without_writes_or_echo", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "sv-t50-"));
  try {
    for (const options of [{isEncryptionAvailable:()=>false}, {getSelectedStorageBackend:()=>"basic_text"}]) {
      const h = storage(directory, options);
      assert.equal(h.store.status().encryption, false);
      assert.throws(()=>h.store.set(key), /apiKey.encryptionUnavailable/);
      assert.equal(h.store.forSpawn(), "");assert.deepEqual(h.calls, []);
    }
    const h = storage(directory);
    for (const value of ["", "   ", "two words", "tab\tinside", "line\ninside", "é", "\0", "\x7f", "x".repeat(513), null, 42, {}]) {
      assert.throws(()=>h.store.set(value), /apiKey.invalid/);
    }
    assert.equal(h.calls.length, 0);assert.deepEqual(fs.readdirSync(directory), []);
    h.store.set("x".repeat(512));assert.equal(h.calls.length, 1);
  } finally {fs.rmSync(directory, {recursive:true, force:true});}
});
test("api_key_reaches_only_child_env_and_split_stderr_never_leaks_to_logs_or_renderer", () => {
  const child = new EventEmitter();child.stdout = new EventEmitter();child.stderr = new EventEmitter();child.kill=()=>{};
  const spawns=[],logs=[],payloads=[];let decrypted=0;
  launchStudio({spawn:(...args)=>{spawns.push(args);return child;},env:{SHELL:"/bin/bash", NVIDIA_API_KEY:"fake-shell-key"},
    apiKeyForSpawn:()=>{decrypted++;return key;},timer:{setTimeout:()=>1,clearTimeout(){}},
    logPath:"/safe/studio.log",log:data=>logs.push(String(data)),open:(...args)=>payloads.push(args),error:(...args)=>payloads.push(args)});
  assert.equal(decrypted, 1);assert.equal(spawns[0][2].env.SPECVANIA_API_KEY, key);
  assert.ok(!JSON.stringify(spawns[0].slice(0,2)).includes(key));
  child.stderr.emit("data", `bad ${key.slice(0,7)}`);child.stderr.emit("data", `${key.slice(7)} and fake-shell-key\n`);
  child.stdout.emit("data", JSON.stringify({port:8765,view_token:"fake-view",control_token:"fake-control"})+"\n");
  child.emit("exit", 1);
  assert.match(logs.join(""), /redacted/);
  for (const secret of [key, "fake-shell-key"]) assert.ok(!JSON.stringify({logs,payloads}).includes(secret));
});

const vm = require("node:vm");
const desktop = path.resolve(__dirname, "../../apps/desktop");
async function mainHarness(directory, safeOptions = {}) {
  const children=[],pages=[],logs=[],ipc=new EventEmitter(),handlers=new Map(),shutdown=[];
  ipc.handle=(name,fn)=>handlers.set(name,fn);
  const app=new EventEmitter();app.whenReady=()=>Promise.resolve();app.getPath=()=>directory;app.getLocale=()=>"en";app.quit=()=>{};app.exit=()=>assert.fail("desktop failed");
  let window,storedValue="";
  class BrowserWindow extends EventEmitter {
    constructor(){super();window=this;this.webContents=new EventEmitter();this.webContents.setWindowOpenHandler=()=>{};this.webContents.getZoomLevel=()=>0;this.webContents.setZoomLevel=()=>{};}
    loadURL(url){pages.push(url);return Promise.resolve();}
  }
  const studio=require("../../apps/desktop/studio.cjs");
  const safeStorage={isEncryptionAvailable:()=>true,encryptString:value=>{storedValue=value;return Buffer.from("ciphertext");},decryptString:()=>storedValue,...safeOptions};
  const spawns=[];
  vm.runInNewContext(fs.readFileSync(path.join(desktop,"main.js"),"utf8"),{__dirname:desktop,URL,Buffer,console,process:{argv:["electron","."],env:{SHELL:"/bin/bash"}},
    require(name){
      if(name==="electron")return {app,BrowserWindow,ipcMain:ipc,safeStorage,Notification:{},shell:{},screen:{getAllDisplays:()=>[]},Menu:{buildFromTemplate:v=>v,setApplicationMenu(){}}};
      if(name==="node:child_process")return {spawn:(...args)=>{spawns.push(args);const child=new EventEmitter();child.stdout=new EventEmitter();child.stderr=new EventEmitter();child.kill=()=>child.emit("exit",0);children.push(child);return child;}};
      if(name==="node:fs")return {...fs,createWriteStream:()=>{const stream=new EventEmitter();stream.write=data=>logs.push(String(data));stream.end=()=>{};return stream;}};
      if(name.endsWith("studio.cjs"))return {...studio,
        launchStudio:options=>studio.launchStudio({...options,env:{SHELL:"/bin/bash"},timer:{setTimeout:()=>1,clearTimeout(){}}}),
        connectControl:()=>{const child=children.at(-1);const socket=new EventEmitter();socket.close=()=>{};socket.send=data=>{shutdown.push(data);child.emit("exit",0);};setImmediate(()=>socket.emit("open"));return socket;}};
      return require(name);
    }});
  await new Promise(resolve=>setImmediate(resolve));
  const ready=()=>children.at(-1).stdout.emit("data", JSON.stringify({port:8765,view_token:"view",control_token:"control"})+"\n");
  const event=()=>({sender:window.webContents,senderFrame:{url:pages.at(-1)}});
  return {children,pages,logs,spawns,shutdown,handlers,ready,event,ipc};
}
test("main_api_key_set_replace_remove_restart_studio_and_keep_all_public_payloads_secret_free",async()=>{
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),"sv-t50-main-"));
  try {
    const h=await mainHarness(directory);h.ready();
    const set=h.handlers.get("specvania:api-key-set"),remove=h.handlers.get("specvania:api-key-remove"),status=h.handlers.get("specvania:api-key-status");
    assert.equal(typeof set,"function");
    const payloads=[];
    for(const value of [key,"fake-T50-replacement"]) {
      payloads.push(await set(h.event(),value));assert.equal(payloads.at(-1).ok,true);
      assert.equal(h.spawns.at(-1)[2].env.SPECVANIA_API_KEY,value);
      h.ready();payloads.push(await status(h.event()));
      assert.deepEqual(JSON.parse(JSON.stringify(payloads.at(-1))),{stored:true,encryption:true});
      assert.ok(!JSON.stringify(h.spawns.at(-1).slice(0,2)).includes(value));
    }
    payloads.push(await remove(h.event()));h.ready();
    assert.equal(h.spawns.length,4);assert.equal(h.shutdown.length,3);
    assert.equal(h.spawns.at(-1)[2].env.SPECVANIA_API_KEY,undefined);
    const foreign={sender:h.event().sender,senderFrame:{url:"https://untrusted.invalid"}};
    for(const name of ["set","remove","status"]) assert.throws(()=>h.handlers.get(`specvania:api-key-${name}`)(foreign,key),/Untrusted/);
    await h.handlers.get("specvania:settings-set")(h.event(),{language:"en"});
    const publicData=JSON.stringify({payloads,pages:h.pages,logs:h.logs,shutdown:h.shutdown,settings:fs.readFileSync(path.join(directory,"settings.json"),"utf8")});
    for(const secret of [key,"fake-T50-replacement"])assert.ok(!publicData.includes(secret));
    h.children.at(-1).emit("exit",0);
  }finally{fs.rmSync(directory,{recursive:true,force:true});}
});
test("main_refuses_unavailable_encryption_with_translation_key_and_keeps_shell_studio",async()=>{
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),"sv-t50-main-"));
  try{
    const h=await mainHarness(directory,{isEncryptionAvailable:()=>false});h.ready();
    const result=await h.handlers.get("specvania:api-key-set")(h.event(),key);
    assert.equal(result.ok,false);assert.equal(result.reason,"apiKey.encryptionUnavailable");
    assert.equal(h.spawns.length,1);assert.ok(!JSON.stringify(result).includes(key));h.children.at(-1).emit("exit",0);
  }finally{fs.rmSync(directory,{recursive:true,force:true});}
});

test("api_key_atomic_write_failure_preserves_old_ciphertext_cleans_temp_and_never_echoes_fs_errors",()=>{
  const files=new Map([["/private-data/api-key.enc", Buffer.from("old-ciphertext")]]),writes=[],renames=[];
  const fakeFs={
    existsSync:file=>files.has(file),mkdirSync(){},
    writeFileSync(file,value,options){assert.equal(files.has(file),false);files.set(file,value);writes.push({file,value,options});},
    renameSync(from,to){renames.push([from,to]);throw new Error(key);},
    unlinkSync:file=>files.delete(file),readFileSync:file=>files.get(file),
  };
  const store=createApiKeyStore({userData:"/private-data",fs:fakeFs,safeStorage:{isEncryptionAvailable:()=>true,encryptString:()=>Buffer.from("new-ciphertext")}});
  assert.throws(()=>store.set(key),error=>error.message==="apiKey.storeFailed" && !error.message.includes(key));
  assert.equal(writes.length,1);assert.equal(writes[0].options.mode,0o600);assert.equal(writes[0].options.flag,"wx");
  assert.equal(renames.length,1);assert.equal(files.size,1);assert.equal(files.get("/private-data/api-key.enc").toString(),"old-ciphertext");
  assert.ok(!JSON.stringify([...files.values()]).includes(key));
});
test("api_key_decryption_failure_prevents_spawn_and_never_reflects_underlying_error",()=>{
  const errors=[];
  const lifecycle=launchStudio({spawn:()=>assert.fail("must not spawn without decrypting"),env:{},timer:{clearTimeout(){}},logPath:"/safe/studio.log",log:()=>assert.fail("no key diagnostics"),
    apiKeyForSpawn:()=>{throw new Error(key);},open:()=>assert.fail("must not open"),error:(...args)=>errors.push(args)});
  assert.equal(lifecycle.child,null);assert.equal(errors[0][1].reasonKey,"apiKey.readFailed");assert.ok(!JSON.stringify(errors).includes(key));
});

test("api_key_escaped_stderr_and_secret_bearing_handshake_fail_closed",()=>{
  const secret='fake-T50-"quoted\\key';
  const child=new EventEmitter();child.stdout=new EventEmitter();child.stderr=new EventEmitter();child.kill=()=>{};
  const logs=[],opened=[],errors=[];
  launchStudio({spawn:()=>child,env:{},timer:{setTimeout:()=>1,clearTimeout(){}},apiKeyForSpawn:()=>secret,
    logPath:"/safe/studio.log",log:value=>logs.push(String(value)),open:(...args)=>opened.push(args),error:(...args)=>errors.push(args)});
  child.stderr.emit("data",JSON.stringify({error:secret})+"\n");
  child.stdout.emit("data",JSON.stringify({port:8765,view_token:secret,control_token:"control"})+"\n");
  assert.equal(opened.length,0);assert.equal(errors[0][1].reasonKey,"error.startInvalid");
  assert.ok(!logs.join("").includes(JSON.stringify(secret).slice(1,-1)));
});
