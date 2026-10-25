const {EventEmitter} = require('node:events');
const {pathToFileURL} = require('node:url');
const path = require('node:path');
const load = file => import(pathToFileURL(path.resolve(__dirname, '../../src/specvania/ui/static', file)));
const flat = node => [node, ...(node.children || []).flatMap(flat)];
const text = node => flat(node).map(n => n.textContent || '').join('\n');
class Element {
  constructor(tag, document) {Object.assign(this, {tagName:tag, document, children:[], style:{}, dataset:{}, attrs:{}, listeners:{}, value:'', hidden:false, disabled:false, _text:''});}
  set textContent(value) {this._text=String(value); this.replaceChildren();}
  get textContent() {return this._text;}
  append(...nodes) {for(const node of nodes) {node.remove(); node.parent=this; this.children.push(node);}}
  prepend(node) {node.remove(); node.parent=this; this.children.unshift(node);}
  replaceChildren(...nodes) {for(const node of this.children)node.parent=null;this.children=[];this.append(...nodes);}
  remove() {if(this.parent)this.parent.children=this.parent.children.filter(n=>n!==this);this.parent=null;}
  setAttribute(key,value) {this.attrs[key]=String(value);}
  getAttribute(key) {return this.attrs[key] ?? null;}
  removeAttribute(key) {delete this.attrs[key];}
  addEventListener(key,fn) {(this.listeners[key] ||= []).push(fn);}
  removeEventListener(key,fn) {this.listeners[key]=(this.listeners[key]||[]).filter(f=>f!==fn);}
  dispatch(key,event={}) {event.target ||= this;for(const fn of this.listeners[key]||[])fn(event);if(this.parent)this.parent.dispatch(key,event);}
  focus() {this.document.activeElement=this;}
  click() {if(!this.disabled)this.dispatch('click');}
  contains(node) {return flat(this).includes(node);}
}
async function harness({desktop=true, chips=false, approval=true, timing={}}={}) {
  const {setLanguage}=await load('i18n/index.js'); setLanguage('en');
  const document={createElement:tag=>new Element(tag,document)};
  document.body=document.createElement('body');document.head=document.createElement('head');
  for(const id of ['world-map','castle-hud','screen-host']) {const node=document.createElement('section');node.id=id;node.hidden=id==='screen-host';document.body.append(node);}
  document.getElementById=id=>flat(document.body).find(n=>n.id===id) || null;
  const browser=new EventEmitter();browser.document=document;browser.innerWidth=960;browser.innerHeight=540;
  browser.addEventListener=browser.on.bind(browser);browser.removeEventListener=browser.off.bind(browser);browser.dispatchEvent=event=>browser.emit(event.type,event);
  browser.CustomEvent=class {constructor(type,options){Object.assign(this,{type,...options});}};
  const notifications=[];if(desktop)browser.specvaniaDesktop=typeof desktop==='object'?desktop:{controlToken:()=> 'control',notify:(...args)=>notifications.push(args)};
  const socket=new EventEmitter(),sent=[];socket.readyState=1;socket.addEventListener=socket.on.bind(socket);socket.removeEventListener=socket.off.bind(socket);socket.send=data=>sent.push(JSON.parse(data));
  const mounts=[];
  if(approval)mounts.push((await load('hud/approval.js')).mountApproval(socket,{window:browser,document}));
  else mounts.push((await load('hud/dialogue-box.js')).mountDialogueBox(socket,{browser,timing}));
  let chip;if(chips){chip=(await load('hud/chips.js')).mountChips(socket,{browser});mounts.push(chip);}
  const message=value=>{socket.emit('message',{data:JSON.stringify(value)});if(value.kind==='state')browser.dispatchEvent(new browser.CustomEvent('specvania:state',{detail:{...value.state,replay:value.replay}}));if(value.kind==='event')browser.dispatchEvent(new browser.CustomEvent('specvania:event',{detail:value.event}));};
  const key=(key,target=document.activeElement)=>{const event={key,target,preventDefault(){this.defaultPrevented=true;}};target?.dispatch('keydown',event);browser.emit('keydown',event);return event;};
  return {document,doc:document,browser,window:browser,socket,sent,notifications,chip,body:document.body,head:document.head,
    get box(){return document.getElementById('dialogue-box');},message,event:event=>message({kind:'event',event}),close(){socket.readyState=3;socket.emit('close');},key,
    button:label=>flat(document.body).find(n=>n.tagName==='button'&&n.textContent===label),
    destroy(){for(const mount of mounts.reverse())mount.destroy();},get input(){return flat(document.body).find(n=>n.id==='approval-denial-reason');}};
}
module.exports={Element,load,flat,text,harness};
