import { GpuRenderer } from "./gpu.js";

const $ = (id) => document.getElementById(id);
const inputs = [...document.querySelectorAll('#controls input[type="range"]')];
const defaults = Object.fromEntries(inputs.map((input) => [input.id, 0]));
const settings = () => Object.fromEntries(inputs.map((input) => [input.id, Number(input.value)]));
let canvas = $("preview"), gpu, context;
let photo = null, opening = false, exporting = false, comparing = false, split = false;
let mode = "fit", pan = [0,0], detail = null, cpuOriginal = null, cpuEdited = null;
let revision = 0, detailRevision = 0, editTimer, detailTimer, histogramTimer, frame;
let cpuRunning = false, histogramRunning = false, detailAbort;
const worker = new Worker("/static/histogram-worker.js");

function error(message) { $("error-text").textContent = message; $("error").hidden = false; }
function busy(message) { $("busy").hidden = !message; $("busy-text").textContent = message || ""; }
function status(message) { $("status").textContent = message; }
function availability() {
  $("controls").disabled = !photo || opening || exporting;
  for (const id of ["reset","compare","export","fit","zoom","split"]) $(id).disabled = !photo || opening || exporting;
  $("split-position").disabled = !photo || opening || exporting;
  $("open").disabled = $("choose").disabled = opening || exporting;
}
async function checked(response) {
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(typeof data.detail === "string" ? data.detail : `요청을 완료하지 못했어요 (${response.status}).`);
  }
  return response;
}
function updateOutputs() {
  for (const input of inputs) document.querySelector(`output[for="${input.id}"]`).textContent = input.id === "exposure" ? Number(input.value).toFixed(2) : input.value;
}
async function postImage(id, kind, values, signal) {
  return checked(await fetch(`/api/photos/${id}/${kind}`, {
    method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(values), signal,
  }));
}
async function linearData(response) {
  await checked(response);
  const buffer = await response.arrayBuffer();
  const data = {width:Number(response.headers.get("X-Image-Width")), height:Number(response.headers.get("X-Image-Height")),
    x:Number(response.headers.get("X-Image-X")), y:Number(response.headers.get("X-Image-Y")), pixels:new Uint16Array(buffer)};
  if (!data.width || !data.height || buffer.byteLength !== data.width*data.height*6) throw new Error("미리보기 데이터가 올바르지 않습니다.");
  return data;
}
function cpuCanvas() {
  const replacement = canvas.cloneNode(false); canvas.replaceWith(replacement); canvas = replacement;
  gpu = null; context = canvas.getContext("2d",{alpha:false});
  $("renderer-mode").textContent = "CPU 미리보기 · GPU 사용 불가";
}
try {
  gpu = new GpuRenderer(canvas);
  $("renderer-mode").textContent = gpu.software ? "WebGL2 · 소프트웨어" : gpu.hardwareKnown ? "GPU · WebGL2" : "WebGL2 · 가속 상태 확인 불가";
}
catch { cpuCanvas(); }
function closeBitmap(image) { image?.close(); }
function clearDetail() {
  detailRevision++; detailAbort?.abort(); clearTimeout(detailTimer);
  if (detail) { closeBitmap(detail.original); closeBitmap(detail.edited); }
  detail = null; gpu?.clearDetail(); delete canvas.dataset.detail;
}
async function loadCpu(next) {
  const image = await createImageBitmap(await (await postImage(next.id,"preview",defaults)).blob());
  if (cpuEdited !== cpuOriginal) closeBitmap(cpuEdited); closeBitmap(cpuOriginal);
  cpuOriginal = cpuEdited = image;
}
$("canvas").addEventListener("webglcontextlost", async (event) => {
  event.preventDefault(); if (!gpu) return;
  cpuCanvas(); clearDetail(); revision++;
  if (photo) {
    try { await loadCpu(photo); await refreshCpu(); resize(); scheduleHistogram(); scheduleDetail(); }
    catch (e) { error(e.message); }
  }
  error("GPU 연결이 해제되어 CPU 미리보기로 전환했습니다.");
}, true);

function view() {
  if (!photo) return {scale:1,origin:[0,0],offset:[0,0]};
  const scale = mode === "fit" ? Math.min(canvas.width/photo.width,canvas.height/photo.height) : 1;
  const offset = [Math.max(0,(canvas.width-photo.width*scale)/2),Math.max(0,(canvas.height-photo.height*scale)/2)];
  if (mode === "zoom") pan = pan.map((v,i) => Math.max(0,Math.min(Math.round(v),Math.max(0,[photo.width,photo.height][i]-[canvas.width,canvas.height][i]))));
  return {scale,origin:mode === "fit" ? [0,0] : pan,offset};
}
function render() {
  if (!photo || !canvas.width || !canvas.height) return;
  const v = view(), fraction = Number($("split-position").value)/100;
  if (gpu) gpu.draw(settings(),v,{before:comparing,split,splitPosition:fraction});
  else if (cpuOriginal) {
    context.fillStyle = "#101212"; context.fillRect(0,0,canvas.width,canvas.height);
    const draw = (original) => {
      const image = original ? cpuOriginal : cpuEdited;
      context.imageSmoothingEnabled = mode !== "zoom";
      context.drawImage(image,v.offset[0]-v.origin[0]*v.scale,v.offset[1]-v.origin[1]*v.scale,photo.width*v.scale,photo.height*v.scale);
      if (detail && mode === "zoom") {
        context.imageSmoothingEnabled = false;
        context.drawImage(original ? detail.original : detail.edited,v.offset[0]+(detail.x-v.origin[0])*v.scale,v.offset[1]+(detail.y-v.origin[1])*v.scale,detail.width*v.scale,detail.height*v.scale);
      }
    };
    draw(comparing);
    if (split && !comparing) {
      context.save(); context.beginPath(); context.rect(0,0,canvas.width*fraction,canvas.height); context.clip(); draw(true); context.restore();
    }
  }
  $("compare-label").hidden = !comparing;
  $("split-line").hidden = !split || comparing;
  $("split-line").style.left = `${fraction*100}%`;
}
function queueRender() { cancelAnimationFrame(frame); frame = requestAnimationFrame(render); }
function resize() {
  const rect = $("canvas").getBoundingClientRect();
  canvas.width = Math.max(1,Math.round((rect.width-2)*devicePixelRatio));
  canvas.height = Math.max(1,Math.round((rect.height-2)*devicePixelRatio));
  render(); if (mode === "zoom") scheduleDetail();
}
new ResizeObserver(resize).observe($("canvas"));

async function openFile(file) {
  if (!file || opening || exporting) return;
  if (!/\.nef$/i.test(file.name)) return error("니콘 NEF 파일을 선택해 주세요.");
  if (file.size > 256*1024*1024) return error("사진 한 장의 최대 크기는 256 MB입니다.");
  opening = true; revision++; clearTimeout(editTimer); clearDetail(); availability();
  $("error").hidden = true; busy("NEF를 열고 400만 화소 미리보기를 준비하고 있어요…");
  try {
    const form = new FormData(); form.append("file",file);
    const next = await (await checked(await fetch("/api/photos",{method:"POST",body:form}))).json();
    if (gpu) {
      try { gpu.load(await linearData(await fetch(`/api/photos/${next.id}/pixels`)),next); }
      catch { cpuCanvas(); await loadCpu(next); }
    } else await loadCpu(next);
    photo = next; comparing = false; split = false; mode = "fit"; pan = [0,0];
    inputs.forEach((input) => {input.value=0;}); updateOutputs();
    $("empty").hidden = true; canvas.hidden = false;
    $("filename").textContent = next.filename;
    $("dimensions").textContent = `${next.width} × ${next.height} · 미리보기 ${(next.preview_width*next.preview_height/1e6).toFixed(1)}MP`;
    updateViewButtons(); resize(); status("준비됐어요 · 원본은 안전하게"); scheduleHistogram();
  } catch (e) { error(e.message); }
  finally { opening = false; busy(null); availability(); $("file").value=""; }
}
function scheduleEdits() {
  revision++; updateOutputs();
  if (gpu) { queueRender(); status(gpu.software ? "보정 완료 · WebGL2 소프트웨어" : "보정 완료 · WebGL2 미리보기"); scheduleHistogram(); }
  else { clearTimeout(editTimer); status("보정 적용 중…"); editTimer=setTimeout(refreshCpu,100); }
}
async function refreshCpu() {
  if (!photo || cpuRunning || opening || exporting || gpu) return;
  cpuRunning = true; const version=revision, id=photo.id;
  try {
    const image=await createImageBitmap(await (await postImage(id,"preview",settings())).blob());
    if (version === revision && photo.id === id) {
      if (cpuEdited !== cpuOriginal) closeBitmap(cpuEdited);
      cpuEdited=image; clearDetail(); render(); scheduleHistogram(); scheduleDetail(); status("보정 완료 · CPU 미리보기");
    } else closeBitmap(image);
  } catch (e) { if (version === revision) error(e.message); }
  finally { cpuRunning=false; if (version !== revision && !opening && !exporting) refreshCpu(); }
}

function scheduleHistogram() { clearTimeout(histogramTimer); histogramTimer=setTimeout(updateHistogram,180); }
function updateHistogram() {
  if (!photo || opening || histogramRunning) return;
  try {
    let pixels;
    if (gpu) { pixels=gpu.histogram(settings(),view()); render(); }
    else {
      const offscreen=document.createElement("canvas"); offscreen.width=photo.preview_width; offscreen.height=photo.preview_height;
      const ctx=offscreen.getContext("2d"); ctx.drawImage(cpuEdited,0,0); pixels=ctx.getImageData(0,0,offscreen.width,offscreen.height).data;
    }
    histogramRunning=true; worker.postMessage({revision,pixels:pixels.buffer},[pixels.buffer]);
  } catch (e) { error(`히스토그램을 만들 수 없습니다: ${e.message}`); }
}
worker.onmessage = ({data}) => {
  histogramRunning=false;
  if (data.revision !== revision) { scheduleHistogram(); return; }
  const chart=$("histogram"), ctx=chart.getContext("2d"), {bins,count}=data;
  ctx.clearRect(0,0,chart.width,chart.height);
  const max=Math.max(...bins,1), height=chart.height-4;
  ctx.strokeStyle="#303435"; ctx.lineWidth=1;
  for (let x=0;x<=4;x++) {ctx.beginPath();ctx.moveTo(x*chart.width/4,0);ctx.lineTo(x*chart.width/4,height);ctx.stroke();}
  ctx.globalCompositeOperation="screen";
  for (let c=0;c<3;c++) {
    ctx.beginPath();ctx.moveTo(0,height);
    for (let i=0;i<256;i++) ctx.lineTo(i*chart.width/255,height-Math.sqrt(bins[c*256+i]/max)*height);
    ctx.lineTo(chart.width,height);ctx.closePath();ctx.fillStyle=["#dd625466","#74b48b66","#699edb66"][c];ctx.fill();
    ctx.strokeStyle=["#dd6254","#74b48b","#699edb"][c];ctx.stroke();
  }
  ctx.globalCompositeOperation="source-over";
  $("histogram-count").textContent=`${count.toLocaleString()} 픽셀 · 보정 후 전체 사진`;
  chart.dataset.count=count; chart.dataset.revision=data.revision;
};
worker.onerror = () => {histogramRunning=false;error("히스토그램 작업에 실패했습니다. 새로고침해 주세요.");};

function updateViewButtons() {
  $("fit").setAttribute("aria-pressed",mode === "fit"); $("zoom").setAttribute("aria-pressed",mode === "zoom");
  $("split").setAttribute("aria-pressed",split); $("split-control").hidden=!split;
  $("canvas").classList.toggle("zoomed",mode === "zoom");
}
function setMode(next, point) {
  if (!photo || opening || exporting) return;
  const v=view();
  if (next === "zoom") {
    const center=point || [canvas.width/2,canvas.height/2];
    pan=[(center[0]-v.offset[0])/v.scale+v.origin[0]-canvas.width/2,(center[1]-v.offset[1])/v.scale+v.origin[1]-canvas.height/2];
  }
  mode=next;clearDetail();updateViewButtons();render();
  if (mode === "zoom") scheduleDetail(); else status("화면 맞춤 · 전체 사진");
}
function scheduleDetail() {
  clearTimeout(detailTimer); detailRevision++; detailAbort?.abort();
  if (mode !== "zoom" || !photo || opening) return;
  detailTimer=setTimeout(loadDetail,140);
}
async function loadDetail() {
  if (mode !== "zoom" || !photo || opening) return;
  const v=view(), id=photo.id, version=detailRevision;
  const x=Math.floor(v.origin[0]),y=Math.floor(v.origin[1]);
  const width=Math.min(4096,canvas.width,photo.width-x),height=Math.min(4096,canvas.height,photo.height-y);
  const query=`detail?x=${x}&y=${y}&width=${width}&height=${height}`;
  detailAbort=new AbortController(); const signal=detailAbort.signal;
  status("100% 디테일을 불러오는 중…");
  let original,edited;
  try {
    if (gpu) {
      const data=await linearData(await fetch(`/api/photos/${id}/${query}`,{signal}));
      if (version !== detailRevision || photo.id !== id || mode !== "zoom") return;
      gpu.loadDetail(data);
    } else {
      const responses=await Promise.all([postImage(id,query,defaults,signal),postImage(id,query,settings(),signal)]);
      original=await createImageBitmap(await responses[0].blob()); edited=await createImageBitmap(await responses[1].blob());
      if (version !== detailRevision || photo.id !== id || mode !== "zoom") {closeBitmap(original);closeBitmap(edited);return;}
      if (detail) {closeBitmap(detail.original);closeBitmap(detail.edited);}
      detail={original,edited,x,y,width:original.width,height:original.height};
    }
    canvas.dataset.detail=`${x},${y},${width},${height}`; render();
    status("100% · 원본 1픽셀 = 화면 1픽셀 · 드래그로 이동");
  } catch (e) {
    closeBitmap(original);closeBitmap(edited);
    if (e.name !== "AbortError" && version === detailRevision) {error(e.message);status("원본 디테일을 불러오지 못했어요 · 축소 미리보기 표시 중");}
  }
}
let drag=null;
$("canvas").addEventListener("pointerdown",(event) => {
  if (mode !== "zoom" || !photo || opening || exporting || event.target.closest("#busy")) return;
  event.preventDefault(); $("canvas").setPointerCapture(event.pointerId);
  drag={x:event.clientX,y:event.clientY,pan:[...view().origin]};
});
$("canvas").addEventListener("pointermove",(event) => {
  if (!drag) return;
  pan=[drag.pan[0]-(event.clientX-drag.x)*devicePixelRatio,drag.pan[1]-(event.clientY-drag.y)*devicePixelRatio]; queueRender();
});
for (const name of ["pointerup","pointercancel","lostpointercapture"]) $("canvas").addEventListener(name,() => {if (drag) {drag=null;render();scheduleDetail();}});
$("canvas").addEventListener("dblclick",(event) => {
  const rect=canvas.getBoundingClientRect(); setMode(mode === "fit" ? "zoom" : "fit",[(event.clientX-rect.left)*devicePixelRatio,(event.clientY-rect.top)*devicePixelRatio]);
});
$("fit").onclick=() => setMode("fit"); $("zoom").onclick=() => {if (mode !== "zoom") setMode("zoom");};
$("split").onclick=() => {split=!split;comparing=false;updateViewButtons();render();};
$("split-position").oninput=queueRender;
inputs.forEach((input) => input.addEventListener("input",scheduleEdits));
$("reset").onclick=() => {inputs.forEach((input) => {input.value=0;});scheduleEdits();};
$("dismiss").onclick=() => {$("error").hidden=true;};
$("open").onclick=$("choose").onclick=() => $("file").click();
$("file").onchange=() => openFile($("file").files[0]);
let dragDepth=0;
$("drop-zone").addEventListener("dragenter",(event) => {event.preventDefault();dragDepth++;$("drop-zone").classList.add("dragging");});
$("drop-zone").addEventListener("dragover",(event) => event.preventDefault());
$("drop-zone").addEventListener("dragleave",() => {if (--dragDepth<=0) $("drop-zone").classList.remove("dragging");});
$("drop-zone").addEventListener("drop",(event) => {event.preventDefault();dragDepth=0;$("drop-zone").classList.remove("dragging");openFile(event.dataTransfer.files[0]);});
function compare(on) {if (!photo || opening || exporting) return;comparing=on;render();}
$("compare").addEventListener("pointerdown",(event) => {$("compare").setPointerCapture(event.pointerId);compare(true);});
for (const name of ["pointerup","pointercancel","lostpointercapture","blur"]) $("compare").addEventListener(name,() => compare(false));
$("compare").addEventListener("keydown",(event) => {if ([" ","Enter"].includes(event.key)) {event.preventDefault();compare(true);}});
$("compare").addEventListener("keyup",() => compare(false));
$("export").onclick=async () => {
  if (!photo || opening || exporting) return;
  compare(false);exporting=true;availability();busy("원본 해상도로 현상하고 JPEG를 만들고 있어요…");
  try {
    const response=await postImage(photo.id,"export",settings());
    const url=URL.createObjectURL(await response.blob()),link=document.createElement("a");
    link.href=url;link.download=photo.filename.replace(/\.nef$/i,"")+"-edited.jpg";
    document.body.append(link);link.click();link.remove();setTimeout(() => URL.revokeObjectURL(url),60000);
    status("JPEG 준비 완료 · 다운로드를 확인해 주세요");
  } catch (e) {error(e.message);}
  finally {exporting=false;busy(null);availability();if (!gpu) refreshCpu();}
};
document.addEventListener("keydown",(event) => {
  if (event.ctrlKey || event.metaKey || event.altKey || ["INPUT","TEXTAREA"].includes(event.target.tagName)) return;
  if (event.key.toLowerCase() === "o" && !opening && !exporting) $("file").click();
  if (event.key === "1") setMode("zoom"); if (event.key.toLowerCase() === "f") setMode("fit");
});
fetch("/api/health").then(checked).then((r) => r.json()).then((data) => {$("engine").textContent=`RAW ENGINE · LibRaw ${data.libraw}`;}).catch(() => error("서버에 연결할 수 없어요. 실행 상태를 확인해 주세요."));
