/* SPDX-License-Identifier: Apache-2.0 */
// Optional documentation capture. Uses synthetic data and no running job's screen.
const fs = require('node:fs'), path = require('node:path');
const {chromium} = require(process.env.GPU_LAB_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname,'..');
const now = Date.now()/1000;
const job = (id,label,status,gpu,seconds,extra={}) => ({id,label,status,gpu_slot:gpu,requested_gpu:'auto',created_at:now-seconds-2,started_at:status==='queued'?null:now-seconds,timeout_seconds:300,argv:['example-test'],cwd:'/workspace/demo',workload:'light',vram_mb:512,...extra});
const jobs = [job('0000000000000001','Vulkan render check','running','0',44,{display:':99'}),job('0000000000000002','Godot menu test','running','1',32,{display:':98'}),job('0000000000000003','OpenGL visual test','running','1',92,{display:':108'}),job('0000000000000004','Performance benchmark','queued','auto',15,{workload:'heavy',vram_mb:8192,waiting_reason:'Both GPUs have active jobs. This benchmark will start alone on the first available GPU.'})];
const gpu = (name,load,memory,active) => ({available:true,accepting:true,gpu:{name},load:{utilization_percent:load,memory_used_mib:memory,memory_total_mib:24576},active_jobs:active,queued:[],scheduler:{max_concurrent:3}});
const state = {updated_at:now,mode:'shared-queue',scheduler_version:4,jobs,active_jobs:jobs.slice(0,3).map(j=>j.id),queued:[jobs[3].id],shared_queued:[jobs[3].id],gpus:{'0':gpu('NVIDIA GeForce RTX 3090',42,4096,[jobs[0].id]),'1':gpu('NVIDIA GeForce RTX 3090 Ti',51,6144,jobs.slice(1,3).map(j=>j.id))},history:{}};
for(const slot of ['0','1']) state.history[slot] = Array.from({length:60},(_,i)=>({time:now-120+i*2,utilization:Math.round((slot==='0'?38:49)+Math.sin(i/5)*6)}));
(async()=>{
 const browser = await chromium.launch({headless:true,...(process.env.GPU_LAB_BROWSER ? {executablePath:process.env.GPU_LAB_BROWSER}:{}),args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']});
 try {
  const page = await browser.newPage({viewport:{width:1440,height:1050},reducedMotion:'reduce'});
  await page.route('http://gpu-lab.test/**', async route => {
   const url = new URL(route.request().url());
   if(url.pathname==='/api/status') return route.fulfill({json:{...state,updated_at:Date.now()/1000}});
   if(url.pathname.startsWith('/api/')) return route.fulfill({status:404,json:{error:'Synthetic documentation example'}});
   const file = {'/':'index.html','/app.js':'app.js','/style.css':'style.css','/icon.svg':'icon.svg'}[url.pathname];
   if(!file) return route.fulfill({status:404,body:''});
   const type = {'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml'}[path.extname(file)];
   return route.fulfill({contentType:type,body:fs.readFileSync(path.join(root,'dashboard',file))});
  });
  await page.goto('http://gpu-lab.test/');
  await page.getByRole('button',{name:/Inspect Performance benchmark/}).click();
  await page.locator('#selected-location').filter({hasText:'Awaiting GPU assignment'}).waitFor();
  fs.mkdirSync(path.join(root,'docs/assets'),{recursive:true});
  await page.screenshot({path:path.join(root,'docs/assets/dashboard.png'),fullPage:true});
  console.log('Saved docs/assets/dashboard.png using synthetic data only.');
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
