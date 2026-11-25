const WS_URL="ws://127.0.0.1:9222/devtools/page/CC2AD3F3BD391DC421B057553EFAC0D1";
const ws=new WebSocket(WS_URL);
let id=0; const pending={}; const netLog=[];
function send(method,params){return new Promise((res,rej)=>{const i=++id;pending[i]={res,rej};ws.send(JSON.stringify({id:i,method,params:params||{}}));});}
ws.onmessage=ev=>{const m=JSON.parse(ev.data);if(m.id&&pending[m.id]){pending[m.id].res(m);delete pending[m.id];}
  if(m.method==="Network.requestWillBeSent" && (m.params.request.url.includes("/gateway/")||m.params.request.url.includes("/saas-"))){
    netLog.push({m:"req",url:m.params.request.url,method:m.params.request.method,type:m.params.type});
  }
  if(m.method==="Network.responseReceived" && m.params.response){
    const u=m.params.response.url||"";
    if(u.includes("/gateway/")||u.includes("/saas-")) netLog.push({m:"res",url:u,status:m.params.response.status,mime:m.params.response.mimeType});
  }
};
ws.onopen=async()=>{
  await send("Runtime.enable");
  await send("Network.enable");
  // Wait for page to settle and collect any pending API calls
  await new Promise(r=>setTimeout(r,3000));
  console.log("=== captured network ("+netLog.length+") ===");
  netLog.forEach(n=>console.log(JSON.stringify(n)));
  // Now dump the page DOM structure: tabs, buttons, forms, tables columns, filters
  const r=await send("Runtime.evaluate",{expression:`(function(){
    const tabs=[...document.querySelectorAll('.tab-item, .el-tabs__item, [role=tab]')].map(e=>e.textContent.trim()).filter(Boolean);
    const buttons=[...document.querySelectorAll('button, [role=button]')].map(b=>b.textContent.trim()).filter(Boolean);
    const formLabels=[...document.querySelectorAll('.el-form-item__label, label')].map(e=>e.textContent.trim()).filter(Boolean);
    const tableHeaders=[...document.querySelectorAll('th, .el-table th')].map(e=>e.textContent.trim()).filter(Boolean);
    const placeholders=[...document.querySelectorAll('input')].map(i=>i.placeholder).filter(Boolean);
    const emptyStates=[...document.querySelectorAll('[class*=empty]')].map(e=>e.textContent.trim().slice(0,40)).filter(Boolean);
    const selects=[...document.querySelectorAll('.el-select')].map(e=>e.textContent.trim().slice(0,30)).filter(Boolean);
    const bodyText=document.body.innerText.slice(0,400);
    return JSON.stringify({url:location.href,tabs,buttons,formLabels,tableHeaders,placeholders,emptyStates,selects,bodyText});
  })()`,returnByValue:true});
  console.log("\n=== PAGE DOM ===");
  console.log(r.result?.result?.value);
  ws.close();
};
setTimeout(()=>{console.log("timeout");process.exit(1);},20000);
