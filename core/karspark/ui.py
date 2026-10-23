"""/ui: a small live dashboard over /v1/status, /v1/queue and /v1/jobs. The key is kept in the browser's
localStorage and sent as a bearer token; the page itself holds no data."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>karspark</title><style>
:root{--bg:#0f1115;--fg:#e6e6e6;--mut:#8a8f98;--card:#171a21;--acc:#7aa2f7;--ok:#9ece6a;--warn:#e0af68;--bad:#f7768e}
@media (prefers-color-scheme: light){:root{--bg:#f6f7f9;--fg:#1a1b1e;--mut:#5c6370;--card:#fff;--acc:#2f5bd3}}
body{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif;margin:0;padding:16px;max-width:1200px;margin:auto}
h1{font-size:18px;margin:0 0 12px}h2{font-size:14px;color:var(--mut);margin:18px 0 6px;text-transform:uppercase;letter-spacing:.05em}
.card{background:var(--card);border-radius:10px;padding:12px 14px;margin-bottom:10px;overflow-x:auto}
table{border-collapse:collapse;width:100%}td,th{padding:4px 8px;text-align:left;border-bottom:1px solid #8882;white-space:nowrap}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.mut{color:var(--mut)}
button{background:var(--acc);color:#fff;border:0;border-radius:6px;padding:3px 9px;cursor:pointer}
input{background:var(--card);color:var(--fg);border:1px solid #8885;border-radius:6px;padding:5px;width:320px;max-width:100%}
</style></head><body>
<h1>karspark <span id="mode" class="mut"></span></h1>
<div id="login" class="card" style="display:none">API key: <input id="key" type="password"> <button onclick="save()">Use</button></div>
<div id="main"></div>
<script>
const $=s=>document.querySelector(s);let KEY="";try{KEY=localStorage.getItem("ks_key")||""}catch(e){}
function save(){KEY=$("#key").value.trim();try{localStorage.setItem("ks_key",KEY)}catch(e){};tick()}
async function api(p,o={}){const r=await fetch(p,{...o,headers:{Authorization:"Bearer "+KEY,"Content-Type":"application/json"}});
 if(r.status==401){$("#login").style.display="block";throw new Error("key")}return r.json()}
const esc=s=>String(s??"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const cls=s=>({ready:"ok",running:"ok",succeeded:"ok",starting:"warn",queued:"warn",paused:"warn",pausing:"warn",failed:"bad",cancelled:"mut"}[s]||"");
function table(rows,cols){if(!rows.length)return '<span class="mut">none</span>';return "<table><tr>"+cols.map(c=>"<th>"+c[0]+"</th>").join("")+"</tr>"+
 rows.map(r=>"<tr>"+cols.map(c=>"<td>"+c[1](r)+"</td>").join("")+"</tr>").join("")+"</table>"}
async function act(id,a){await api(`/v1/jobs/${id}/${a}`,{method:"POST"});tick()}
async function tick(){if(!KEY){$("#login").style.display="block";return}
 try{const [s,q,j]=await Promise.all([api("/v1/status"),api("/v1/queue"),api("/v1/jobs?mine=false&limit=25")]);
 $("#login").style.display="none";const m=s.mode;
 $("#mode").textContent=`mode ${m.current}${m.switching?" → "+m.switching:""}${m.hold?` (${m.hold.kind} hold ${Math.round(m.hold.ends_in_s/60)} min)`:""}`;
 const r=s.resources||{};
 $("#main").innerHTML=`<h2>Engines</h2><div class=card>${table(s.engines,[["recipe",e=>esc(e.recipe)],["state",e=>`<span class=${cls(e.state)}>${e.state}</span>`],
 ["loaded",e=>esc(e.loaded_model||"")],["load s",e=>e.load_s??""],["GiB",e=>e.memory_gib],["error",e=>`<span class=bad>${esc((e.error||"").slice(0,120))}</span>`]])}
 <div class=mut>memory ${r.mem_used_gib??"?"} / ${r.mem_total_gib??"?"} GiB · GPU ${r.gpu_util??"?"}% · ${r.gpu_temp??"?"}°C · ${r.gpu_power_w??"?"} W</div></div>
 <h2>Text streams</h2><div class=card>${table(s.text_streams,[["project",x=>esc(x.project)],["model",x=>esc(x.model)],["P",x=>x.priority],["s",x=>x.running_s],["chunks",x=>x.tokens]])}</div>
 <h2>Queue</h2><div class=card>${table(q.data,[["#",x=>x.position??""],["engine",x=>esc(x.engine)],["project",x=>esc(x.project)],["type",x=>esc(x.kind)],["model",x=>esc(x.model)],["P",x=>x.priority],["waited s",x=>x.waited_s],["eta s",x=>x.eta_s??""],["why",x=>esc(x.why)]])}</div>
 <h2>Jobs</h2><div class=card>${table(j.data,[["id",x=>`<span class=mut>${x.id}</span>`],["project",x=>esc(x.project)],["type",x=>x.kind],["model",x=>esc(x.model)],
 ["state",x=>`<span class=${cls(x.state)}>${x.state}</span>`],["P",x=>x.priority],["progress",x=>x.progress&&x.progress.step?`${esc(x.progress.stage)} ${x.progress.step}/${x.progress.total}`:""],
 ["run s",x=>Math.round(x.run_s||0)],["",x=>["running","queued"].includes(x.state)?`<button onclick="act('${x.id}','pause')">pause</button> <button onclick="act('${x.id}','cancel')">cancel</button>`:x.state=="paused"?`<button onclick="act('${x.id}','resume')">resume</button>`:(x.outputs||[]).map(o=>esc(o.name)).join(" ")]])}</div>`;
 }catch(e){}}
tick();setInterval(tick,3000);
</script></body></html>"""


@router.get("/ui", response_class=HTMLResponse)
async def ui():
    return PAGE
