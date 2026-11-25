const WS_URL="ws://127.0.0.1:9222/devtools/page/CC2AD3F3BD391DC421B057553EFAC0D1";
const ws=new WebSocket(WS_URL);
let id=0; const pending={}; const netLog=[];
function send(method,params){return new Promise((res,rej)=>{const i=++id;pending[i]={res,rej};ws.send(JSON.stringify({id:i,method,params:params||{}}));});}
ws.onmessage=ev=>{const m=JSON.parse(ev.data);if(m.id&&pending[m.id]){pending[m.id].res(m);delete pending[m.id];}
  if(m.method==="Network.responseReceived" && m.params.response){
    const u=m.params.response.url||""; if(u.includes("/gateway/")) netLog.push({url:u,status:m.params.response.status});
  }
};
ws.onopen=async()=>{
  await send("Runtime.enable");
  await send("Network.enable");
  const out={};
  const routes=["receptionRecord","records","inquiry","preInquiryManagement","orders","cloudPharmacyOrder","reports","performance","templateManagement","EmpiricalRecipels","materialSms","account","strategySettings","electronicWallet","invoices","printTemplate"];
  for(const route of routes){
    netLog.length=0;
    const r=await send("Runtime.evaluate",{expression:`location.hash='#/simpleness/${route}';'nav'`,returnByValue:true});
    await new Promise(r=>setTimeout(r,2500));
    const r2=await send("Runtime.evaluate",{expression:`(function(){
      const tabs=[...document.querySelectorAll('.tab-item, .el-tabs__item, [role=tab]')].map(e=>e.textContent.trim()).filter(Boolean);
      const buttons=[...document.querySelectorAll('button, [role=button]')].map(b=>b.textContent.trim()).filter(Boolean);
      const formLabels=[...document.querySelectorAll('.el-form-item__label, label')].map(e=>e.textContent.trim()).filter(Boolean);
      const th=[...document.querySelectorAll('th, .el-table th')].map(e=>e.textContent.trim()).filter(Boolean);
      const ph=[...document.querySelectorAll('input')].map(i=>i.placeholder).filter(Boolean);
      const empty=[...document.querySelectorAll('[class*=empty]')].map(e=>e.textContent.trim().slice(0,30)).filter(Boolean);
      const sel=[...document.querySelectorAll('.el-select')].map(e=>e.textContent.trim().slice(0,20)).filter(Boolean);
      return JSON.stringify({url:location.href,tabs,buttons,formLabels,tableHeaders:th,placeholders:ph,emptyStates:empty,selects:sel,body:document.body.innerText.slice(0,300)});
    })()`,returnByValue:true});
    out[route]=JSON.parse(r2.result?.result?.value||"{}");
    out[route].netReqs=netLog.map(n=>n.url.replace("https://saas-pro.igancao.cn/gateway",""));
    console.log("### "+route+": "+JSON.stringify(out[route]).slice(0,600));
  }
  // save full
  require("fs").writeFileSync("/tmp/hisrev/crawl_simpleness.json",JSON.stringify(out,null,2));
  console.log("\n=== saved to /tmp/hisrev/crawl_simpleness.json ===");
  ws.close();
};
setTimeout(()=>{console.log("timeout");process.exit(1);},120000);
