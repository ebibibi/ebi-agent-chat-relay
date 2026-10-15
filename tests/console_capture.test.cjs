const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
function setup() {
  const source = fs.readFileSync('claude_discord/console/static/app.js','utf8');
  const begin = source.indexOf('// Capture submission') >= 0 ? source.indexOf('// Capture submission') : source.indexOf('async function capture(text)');
  const code = source.slice(begin, source.indexOf('// The capture box', begin));
  let release, calls = 0;
  const form = {setAttribute(k,v) {this[k]=v;},querySelectorAll:() => []};
  const elements = {'capture':form,'capture-input':{},'capture-status':{textContent:''}};
  const ctx = {window:{ConsolePaste:{ready:()=>[],clear:()=>{},prepareText:() => new Promise(r => {release=()=>r({text:'task',files:[]});})}},
    $:id=>elements[id],CAPTURE:'__capture__',autostart:()=>true,T:{capturing:'Adding…',paste_wait:'Uploading'},
    api:async()=>{calls++;return {item:{id:'c1'}};},refresh:async()=>{},state:{view:'todo'},setView:()=>{},renderList:()=>{},revealSelection:()=>{},toast:()=>{}};
  vm.createContext(ctx); vm.runInContext(code,ctx);
  return {ctx,elements,calls:()=>calls,release:()=>release()};
}
test('capture blocks repeated submissions before upload and API response finish',async()=>{
  const f=setup();
  const first=f.ctx.capture('task');
  const second=f.ctx.capture('task');
  f.release();
  assert.equal(await second,false);
  assert.equal(await first,true);
  assert.equal(f.calls(),1);
});
test('capture immediately announces pending state and releases controls on completion',async()=>{
  const f=setup(); const promise=f.ctx.capture('task');
  assert.equal(f.elements.capture['aria-busy'],'true');
  assert.equal(f.elements['capture-input'].readOnly,true);
  assert.equal(f.elements['capture-status'].textContent,'Adding…');
  f.release(); await promise;
  assert.equal(f.elements.capture['aria-busy'],'false');
  assert.equal(f.elements['capture-input'].readOnly,false);
  assert.equal(f.elements['capture-status'].textContent,'');
});
test('upload error unlocks submission and retains attachments for a retry',async()=>{
  const f=setup(); let cleared=0;
  f.ctx.window.ConsolePaste.clear=()=>cleared++;
  f.ctx.window.ConsolePaste.prepareText=async()=>{throw new Error('upload failed');};
  assert.equal(await f.ctx.capture('task'),false);
  assert.equal(cleared,0);
  assert.equal(f.elements['capture-input'].readOnly,false);
  f.ctx.window.ConsolePaste.prepareText=async()=>({text:'task',files:[]});
  assert.equal(await f.ctx.capture('task'),true);
  assert.equal(f.calls(),1);
});
test('an accepted capture is not offered for retry when a subsequent render fails',async()=>{
  const f=setup();
  f.ctx.renderList=()=>{throw new Error('render failed');};
  const promise=f.ctx.capture('task'); f.release();
  assert.equal(await promise,true);
  assert.equal(f.calls(),1);
});
test('attachments still uploading do not submit or leave the form locked',async()=>{
  const f=setup(); f.ctx.window.ConsolePaste.ready=()=>null;
  assert.equal(await f.ctx.capture('task'),false);
  assert.equal(f.calls(),0);
  assert.equal(f.elements['capture-input'].readOnly,false);
});
test('a rejected request releases controls and keeps attachments',async()=>{
  const f=setup(); let cleared=0;
  f.ctx.window.ConsolePaste.clear=()=>cleared++;
  f.ctx.api=async()=>{throw new Error('503');};
  const promise=f.ctx.capture('task'); f.release();
  assert.equal(await promise,false);
  assert.equal(cleared,0);
  assert.equal(f.elements['capture-input'].readOnly,false);
});
