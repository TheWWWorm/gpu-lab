/* SPDX-License-Identifier: Apache-2.0 */
"use strict";
const $ = id => document.getElementById(id);
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
const states = {running:"Running",queued:"Queued",completed:"Completed",failed:"Failed",cancelled:"Cancelled",timed_out:"Timed out",interrupted:"Interrupted"};
const active = job => job.status === "running" || job.status === "queued";
const name = job => job.label || job.argv?.[0]?.split("/").pop() || job.id;
const fmtTime = value => value ? new Date(value * 1000).toLocaleString([], {month:"short",day:"numeric",hour:"2-digit",minute:"2-digit",second:"2-digit"}) : "—";
const duration = seconds => { const s = Math.max(0, Math.floor(seconds)); return s >= 3600 ? `${Math.floor(s/3600)}h ${Math.floor(s%3600/60)}m` : s >= 60 ? `${Math.floor(s/60)}m ${s%60}s` : `${s}s`; };
const elapsed = job => duration((job.finished_at || Date.now()/1000) - (job.started_at || job.created_at));
const gib = mib => typeof mib === "number" ? `${(mib/1024).toFixed(1)} GiB` : "—";
const badge = job => el("span",`badge ${Object.hasOwn(states,job.status) ? job.status : ""}`,states[job.status] || job.status);
let state = {gpus:{},jobs:[],history:{},active_jobs:[],queued:[]};
let selected = null, selectedJob = null, filter = "all", gpuFilter = "all", search = "", limit = 12;
let currentTab = "preview", previewPaused = false, previewBusy = false, logsBusy = false, statusBusy = false;
let frameURL = null, frameJob = null, lastFrameAt = 0, lastLogsAt = 0, logsText = "", renderKey = "", detailsKey = "";
let connected = false, lastStatusError = false;
const rows = new Map();

async function api(path, options) {
  const response = await fetch(path, {cache:"no-store", ...options});
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error = new Error(body.error || `Request failed (${response.status})`);
    error.status = response.status; throw error;
  }
  return response.json();
}
function notice(message) { $("notice").hidden = !message; $("notice-text").textContent = message || ""; }
function connection(ok, text) { $("connection").className = `connection ${ok ? "live" : "error"}`; $("connection-text").textContent = text; }
const inSharedQueue = job => (state.shared_queued || []).includes(job.id);
const gpuLabel = job => job.gpu_slot === "auto" ? "Awaiting GPU assignment" : `GPU ${job.gpu_slot ?? "?"}`;
function queuePosition(job) { const q = inSharedQueue(job) ? state.shared_queued : state.gpus[job.gpu_slot]?.queued || []; return q.indexOf(job.id) + 1; }
function queueLabel(job) { const position = queuePosition(job); return `${inSharedQueue(job) ? "Shared queue" : `GPU ${job.gpu_slot} queue`}${position ? ` #${position}` : ""}`; }
function makeChart(samples) {
  const ns = "http://www.w3.org/2000/svg", svg = document.createElementNS(ns,"svg");
  svg.setAttribute("viewBox","0 0 300 45"); svg.setAttribute("preserveAspectRatio","none"); svg.classList.add("chart"); svg.setAttribute("role","img"); svg.setAttribute("aria-label","GPU utilization over the last two minutes, from 0 to 100 percent");
  const line = document.createElementNS(ns,"path"); line.setAttribute("d","M0 43H300 M0 2H300"); line.classList.add("grid-line"); svg.append(line);
  let points = [];
  const draw = () => { if (!points.length) return; const path = document.createElementNS(ns,"polyline"); path.setAttribute("points",points.join(" ")); path.classList.add("trace"); svg.append(path); points = []; };
  samples.forEach((s,i) => { if (typeof s.utilization !== "number") {draw(); return;} points.push(`${(59-samples.length+1+i)/59*300},${43-Math.min(100,Math.max(0,s.utilization))*.41}`); });
  draw(); return svg;
}
function renderGPUs() {
  const cards = ["0","1"].map(slot => {
    const gpu = state.gpus[slot] || {}, available = gpu.available === true && !lastStatusError;
    const load = available ? gpu.load || {} : {}, jobs = gpu.active_jobs || [];
    const card = el("article","gpu-card"); card.setAttribute("aria-label",`GPU ${slot}`);
    const header = el("div","gpu-header"), identity = el("h2","gpu-name");
    identity.append(el("span","gpu-index",`GPU ${slot}`),el("span","gpu-model",gpu.gpu?.name?.replace(/^NVIDIA GeForce /,"") || "GPU unavailable"));
    header.append(identity,el("span",`gpu-availability${available ? "" : " offline"}`,available ? (gpu.accepting === false ? "Draining queue" : "Worker online") : "Worker offline"));
    const reading = el("div","gpu-reading"), util = el("div"), uh = el("div","util-heading");
    uh.append(el("strong",null,typeof load.utilization_percent === "number" ? `${load.utilization_percent}%` : "—"),el("span",null,"GPU load · 2 min"));
    util.append(uh,makeChart(state.history[slot] || []));
    const memory = el("div"), mh = el("div","memory-heading"); mh.append(el("span",null,"Memory in use"),el("strong",null,gib(load.memory_used_mib)));
    const progress = el("progress","memory-progress"); progress.max = load.memory_total_mib || 1; progress.value = load.memory_used_mib || 0;
    progress.setAttribute("aria-label",`GPU ${slot} memory: ${gib(load.memory_used_mib)} of ${gib(load.memory_total_mib)}`); progress.hidden = load.memory_used_mib == null;
    memory.append(mh,progress,el("p","memory-total",load.memory_total_mib ? `of ${gib(load.memory_total_mib)} total` : "Memory reading unavailable"));
    reading.append(util,memory);
    const bottom = el("div","gpu-bottom"), slots = el("div","slot-group");
    for(let i=0;i<(gpu.scheduler?.max_concurrent || 3);i++) { const s = el("span",`slot${available && i<jobs.length ? " occupied" : ""}`); s.setAttribute("aria-hidden","true");slots.append(s); }
    slots.append(el("span",null,available ? `${jobs.length} / ${gpu.scheduler?.max_concurrent || 3} running` : "Slots unavailable"));
    const q = (gpu.queued || []).length; bottom.append(slots,el("span",q ? "queue-count" : "",available ? `${q} queued` : "Queue unavailable"));
    card.append(header,reading,bottom); return card;
  });
  $("gpus").replaceChildren(...cards);
}
function filteredJobs() {
  const priority = j => j.status === "running" ? 0 : j.status === "queued" ? 1 : 2;
  return state.jobs.filter(j => (filter === "all" || (filter === "finished" ? !active(j) : j.status === filter)) && (gpuFilter === "all" || j.gpu_slot === gpuFilter) && (!search || `${name(j)} ${j.id} ${j.cwd} ${(j.argv || []).join(" ")}`.toLowerCase().includes(search))).sort((a,b) => priority(a)-priority(b) || (active(a) ? a.created_at-b.created_at : b.created_at-a.created_at));
}
function renderJobs() {
  const jobs = filteredJobs(), showing = jobs.slice(0,limit), running = state.active_jobs.length, queued = state.queued.length;
  $("job-count").textContent = state.jobs.length; $("running-count").textContent = running; $("queued-count").textContent = queued;
  const shared = (state.shared_queued || []).length;
  $("queue-summary").textContent = `${running} running · ${queued} queued${shared ? ` · ${shared} awaiting dispatch` : ""}`;
  const key = JSON.stringify([selected,showing.map(j => [j.id,j.status,j.label,j.cwd,j.gpu_slot,queuePosition(j),inSharedQueue(j)])]);
  if (key !== renderKey) {
    const body = $("jobs"), kept = new Set();
    for (const job of showing) {
      let row = rows.get(job.id);
      if (!row) {
        row = el("tr","job-row"); row.dataset.id = job.id;
        const first = el("td"), button = el("button","job-name"); button.type = "button";
        button.addEventListener("click",() => selectJob(job.id,true));
        first.append(button,el("div","job-folder"));
        row.append(first,el("td"),el("td"),el("td","duration-cell"));
        row.addEventListener("click",e => { if (!e.target.closest("button")) selectJob(job.id,true); });
        rows.set(job.id,row);
      }
      row.classList.toggle("selected",job.id === selected);
      const button = row.querySelector("button"); button.textContent = name(job); button.title = name(job); button.setAttribute("aria-pressed",String(job.id === selected)); button.setAttribute("aria-label",`Inspect ${name(job)}, ${states[job.status] || job.status}, ${gpuLabel(job)}`);
      row.querySelector(".job-folder").textContent = job.cwd?.replace(/^\/home\/[^/]+\//,"~/") || "—";
      row.querySelector(".job-folder").title = job.cwd || "";
      row.children[1].replaceChildren(badge(job));
      if(job.status === "queued") row.children[1].append(el("div","queue-position",queueLabel(job)));
      row.children[2].textContent = job.gpu_slot === "auto" ? "Auto" : job.gpu_slot ?? "—";
      const expected = body.children[kept.size]; if (expected !== row) body.insertBefore(row,expected || null);
      kept.add(job.id);
    }
    for(const child of [...body.children]) if(!kept.has(child.dataset.id)) child.remove();
    for(const id of rows.keys()) if(!state.jobs.some(j => j.id === id)) rows.delete(id);
    renderKey = key;
  }
  for(const job of showing) { const row = rows.get(job.id); row.lastElementChild.textContent = elapsed(job); row.lastElementChild.title = job.status === "queued" ? "Time waiting" : "Run duration"; }
  $("empty-jobs").hidden = jobs.length !== 0;
  const initialEmpty = state.jobs.length === 0 && !search && filter === "all" && gpuFilter === "all";
  $("empty-jobs").querySelector("h3").textContent = initialEmpty ? "No jobs yet" : "No matching jobs";
  $("empty-jobs").querySelector("p").textContent = initialEmpty ? "Jobs submitted through GPU Lab will appear here automatically." : "Try another filter or search.";
  $("clear-filters").hidden = initialEmpty;
  $("show-more").hidden = showing.length === jobs.length;
  $("history-note").textContent = `${showing.length} of ${jobs.length} shown · recent history`;
}
function selectJob(id, fromUser=false) {
  if (id !== selected) {
    selected = id; detailsKey = ""; lastLogsAt = 0; lastFrameAt = 0; logsText = "";
    $("stdout").textContent = "Loading output…"; $("stderr").textContent = "Loading output…"; $("copy-logs").disabled = true; $("logs-error").hidden = true;
    if(frameURL) URL.revokeObjectURL(frameURL);
    frameURL = frameJob = null; $("preview").hidden = true; $("preview-empty").hidden = false;
    $("enlarge-preview").disabled = true; $("preview-dialog").close();
  }
  selectedJob = state.jobs.find(j => j.id === id) || selectedJob;
  renderJobs(); renderInspector(); refreshInspector();
  if(fromUser && matchMedia("(max-width:780px)").matches) document.querySelector(".inspector").scrollIntoView({behavior:"instant",block:"start"});
}
function detail(label,value,mono=false) { const row = el("div"); row.append(el("dt",null,label),el("dd",mono ? "mono" : null,value == null ? "—" : String(value))); return row; }
function renderInspector() {
  const job = selectedJob; $("no-selection").hidden = !!job; $("selection").hidden = !job;
  if (!job) return;
  $("selected-title").textContent = name(job);
  $("selected-badge").replaceWith(Object.assign(badge(job),{id:"selected-badge"}));
  $("selected-location").textContent = `${gpuLabel(job)}${job.display ? ` · Display ${job.display}` : ""}`;
  const reason = job.status === "queued" ? `${queueLabel(job)}. ${job.waiting_reason || "Waiting for earlier jobs and available GPU capacity."}` : job.error;
  $("selected-reason").hidden = !reason; $("selected-reason").textContent = reason || "";
  $("selected-id").textContent = job.id; $("selected-duration").textContent = `${elapsed(job)} ${job.status === "queued" ? "waiting" : job.status === "running" ? "elapsed" : "run time"}`;
  $("pause-preview").disabled = job.status !== "running";
  if(!frameURL) {
    $("preview-empty").hidden = false;
    $("preview-empty-title").textContent = job.status === "running" ? "Reading virtual display…" : job.status === "queued" ? "Waiting for a display" : "This job has finished";
    $("preview-empty-message").textContent = job.status === "running" ? "Some jobs run in the background without a visible window." : job.status === "queued" ? "The preview appears when this job starts." : "Its virtual display has been released. Logs and details are still available.";
  }
  if(job.status !== "running") $("preview-state").textContent = frameURL ? "Last frame · job finished" : job.status === "queued" ? "Queued · no display yet" : "No live display";
  else if(previewPaused) $("preview-state").textContent = "Preview paused";
  else if(!frameURL) $("preview-state").textContent = "Reading display…";
  const key = JSON.stringify(job);
  if(key !== detailsKey) {
    const values = [detail("Job ID",job.id,true),detail("GPU",gpuLabel(job)),detail("Display",job.display || "Not assigned"),detail("Workload",job.workload || "auto"),detail("Memory hint",job.vram_mb ? `${job.vram_mb.toLocaleString()} MiB` : "Default"),detail("Working folder",job.cwd,true),detail("Created",fmtTime(job.created_at)),detail("Started",fmtTime(job.started_at)),detail("Finished",fmtTime(job.finished_at)),detail("Time limit",duration(job.timeout_seconds)),detail("Exit code",job.exit_code),detail("Artifacts",job.directory,true)];
    if(job.requested_gpu) values.splice(2,0,detail("GPU request",job.requested_gpu === "auto" ? "Automatic" : `GPU ${job.requested_gpu}`));
    if(job.assignment_reason) values.splice(3,0,detail("Assignment",job.assignment_reason));
    $("details").replaceChildren(...values);
    $("command").textContent = (job.argv || []).map(a => /^[\w./:=+-]+$/.test(a) ? a : "'"+a.replaceAll("'","'\\''")+"'").join(" ");
    detailsKey = key;
  }
}
async function refreshPreview() {
  const job = selectedJob;
  if(!job || job.status !== "running" || previewPaused || previewBusy || document.hidden || currentTab !== "preview" || Date.now()-lastFrameAt<1900) return;
  previewBusy = true; const id = job.id;
  try {
    const response = await fetch(`/api/jobs/${id}/preview`,{cache:"no-store"});
    if(!response.ok) throw new Error((await response.json()).error || "Preview unavailable");
    const blob = await response.blob();
    if(selected !== id) return;
    const previous = frameURL; frameURL = URL.createObjectURL(blob); frameJob = id;
    $("preview").src = frameURL; $("large-preview").src = frameURL;
    if(previous) URL.revokeObjectURL(previous);
    $("preview").hidden = false; $("preview-empty").hidden = true; $("enlarge-preview").disabled = false;
    $("preview-state").textContent = response.headers.get("X-Preview-State") === "last-frame" ? "Last frame · job finished" : "Live snapshots · every 2s";
    $("dialog-note").textContent = "Snapshot preview · updates every 2 seconds";
  } catch(error) {
    if(selected !== id) return;
    $("preview-state").textContent = frameURL ? "Last frame · retrying…" : "Preview unavailable · retrying…";
    if(!frameURL) { $("preview-empty-title").textContent = "No preview yet"; $("preview-empty-message").textContent = error.message; }
    $("dialog-note").textContent = "Preview unavailable · showing the last frame and retrying";
  } finally { previewBusy = false; if(selected === id) lastFrameAt = Date.now(); }
}
async function refreshLogs() {
  if(!selectedJob || currentTab !== "logs" || logsBusy || document.hidden || Date.now()-lastLogsAt<3900) return;
  logsBusy = true; const id = selected;
  try {
    const result = await api(`/api/jobs/${id}/logs`);
    if(selected !== id) return;
    for(const stream of ["stdout","stderr"]) { const node = $(stream), atEnd = node.scrollHeight-node.scrollTop-node.clientHeight<20; node.textContent = result[stream] || "No output yet."; if(atEnd) node.scrollTop = node.scrollHeight; }
    logsText = `Standard output\n${result.stdout || ""}\n\nStandard error\n${result.stderr || ""}`;
    $("copy-logs").disabled = false; $("logs-error").hidden = true;
    $("log-state").textContent = active(selectedJob) ? "· updating" : "· finished";
    const truncated = result.job?.stdout_truncated || result.job?.stderr_truncated;
    $("log-note").textContent = truncated ? "Latest 32 KB of retained output. This job exceeded the worker's 8 MiB limit; later output was not retained." : "Latest 32 KB per stream. Full retained logs are in the job's artifact folder.";
  } catch(error) { if(selected === id) { $("logs-error").hidden = false; $("logs-error").textContent = `${error.message} Retrying shortly.`; } }
  finally { logsBusy = false; if(selected === id) lastLogsAt = Date.now(); }
}
function refreshInspector() { refreshPreview(); refreshLogs(); }
async function refreshStatus() {
  if(statusBusy || document.hidden) return;
  statusBusy = true;
  try {
    state = await api("/api/status"); connected = true;
    lastStatusError = !!state.error || !state.updated_at || Date.now()/1000-state.updated_at>10;
    notice(lastStatusError ? "GPU readings are unavailable. Showing the last known jobs and retrying automatically." : null);
    const offline = Object.values(state.gpus).some(gpu => !gpu.available);
    connection(!lastStatusError && !offline,lastStatusError ? "Connection interrupted" : offline ? "Worker unavailable" : "Live");
    renderGPUs();
    if(!selected && state.jobs.length) { const first = state.jobs.find(j => j.status === "running") || state.jobs.find(j => j.status === "queued") || state.jobs[0]; selectJob(first.id); }
    else { selectedJob = state.jobs.find(j => j.id === selected) || selectedJob; renderJobs(); renderInspector(); }
    refreshInspector();
  } catch(error) {
    lastStatusError = true;
    connection(false,error.status === 401 ? "Reconnect browser" : "Connection interrupted");
    notice(error.status === 401 ? error.message : "GPU Lab is unavailable. Retrying automatically; if this continues, open GPU Lab from Applications again.");
    if(connected) renderGPUs();
  } finally { statusBusy = false; }
}
function changeTab(tab,focus=false) {
  currentTab = tab;
  for(const button of document.querySelectorAll("[data-tab]")) { const chosen = button.dataset.tab === tab; button.setAttribute("aria-selected",String(chosen)); button.tabIndex = chosen ? 0 : -1; $(`panel-${button.dataset.tab}`).hidden = !chosen; if(chosen && focus) button.focus(); }
  refreshInspector();
}
for(const button of document.querySelectorAll("[data-filter]")) button.addEventListener("click",() => { filter = button.dataset.filter; limit = 12; document.querySelectorAll("[data-filter]").forEach(b => b.setAttribute("aria-pressed",String(b === button))); renderJobs(); });
$("search").addEventListener("input",event => { search = event.target.value.toLowerCase().trim(); limit = 12; renderJobs(); });
$("gpu-filter").addEventListener("change",event => { gpuFilter = event.target.value; limit = 12; renderJobs(); });
$("clear-filters").addEventListener("click",() => { search = ""; gpuFilter = "all"; $("search").value = ""; $("gpu-filter").value = "all"; document.querySelector('[data-filter="all"]').click(); });
$("show-more").addEventListener("click",() => { limit += 12; renderJobs(); });
for(const button of document.querySelectorAll("[data-tab]")) {
  button.addEventListener("click",() => changeTab(button.dataset.tab));
  button.addEventListener("keydown",event => { const tabs = ["preview","logs","details"], i = tabs.indexOf(currentTab); const next = event.key === "ArrowRight" ? (i+1)%3 : event.key === "ArrowLeft" ? (i+2)%3 : event.key === "Home" ? 0 : event.key === "End" ? 2 : null; if(next !== null) {event.preventDefault();changeTab(tabs[next],true);} });
}
$("pause-preview").addEventListener("click",() => { previewPaused = !previewPaused; $("pause-preview").textContent = previewPaused ? "Resume" : "Pause"; $("pause-preview").setAttribute("aria-pressed",String(previewPaused)); $("preview-state").textContent = previewPaused ? "Preview paused" : "Reading display…"; lastFrameAt = 0; refreshPreview(); });
$("enlarge-preview").addEventListener("click",() => { $("dialog-title").textContent = `${name(selectedJob)} · GPU ${selectedJob.gpu_slot}`; $("dialog-note").textContent = previewPaused ? "Preview paused" : selectedJob.status === "running" ? "Snapshot preview · updates every 2 seconds" : "Last frame · job finished"; $("download-preview").disabled = selectedJob.status !== "running"; $("preview-dialog").showModal(); });
$("close-preview").addEventListener("click",() => $("preview-dialog").close());
$("download-preview").addEventListener("click",async () => {
  const id = selected; $("download-preview").disabled = true;
  try { const response = await fetch(`/api/jobs/${id}/preview?full=1`); if(!response.ok) throw new Error((await response.json()).error); const blob = await response.blob(), url = URL.createObjectURL(blob), link = el("a"); link.href = url; link.download = `gpu-lab-${id}.png`; link.click(); setTimeout(() => URL.revokeObjectURL(url),1000); }
  catch(error) { $("dialog-note").textContent = error.message || "Could not save this frame. Try again."; }
  finally { $("download-preview").disabled = selectedJob?.status !== "running"; }
});
$("copy-logs").addEventListener("click",async () => { try { await navigator.clipboard.writeText(logsText); $("copy-logs").textContent = "Copied"; } catch { $("copy-logs").textContent = "Select logs to copy"; } setTimeout(() => $("copy-logs").textContent = "Copy logs",2000); });
$("retry").addEventListener("click",refreshStatus);
$("back-to-jobs").addEventListener("click",() => { $("jobs-title").scrollIntoView({behavior:"instant",block:"start"}); $("jobs-title").focus({preventScroll:true}); });
document.addEventListener("visibilitychange",() => { if(!document.hidden) refreshStatus(); });
async function start() {
  const token = new URLSearchParams(location.hash.slice(1)).get("token");
  if(token) { history.replaceState(null,"",location.pathname); try { await api("/api/session",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({token})}); } catch(error) { notice(error.message); connection(false,"Reconnect browser"); return; } }
  await refreshStatus();
  setInterval(refreshStatus,2000);
}
start();
