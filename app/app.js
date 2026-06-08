/* faultline app spine — shared by every app/*.html.
   Exposes window.fl: client, auth gate, layout inject, project context,
   data helpers, and render utils. Load order in each page:
     supabase-js CDN  →  /supabase-config.js  →  /app/app.js  →  page <script>
*/
(function () {
  "use strict";
  var URL = window.SUPABASE_URL, KEY = window.SUPABASE_ANON_KEY;
  var configured = !!(URL && KEY && URL.indexOf("YOUR_") === -1 && window.supabase);
  var sb = configured ? window.supabase.createClient(URL, KEY) : null;

  var ctx = { session: null, user: null, orgId: null, orgs: [], projects: [], project: null };

  // ── utils ────────────────────────────────────────────────────────────────
  function esc(s){ return (s==null?"":String(s)).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];}); }
  function rel(ts){ if(!ts) return "—"; var s=Math.max(1,(Date.now()-new Date(ts).getTime())/1000);
    if(s<60) return Math.floor(s)+"s ago"; var m=s/60; if(m<60) return Math.floor(m)+"m ago";
    var h=m/60; if(h<24) return Math.floor(h)+"h ago"; var d=h/24; if(d<30) return Math.floor(d)+"d ago";
    return new Date(ts).toLocaleDateString(); }
  function verdictClass(v){ v=(v||"").toLowerCase(); return v==="fail"?"fail":v==="crash"?"crash":v==="inconclusive"?"inconclusive":"pass"; }
  function verdictLabel(v){ v=(v||"").toLowerCase(); return v==="fail"?"SILENT-WRONG":v==="crash"?"Crash":v==="inconclusive"?"Inconclusive":"Pass"; }
  function runVerdict(r){ if(r.silent_count>0) return "fail"; if(r.crash_count>0) return "crash"; if(r.faults_total===0) return "inconclusive"; return "pass"; }
  function agentColor(name){ var c=["blue","indigo","cyan","amber"]; var h=0; for(var i=0;i<(name||"").length;i++) h=(h*31+name.charCodeAt(i))>>>0; return c[h%4]; }
  function ring(pct, size){ size=size||72; var r=(size/2)-7, c=2*Math.PI*r, on=Math.max(0,Math.min(100,pct))/100*c;
    return '<svg width="'+size+'" height="'+size+'" viewBox="0 0 '+size+' '+size+'">'+
      '<defs><linearGradient id="rg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#38BDF8"/><stop offset="1" stop-color="#3B82F6"/></linearGradient></defs>'+
      '<circle cx="'+size/2+'" cy="'+size/2+'" r="'+r+'" fill="none" stroke="#1B1E27" stroke-width="5"/>'+
      '<circle cx="'+size/2+'" cy="'+size/2+'" r="'+r+'" fill="none" stroke="url(#rg)" stroke-width="5" stroke-linecap="round" stroke-dasharray="'+on+' '+c+'" transform="rotate(-90 '+size/2+' '+size/2+')" style="filter:drop-shadow(0 0 4px rgba(56,189,248,.5))"/></svg>'; }
  function sparkline(vals, color, w, h){ color=color||"#38BDF8"; w=w||62; h=h||26; if(!vals||!vals.length) return "";
    var mn=Math.min.apply(null,vals), mx=Math.max.apply(null,vals), rng=(mx-mn)||1;
    var pts=vals.map(function(v,i){ return [ (i/(vals.length-1))*(w-2)+1, h-2-((v-mn)/rng)*(h-6) ]; });
    var d=pts.map(function(p,i){ return (i?"L":"M")+p[0].toFixed(1)+" "+p[1].toFixed(1); }).join(" ");
    return '<svg class="spark" viewBox="0 0 '+w+' '+h+'" fill="none"><path d="'+d+'" stroke="'+color+'" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>'; }
  function toast(msg, kind){ var t=document.createElement("div"); t.className="toast"; t.innerHTML=(kind==="warn"?"⚠ ":"")+esc(msg);
    document.body.appendChild(t); requestAnimationFrame(function(){ t.classList.add("show"); });
    setTimeout(function(){ t.classList.remove("show"); setTimeout(function(){ t.remove(); },400); }, 3200); }
  function copy(text, btn){ navigator.clipboard && navigator.clipboard.writeText(text); if(btn){ var o=btn.textContent; btn.textContent="Copied"; setTimeout(function(){btn.textContent=o;},1400);} }
  function qs(k){ return new URLSearchParams(location.search).get(k); }

  // ── layout injection ──────────────────────────────────────────────────────
  var LAYOUT = `<aside class="sidebar">
  <a class="sb-logo" href="overview.html" title="Dashboard home"><svg width="26" height="18" viewBox="0 0 40 28" fill="none"><path d="M3 10H17" stroke="#E6EBF2" stroke-width="3.2" stroke-linecap="round"/><path d="M17 10L23 18" stroke="#38BDF8" stroke-width="3.2" stroke-linecap="round"/><path d="M23 18H37" stroke="#E6EBF2" stroke-width="3.2" stroke-linecap="round"/></svg><span class="sb-word">fault<b>line</b></span></a>
  <nav class="sb-nav">
    <div class="nav-item" data-nav="overview" data-href="overview.html"><svg viewBox="0 0 15 15" fill="none"><rect x="2" y="2" width="4.5" height="4.5" rx="1" stroke="currentColor" stroke-width="1.2"/><rect x="8.5" y="2" width="4.5" height="4.5" rx="1" stroke="currentColor" stroke-width="1.2"/><rect x="2" y="8.5" width="4.5" height="4.5" rx="1" stroke="currentColor" stroke-width="1.2"/><rect x="8.5" y="8.5" width="4.5" height="4.5" rx="1" stroke="currentColor" stroke-width="1.2"/></svg> Overview</div>
    <div class="nav-item" data-nav="runs" data-href="runs.html"><svg viewBox="0 0 15 15" fill="none"><path d="M2 11L5 8L7.5 10.5L11 6L13 8" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/><rect x="1.5" y="2" width="12" height="11" rx="1.5" stroke="currentColor" stroke-width="1.2"/></svg> Runs</div>
    <div class="nav-item" data-nav="regressions" data-href="regressions.html"><svg viewBox="0 0 15 15" fill="none"><path d="M2 10l3.5-4.5 3 3L11 5l2 2" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/><rect x="1.5" y="2" width="12" height="11" rx="1.5" stroke="currentColor" stroke-width="1.2"/></svg> Regressions <span class="nav-badge hide" id="faultsBadge">0</span></div>
    <div class="nav-item" data-nav="getting-started" data-href="getting-started.html"><svg viewBox="0 0 15 15" fill="none"><circle cx="7.5" cy="7.5" r="5.5" stroke="currentColor" stroke-width="1.2"/><path d="M5.8 5.8a1.8 1.8 0 1 1 2.4 1.7c-.5.2-.7.5-.7 1M7.5 10.3v.2" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg> Getting started</div>
    <div class="nav-item" data-nav="settings" data-href="settings.html"><svg viewBox="0 0 15 15" fill="none"><circle cx="7.5" cy="7.5" r="2" stroke="currentColor" stroke-width="1.2"/><path d="M7.5 1.5v2M7.5 11.5v2M1.5 7.5h2M11.5 7.5h2M3.4 3.4l1.4 1.4M10.2 10.2l1.4 1.4M11.6 3.4l-1.4 1.4M4.8 10.2l-1.4 1.4" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/></svg> Settings</div>
  </nav>
  <div class="sb-foot"><div class="status-chip"><span class="status-dot"></span> All systems nominal</div></div>
</aside>
<header class="topbar">
  <div class="proj-switch" id="projSwitch"><span class="proj-dot"></span><span id="projName">project</span><span class="proj-caret">▾</span></div>
  <div class="tb-divider"></div>
  <div class="proj-switch tb-search" style="flex:1;max-width:280px;color:var(--muted);cursor:text"><svg width="13" height="13" viewBox="0 0 13 13" fill="none"><circle cx="5.5" cy="5.5" r="4" stroke="currentColor" stroke-width="1.3"/><path d="M8.5 8.5L11.5 11.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg> Search runs, agents, faults…</div>
  <div class="tb-spacer"></div>
  <div style="position:relative"><div class="avatar" id="avatarBtn">A</div><div class="menu" id="avatarMenu"><div class="menu-email" id="menuEmail"></div><a href="settings.html">Settings</a><a href="/index.html" target="_blank">Marketing site ↗</a><button id="menuSignout">Sign out</button></div></div>
</header>`;
  async function injectLayout(){
    var shell=document.getElementById("shell"); if(!shell) return;
    shell.innerHTML = LAYOUT;
    var page=document.body.dataset.page||"overview";
    shell.querySelectorAll("[data-nav]").forEach(function(n){ if(n.dataset.nav===page) n.classList.add("active"); n.addEventListener("click",function(){ if(n.dataset.href) location.href=n.dataset.href; }); });
    // avatar menu
    var av=shell.querySelector("#avatarBtn"), menu=shell.querySelector("#avatarMenu");
    if(av&&menu){ av.textContent=(ctx.user&&ctx.user.email||"?")[0].toUpperCase();
      var em=menu.querySelector("#menuEmail"); if(em) em.textContent=ctx.user&&ctx.user.email||"";
      av.addEventListener("click",function(e){ e.stopPropagation(); menu.classList.toggle("show"); });
      document.addEventListener("click",function(){ menu.classList.remove("show"); });
      var so=menu.querySelector("#menuSignout"); if(so) so.addEventListener("click",signOut); }
    // project switcher label
    var ps=shell.querySelector("#projName"); if(ps&&ctx.project) ps.textContent=(ctx.project.org_slug||"")+" / "+ctx.project.slug;
    var psw=shell.querySelector("#projSwitch"); if(psw) psw.addEventListener("click", openProjectSwitcher);
    // regressions badge
    if(ctx.project){ getRegressions(ctx.orgId,"open").then(function(rs){ var b=shell.querySelector('[data-nav="faults"] .nav-badge, #faultsBadge'); if(b){ if(rs&&rs.length){ b.textContent=rs.length; b.classList.remove("hide"); } else b.classList.add("hide"); } }).catch(function(){}); }
  }
  function openProjectSwitcher(){
    if(!ctx.projects.length) return;
    var html=ctx.projects.map(function(p){ return '<a href="overview.html" onclick="fl.setProject(\''+p.id+'\')"><span class="proj-dot"></span>'+esc(p.slug)+'</a>'; }).join("")+'<a href="onboarding.html">+ New project</a>';
    var m=document.getElementById("avatarMenu"); // reuse menu styles
    var pop=document.createElement("div"); pop.className="menu show"; pop.style.left="18px"; pop.style.right="auto"; pop.style.top="50px";
    pop.innerHTML=html; document.getElementById("shell").appendChild(pop);
    setTimeout(function(){ document.addEventListener("click",function h(){ pop.remove(); document.removeEventListener("click",h); }); },10);
  }

  // ── context + gate ─────────────────────────────────────────────────────────
  async function loadContext(){
    var m=await sb.from("org_members").select("org_id, role, organizations(id,name,slug)").order("added_at");
    ctx.orgs=(m.data||[]).map(function(x){ return { id:x.org_id, role:x.role, name:x.organizations&&x.organizations.name, slug:x.organizations&&x.organizations.slug }; });
    ctx.orgId=ctx.orgs[0]&&ctx.orgs[0].id||null;
    if(ctx.orgId){
      var p=await sb.from("projects").select("id,name,slug,repo,org_id,alert_threshold,organizations(slug)").order("created_at");
      ctx.projects=(p.data||[]).map(function(x){ return { id:x.id, name:x.name, slug:x.slug, repo:x.repo, org_id:x.org_id, alert_threshold:x.alert_threshold, org_slug:x.organizations&&x.organizations.slug }; });
      var saved=localStorage.getItem("fl_project");
      ctx.project=ctx.projects.filter(function(x){return x.id===saved;})[0]||ctx.projects[0]||null;
    }
  }
  function currentProject(){ return ctx.project ? ctx.project.id : null; }
  function setProject(id){ localStorage.setItem("fl_project", id); }
  async function signOut(){ if(sb) await sb.auth.signOut(); location.href="/index.html"; }

  async function ready(cb){
    document.documentElement.style.visibility="hidden";
    if(!configured){ location.replace("/index.html"); return; }
    var s=(await sb.auth.getSession()).data.session;
    if(!s){ location.replace("/index.html#signin"); return; }
    ctx.session=s; ctx.user=s.user;
    await loadContext();
    // Did the user just deliberately delete their last project? Then DON'T auto-recreate one.
    var _jd=false; try{ _jd=!!sessionStorage.getItem("fl_deleted"); if(_jd) sessionStorage.removeItem("fl_deleted"); }catch(e){}
    // Ensure a project exists so the dashboard is ALWAYS reachable (never loop back to onboarding) —
    // but never right after a deliberate delete.
    if(!ctx.project && ctx.orgId && !_jd && !/onboarding\.html/.test(location.pathname)){
      try{
        var npid=await createProject(ctx.orgId,"My agent","my-agent");
        if(npid){ try{ await mintToken(npid,"CI token"); }catch(e){} localStorage.setItem("fl_project",npid); }
      }catch(e){ /* slug may already exist from a prior attempt */ }
      try{ await loadContext(); }catch(e){}
    }
    // only if we genuinely couldn't establish a project (e.g. no org) → onboarding to recover
    if(!ctx.project && !/onboarding\.html/.test(location.pathname)){ location.replace("onboarding.html"); return; }
    await injectLayout();
    document.documentElement.style.visibility="";
    try{ await cb(ctx); }catch(e){ console.error(e); }
  }

  // ── data helpers ───────────────────────────────────────────────────────────
  async function getOverview(pid){
    var since=new Date(Date.now()-90*864e5).toISOString();
    var runs=(await sb.from("runs").select("*").eq("project_id",pid).gte("created_at",since).order("created_at",{ascending:true})).data||[];
    var latest=runs.length?runs[runs.length-1]:null;
    var weekAgo=Date.now()-7*864e5;
    var prior=runs.filter(function(r){return new Date(r.created_at).getTime()<weekAgo;});
    var prevScore=prior.length?prior[prior.length-1].resilience:(latest?latest.resilience:0);
    var silent=runs.reduce(function(a,r){return a+(r.silent_count||0);},0);
    var agents=(await sb.from("agents").select("id",{count:"exact",head:true}).eq("project_id",pid)).count||0;
    var regs=(await sb.from("regressions").select("*").eq("project_id",pid).eq("status","open").order("detected_at",{ascending:false})).data||[];
    var recent=(await sb.from("runs").select("*").eq("project_id",pid).order("created_at",{ascending:false}).limit(6)).data||[];
    return { runs:runs, latest:latest, prevScore:prevScore, silentCaught:silent, agentCount:agents, openRegs:regs, recent:recent };
  }
  async function getRuns(pid, opts){ opts=opts||{}; var q=sb.from("runs").select("*").eq("project_id",pid).order("created_at",{ascending:false}).limit(opts.limit||50);
    if(opts.agent) q=q.eq("agent_name",opts.agent); return (await q).data||[]; }
  async function getRun(id){ if(!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id||"")) return {run:null,faults:[],prev:null};
    var run=(await sb.from("runs").select("*").eq("id",id).maybeSingle()).data;
    var faults=run?((await sb.from("fault_results").select("*").eq("run_id",id)).data||[]):[];
    var prev=null;
    if(run && run.agent_id){
      var pr=(await sb.from("runs").select("*").eq("agent_id",run.agent_id).lt("created_at",run.created_at).order("created_at",{ascending:false}).limit(1)).data;
      prev=pr&&pr[0];
      if(prev){ prev.fault_results=(await sb.from("fault_results").select("*").eq("run_id",prev.id)).data||[]; }
    }
    return { run:run, faults:faults, prev:prev }; }
  async function getAgents(pid){ var ags=(await sb.from("agents").select("*").eq("project_id",pid)).data||[];
    for(var i=0;i<ags.length;i++){ var r=(await sb.from("runs").select("*").eq("agent_id",ags[i].id).order("created_at",{ascending:false}).limit(100)).data||[]; ags[i].latest=r[0]||null; ags[i].history=r.reverse(); }
    return ags; }
  async function getFaults(pid){ // returns the 6-fault library + current footprint
    var lib=[{name:"wrong-number",cat:"silent",desc:"Returns a plausible but wrong value"},{name:"stale-data",cat:"silent",desc:"Returns outdated data as if fresh"},{name:"truncate",cat:"silent",desc:"Cuts results short without warning"},{name:"null-response",cat:"silent",desc:"Returns empty where data was expected"},{name:"timeout",cat:"hard",desc:"The tool call times out"},{name:"server-error",cat:"hard",desc:"The tool returns a 500"}];
    var runs=(await sb.from("runs").select("id").eq("project_id",pid)).data||[]; var ids=runs.map(function(r){return r.id;});
    var frs=ids.length?((await sb.from("fault_results").select("fault,verdict,run_id").in("run_id",ids.slice(0,400))).data||[]):[];
    lib.forEach(function(f){ var rows=frs.filter(function(x){return x.fault===f.name;}); f.caught=rows.filter(function(x){return x.verdict==="fail"||x.verdict==="crash";}).length; f.total=rows.length; });
    return lib; }
  async function getRegressions(orgId, status){ var q=sb.from("regressions").select("*").order("detected_at",{ascending:false}); if(ctx.project) q=q.eq("project_id",ctx.project.id); else q=q.eq("org_id",orgId); if(status) q=q.eq("status",status); return (await q).data||[]; }
  async function resolveRegression(id, status){ return await sb.from("regressions").update({status:status, resolved_at:new Date().toISOString()}).eq("id",id); }
  async function getTokens(pid){ return (await sb.from("project_tokens").select("id,name,token_prefix,created_at,last_used_at,revoked_at").eq("project_id",pid).order("created_at",{ascending:false})).data||[]; }
  async function mintToken(pid, name){ var r=await sb.rpc("create_project_token",{p_project_id:pid, p_name:name||"CI token"}); if(r.error) throw r.error; return r.data; }
  async function revokeToken(id){ return await sb.from("project_tokens").update({revoked_at:new Date().toISOString()}).eq("id",id); }
  async function createProject(orgId, name, slug, repo){ var r=await sb.rpc("create_project",{p_org_id:orgId, p_name:name, p_slug:slug, p_repo:repo||null}); if(r.error) throw r.error; return r.data; }

  window.fl = {
    sb:sb, configured:configured, ctx:ctx, ready:ready, signOut:signOut,
    currentProject:currentProject, setProject:setProject,
    getOverview:getOverview, getRuns:getRuns, getRun:getRun, getAgents:getAgents,
    getFaults:getFaults, getRegressions:getRegressions, resolveRegression:resolveRegression,
    getTokens:getTokens, mintToken:mintToken, revokeToken:revokeToken, createProject:createProject,
    esc:esc, rel:rel, verdictClass:verdictClass, verdictLabel:verdictLabel, runVerdict:runVerdict,
    agentColor:agentColor, ring:ring, sparkline:sparkline, toast:toast, copy:copy, qs:qs
  };
})();
