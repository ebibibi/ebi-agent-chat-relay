// The default picker names where each backend runs (subscription vs Foundry), the nav line
// always shows it, and choosing a signed-out subscription opens the sign-in dialog.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../claude_discord/console/static');
const conns=[
 {backend:'codex',id:'subscription',kind:'subscription',label:'ChatGPT subscription',detail:null,default:false,model:null},
 {backend:'codex',id:'foundry',kind:'foundry',label:'Personal Foundry',detail:'aip.openai.azure.com',default:true,model:'gpt-5.4-mini'}];
for(const [width,height] of [[1280,800],[390,844]])test(`connection picker ${width}x${height}`,async t=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});t.after(()=>browser.close());
 const page=await browser.newPage({viewport:{width,height}});page.setDefaultTimeout(4000);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));t.after(()=>assert.deepEqual(errors,[]));
 let current='foundry';const posts=[];
 const catalog=()=>({backend:'codex',model:current==='foundry'?'gpt-5.4-mini':'gpt-5.5',effort:'medium',
  connection:conns.find(c=>c.id===current),connections:conns,backends:['claude','codex'],
  models:current==='foundry'?[{value:'gpt-5.4-mini',label:''}]:[{value:'gpt-5.5',label:''}],efforts:['low','medium','high'],commands:[],builtins:[],skills:[]});
 await page.route('http://console.test/**',async route=>{
  const req=route.request();const name=new URL(req.url()).pathname;
  if(name==='/'||name.startsWith('/static/')){const file=name==='/'?'index.html':path.basename(name);return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});}
  if(name==='/console/api/defaults'&&req.method()==='POST'){const b=JSON.parse(req.postData());posts.push(b);if(b.name==='connection')current=b.arg;
   return route.fulfill({contentType:'application/json',body:JSON.stringify({message:'ok',catalog:catalog()})});}
  if(name==='/console/api/defaults')return route.fulfill({contentType:'application/json',body:JSON.stringify(catalog())});
  if(name==='/console/api/cli-login')return route.fulfill({contentType:'application/json',body:JSON.stringify({backends:[{backend:'codex',logged_in:false,method:null,flow:null}]})});
  if(name==='/console/api/cli-login/codex')return route.fulfill({contentType:'application/json',body:JSON.stringify({flow:{backend:'codex',state:'waiting',url:'https://auth.openai.com/codex/device',user_code:'AB12-CD34'}})});
  const data=name.endsWith('/board')?{items:[],slots:{},places:[]}:name.endsWith('/usage')?{now:Math.floor(Date.now()/1000),backends:[]}:{};
  return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
 });
 await page.goto('http://console.test/?lang=en');
 const nav=page.locator('.nav-backend').first();
 await nav.waitFor({state:'attached'});
 assert.match(await nav.textContent(),/Codex · ☁️ Foundry · gpt-5\.4-mini · medium/);
 await nav.evaluate(b=>b.click());
 const chips=page.locator('.backend-picker .bp-conns .chip');
 await chips.first().waitFor();
 assert.equal(await chips.count(),2);
 assert.equal(await chips.nth(1).getAttribute('aria-pressed'),'true');
 assert.match(await chips.nth(1).textContent(),/Personal Foundry/);
 assert.match(await chips.nth(0).textContent(),/ChatGPT subscription · 🔑 not signed in/);
 await chips.nth(0).click();
 await page.locator('dialog.cl-dialog[open] .cl-code code').waitFor();
 assert.deepEqual(posts,[{name:'connection',arg:'subscription'}]);
 assert.match(await nav.textContent(),/Codex · 💳 Subscription · gpt-5\.5/);
});
