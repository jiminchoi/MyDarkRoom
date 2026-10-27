import {forwardMatrix,multiply,transform,gpuGeometry,viewAt,anchoredPan,visibleRegion,projectedRegion,containsRegion} from "./geometry-math.js";
import {curveLut,curvePoints,validCurve} from "./curves.js";
import { GpuRenderer } from "./gpu.js";
import {GeometryEditor,geometryDefaults} from "./geometry.js";
let geometryEditor;
import { MaskEditor } from "./masks.js";
import {LocalSpatial,hasLocalSpatial} from "./local-spatial.js";
import {ExportDialog,saveToDirectory} from "./export.js";
import {EditSaver} from "./edit-save.js";
let maskEditor, maskBusy=false, appliedMaskKey="[]", maskTimer, maskGeneration=0, maskAbort;
let maskRequestRunning=false;
let overlayTimer, overlayGeneration=0, overlayAbort, overlayKey="",overlayPendingKey="";

const $ = (id) => document.getElementById(id);
const inputs = [...document.querySelectorAll('#controls input[type="range"]')];
let customCurve=null;
const defaults = {...Object.fromEntries(inputs.map((input) => [input.id, Number(input.defaultValue)])), monochrome:false,curve_points:null};
const settings = (includeMasks=true) => ({curve_points:customCurve ? structuredClone(customCurve) : null,...Object.fromEntries(inputs.map((input) => [input.id, Number(input.value)])), monochrome:$("monochrome").checked,geometry:geometryEditor?.value || geometryDefaults(),masks:includeMasks ? maskEditor?.masks || [] : []});
let canvas = $("preview"), gpu, context;
let photo = null, opening = false, exporting = false, comparing = false, split = false;
let closeSaving=false;
let zoomScale=1,zoomInteracting=false,panSpace=false,detailCache=null;
let mode = "fit", pan = [0,0], detail = null, cpuOriginal = null, cpuEdited = null;
let revision = 0, detailRevision = 0, editTimer, detailTimer, histogramTimer, frame, outputFrame;
let interacting=false,geometryRenderedRevision=-1;
let structureDetailAbort,structureAbort,structurePromise,structurePhoto,structureTimer,structureDetailKey="";
let cpuRenderedRevision=-1;
let cpuRunning = false, histogramRunning = false, detailAbort;
const worker = new Worker("/static/histogram-worker.js");

function error(message) { $("error-text").textContent = message; $("raw-help").hidden=!/DNG/i.test(message); $("error").hidden = false; }
function busy(message) { $("busy").hidden = !message; $("busy-text").textContent = message || ""; }
function status(message) { $("status").textContent = message; }
const editSaver=new EditSaver({getSettings:settings,onError:error,onState:state=>{
 const label=$("edit-save-status");label.dataset.state=state;
 label.textContent=({ready:"사진별 보정 자동 저장",dirty:"변경사항 있음 · 잠시 후 자동 저장",saving:"보정정보 저장 중…",saved:"보정정보 저장됨",error:"자동 저장 실패 · 다시 저장해 주세요"})[state];
}});
function availability() {
  const locked=!photo||opening||exporting||closeSaving;
  $("controls").disabled = $("preset-tools").disabled = locked;
  if (maskEditor) $("mask-tools").disabled=locked;
  if(geometryEditor){$("geometry-tools").disabled=locked;$("mask-tools").disabled ||= geometryEditor.active&&geometryEditor.editing;}
  historyButtons();
  for (const id of ["reset","compare","export","fit","zoom","split"]) $(id).disabled = locked;
  $("zoom").disabled ||= Boolean(geometryEditor?.active&&geometryEditor.editing);
  $("zoom-scale").disabled=locked||Boolean(geometryEditor?.active&&geometryEditor.editing);
  $("export").disabled ||= maskBusy;
  $("split-position").disabled = locked;
  $("open").disabled = $("choose").disabled = opening || exporting || closeSaving;
  for(const id of ["edit-save","edit-export","edit-import"])$(id).disabled=locked;
}
async function checked(response) {
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(typeof data.detail === "string" ? data.detail : `요청을 완료하지 못했어요 (${response.status}).`);
  }
  return response;
}
function updateOutputs() {
  drawCurve();
  $("tone-swatch").style.background=`hsl(${$("tone_hue").value} 65% 60%)`;
  const hue=Number($("tone_hue").value);$("tone-color").textContent=`${hue}° · ${["빨강","주황","노랑","연두","초록","청록","하늘","파랑","보라","자홍","분홍","빨강"][Math.min(11,Math.floor(hue/30))]}`;
  for (const input of inputs) document.querySelector(`output[for="${input.id}"]`).textContent = input.id === "exposure" ? Number(input.value).toFixed(2) : input.value;
}
async function postImage(id, kind, values, signal) {
  return checked(await fetch(`/api/photos/${id}/${kind}`, {
    method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(values), signal,
  }));
}
async function linearData(response,channels=3) {
  await checked(response);
  const buffer = await response.arrayBuffer();
  const data = {width:Number(response.headers.get("X-Image-Width")), height:Number(response.headers.get("X-Image-Height")),
    x:Number(response.headers.get("X-Image-X")), y:Number(response.headers.get("X-Image-Y")), span:[Number(response.headers.get("X-Region-Width")||response.headers.get("X-Image-Width")),Number(response.headers.get("X-Region-Height")||response.headers.get("X-Image-Height"))], pixels:new Uint16Array(buffer)};
  if (!data.width || !data.height || buffer.byteLength !== data.width*data.height*channels*2) throw new Error("미리보기 데이터가 올바르지 않습니다.");
  return data;
}
const localSpatial=new LocalSpatial({post:postImage,linear:linearData,onReady:()=>{syncLocal();queueRender();scheduleHistogram();},onError:error});
function syncLocal(){
 if(opening)return;
 localSpatial.sync(photo,gpu,maskEditor?.masks||[],canvas.dataset.detail?{region:canvas.dataset.detail,sampled:detailCache?.sampled}:null);
 const sharp=gpu?.localAmounts.some((v,i)=>i%4===3&&v!==0);
 canvas.dataset.localReady=String(Boolean(gpu?.localEnabled&&gpu.structureTexture&&(!sharp||gpu.sharpTexture)));
 canvas.dataset.localDetailReady=String(Boolean(gpu?.localDetailEnabled&&(gpu.structureDetailTexture||detailCache?.sampled)&&(!sharp||gpu.sharpDetailTexture||detailCache?.sampled)));
}
function gpuValues(){return {...settings(false),masks:maskEditor.colorMasks};}
function hasSpatial(){return ["dehaze","clarity","texture"].some(k=>Number($(k).value)!==0)||hasLocalSpatial(maskEditor?.masks||[]);}
async function ensureStructure(force=false){
 $("spatial-status").hidden=!gpu||!photo||!hasSpatial()||Boolean(gpu.structureTexture);
 if(!gpu||!photo||(!force&&!hasSpatial()))return;
 const id=photo.id;
 if(!gpu.structureTexture){
   if(!structurePromise||structurePhoto!==id){
     structurePhoto=id;structureAbort?.abort();const abort=new AbortController();structureAbort=abort;
     const promise=(async()=>{try{
       const data=await linearData(await fetch(`/api/photos/${id}/structure`,{signal:abort.signal}),4);
       if(photo?.id!==id||!gpu)return;gpu.loadStructure(data);$("spatial-status").hidden=true;canvas.dataset.structureReady="true";syncLocal();queueRender();scheduleHistogram();
     }catch(e){if(e.name!=="AbortError"&&photo?.id===id)error("질감 준비 실패: "+e.message);}finally{if(structurePromise===promise)structurePromise=null;}})();
     structurePromise=promise;
   }
   await structurePromise;
 }
 if(!gpu||photo?.id!==id||mode!=="zoom"||detailCache?.sampled||!hasSpatial()||!canvas.dataset.detail)return;
 const key=id+":"+canvas.dataset.detail;if(key===structureDetailKey)return;structureDetailKey=key;
 structureDetailAbort?.abort();const controller=new AbortController();structureDetailAbort=controller;
 const [x,y,width,height]=canvas.dataset.detail.split(',');
 try{const data=await linearData(await fetch(`/api/photos/${id}/structure?x=${x}&y=${y}&width=${width}&height=${height}`,{signal:controller.signal}),4);
   if(gpu&&photo?.id===id&&id+":"+canvas.dataset.detail===key){gpu.loadStructure(data,true);syncLocal();queueRender();}
 }catch(e){if(e.name!=="AbortError"&&structureDetailKey===key){structureDetailKey="";error("100% 질감 준비 실패: "+e.message);}}
}
function cpuCanvas() {
  localSpatial.reset();
  const previous=gpu;gpu=null;previous?.dispose();
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
  structureDetailAbort?.abort();structureDetailKey="";detailRevision++; detailAbort?.abort(); clearTimeout(detailTimer);
  if (detail) { closeBitmap(detail.original); closeBitmap(detail.edited); }
  detail = null;detailCache=null; gpu?.clearDetail(); delete canvas.dataset.detail;syncLocal();
}
async function loadCpu(next) {
  const image = await createImageBitmap(await (await postImage(next.id,"preview",defaults)).blob());
  if (cpuEdited !== cpuOriginal) closeBitmap(cpuEdited); closeBitmap(cpuOriginal);
  cpuOriginal = cpuEdited = image;cpuRenderedRevision=opening?revision:-1;
}
$("canvas").addEventListener("webglcontextlost", async (event) => {
  event.preventDefault(); if (!gpu) return;
  cpuCanvas(); clearDetail(); revision++;
  if (photo) {
    try { await loadCpu(photo); await refreshCpu();geometryEditor?.refresh(settings(),defaults); resize(); scheduleHistogram(); scheduleDetail(); }
    catch (e) { error(e.message); }
  }
  error("GPU 연결이 해제되어 CPU 미리보기로 전환했습니다.");
}, true);

function geometryState(){return photo&&geometryEditor?.active?gpuGeometry([photo.width,photo.height],geometryEditor.value,geometryEditor.editing):null;}
function outputSize(){return geometryState()?.size||[photo?.width||1,photo?.height||1];}
function view() {
 if(!photo)return {scale:1,origin:[0,0],offset:[0,0]};
 const size=outputSize(),viewport=[canvas.width,canvas.height],scale=mode==="fit"?Math.min(2,viewport[0]/size[0],viewport[1]/size[1]):zoomScale;
 const v=viewAt(size,viewport,scale,mode==="fit"?[0,0]:pan);if(mode!=="fit")pan=v.origin;return v;
}
function render() {
  if (!photo || !canvas.width || !canvas.height) return;
  canvas.dataset.view=JSON.stringify({...view(),size:outputSize()});
  $("mask-overlay").hidden=comparing;
  $("geometry-view").hidden=!geometryEditor?.active||(Boolean(gpu)&&!geometryEditor.editing);
  if(geometryEditor?.active){
    if(gpu){
      const state={geometry:geometryState(),view:view()};
      gpu.draw(settings(false),state.view,{before:comparing,split,splitPosition:Number($("split-position").value)/100,geometry:state.geometry,overlay:$("mask-show").checked});
      if(geometryRenderedRevision!==revision){geometryRenderedRevision=revision;status("기하 보정 미리보기 완료 · WebGL2");}
    }
    if(!gpu||geometryEditor.editing)geometryEditor.draw($("geometry-view").getContext("2d"),canvas.width,canvas.height,{before:comparing,split,fraction:Number($("split-position").value)/100,guidesOnly:Boolean(gpu),view:view(),detail:needsDetail()?detail:null});
    const state={geometry:geometryState(),view:view()},m=forwardMatrix([photo.width,photo.height],geometryEditor.value);
    const project=p=>{const q=transform(m,[p.x*photo.width,p.y*photo.height]);return [state.view.offset[0]+(q[0]-state.geometry.cropOrigin[0])*state.view.scale,state.view.offset[1]+(q[1]-state.geometry.cropOrigin[1])*state.view.scale];};
    drawMaskOverlay(state.view,project);
    $("compare-label").hidden=!comparing;$("split-line").hidden=!split||comparing;$("split-line").style.left=$("split-position").value+"%";
    canvas.dataset.renderRevision=revision;canvas.dataset.geometryRenderer=gpu?"webgl2":"cpu";return;
  }
  delete canvas.dataset.geometryRenderer;
  const v = view(), fraction = Number($("split-position").value)/100;
  if (gpu) gpu.draw(settings(false),v,{before:comparing,split,splitPosition:fraction,overlay:$("mask-show").checked});
  else if (cpuOriginal) {
    context.fillStyle = "#101212"; context.fillRect(0,0,canvas.width,canvas.height);
    const draw = (original) => {
      const image = original ? cpuOriginal : cpuEdited;
      context.imageSmoothingEnabled = v.scale<1;
      context.drawImage(image,v.offset[0]-v.origin[0]*v.scale,v.offset[1]-v.origin[1]*v.scale,photo.width*v.scale,photo.height*v.scale);
      if (detail && needsDetail()) {
        context.imageSmoothingEnabled = false;
        context.drawImage(original ? detail.original : detail.edited,v.offset[0]+(detail.x-v.origin[0])*v.scale,v.offset[1]+(detail.y-v.origin[1])*v.scale,detail.width*v.scale,detail.height*v.scale);
      }
    };
    draw(comparing);
    if (split && !comparing) {
      context.save(); context.beginPath(); context.rect(0,0,canvas.width*fraction,canvas.height); context.clip(); draw(true); context.restore();
    }
  }
  canvas.dataset.renderRevision=revision;
  drawMaskOverlay(v);
  $("compare-label").hidden = !comparing;
  $("split-line").hidden = !split || comparing;
  $("split-line").style.left = `${fraction*100}%`;
}
function queueRender() { cancelAnimationFrame(frame); frame = requestAnimationFrame(render); }
function resize() {
  const rect = $("canvas").getBoundingClientRect();
  canvas.width = Math.max(1,Math.round((rect.width-2)*devicePixelRatio));
  canvas.height = Math.max(1,Math.round((rect.height-2)*devicePixelRatio));
  $("geometry-view").width=canvas.width;$("geometry-view").height=canvas.height;
  $("mask-overlay").width=canvas.width;$("mask-overlay").height=canvas.height;
  updateViewButtons();render(); if (mode === "zoom") scheduleDetail();
}
new ResizeObserver(resize).observe($("canvas"));

async function openFile(file) {
  if (!file || opening || exporting || closeSaving) return;
  if (!/\.nef$/i.test(file.name)) return error("니콘 NEF 파일을 선택해 주세요.");
  if (file.size > 256*1024*1024) return error("사진 한 장의 최대 크기는 256 MB입니다.");
  opening=true;availability();
  try {await editSaver.flush();}
  catch(e){error(e.message);opening=false;availability();$("file").value="";return;}
  clearTimeout(structureTimer);structureAbort?.abort();structurePromise=null;structurePhoto=null;delete canvas.dataset.structureReady;$("spatial-status").hidden=true;
  opening = true;localSpatial.reset(); revision++; clearTimeout(editTimer); clearDetail(); availability();
  $("error").hidden = true; busy("NEF를 열고 400만 화소 미리보기를 준비하고 있어요…");
  try {
    const form = new FormData(); form.append("file",file);
    const next = await (await checked(await fetch("/api/photos",{method:"POST",body:form}))).json();
    if (gpu) {
      try { gpu.load(await linearData(await fetch(`/api/photos/${next.id}/pixels`)),next); }
      catch { cpuCanvas(); await loadCpu(next); }
    } else await loadCpu(next);
    const previousPhoto=photo;
    photo = next;
    if (previousPhoto) fetch(`/api/photos/${previousPhoto.id}`,{method:"DELETE"}).catch(()=>{});
    if (gpu) {if (cpuEdited!==cpuOriginal) closeBitmap(cpuEdited);closeBitmap(cpuOriginal);cpuOriginal=cpuEdited=null;}
    comparing = false; split = false; mode = "fit";zoomScale=1; pan = [0,0];
    clearTimeout(maskTimer);clearTimeout(overlayTimer);overlayGeneration++;overlayAbort?.abort();
    maskEditor.masks=[];maskEditor.setOverlay(null);overlayKey="";overlayPendingKey="";appliedMaskKey="[]";maskGeneration++;maskAbort?.abort();maskBusy=false;
    geometryEditor.clear();
    setControls(next.edits.settings || defaults);$("mask-draw").checked=false;
    editSaver.attach(next);history=[settings()]; historyIndex=0; $("preset").value=next.edits.settings?"":"base"; updateOutputs();
    $("empty").hidden = true; canvas.hidden = false;$("mask-overlay").hidden=false;
    $("filename").textContent = next.filename;
    $("dimensions").textContent = `${next.width} × ${next.height} · 미리보기 ${(next.preview_width*next.preview_height/1e6).toFixed(1)}MP`;
    updateViewButtons();resize();scheduleEdits();status(next.edits.settings?"저장한 보정을 복원했어요":"준비됐어요 · 원본은 안전하게");scheduleHistogram();structureTimer=setTimeout(()=>ensureStructure(true),700);
    if(next.edits.warning)error(next.edits.warning);
  } catch (e) { error(e.message); }
  finally { opening = false;syncLocal(); busy(null); availability(); $("file").value=""; }
}
function scheduleEdits(event) {
  if(photo&&!opening)editSaver.mark();
  if(!gpu&&detail)clearDetail();
  if (event?.target?.id.startsWith("curve_")) {customCurve=null;constrainCurve(event.target);}
  syncLocal();ensureStructure();
  revision++;cancelAnimationFrame(outputFrame);outputFrame=requestAnimationFrame(updateOutputs);
  if (gpu && maskBusy && maskEditor.bakeKey===appliedMaskKey) {maskGeneration++;clearTimeout(maskTimer);maskBusy=false;availability();}
  if (gpu && maskEditor.bakeKey!==appliedMaskKey) {scheduleMaskBake();}
  else if (gpu) { queueRender(); status(gpu.software ? "보정 완료 · WebGL2 소프트웨어" : "보정 완료 · WebGL2 미리보기"); scheduleHistogram(); }
  else if(!geometryEditor?.active) { clearTimeout(editTimer); status("보정 적용 중…"); editTimer=setTimeout(refreshCpu,100); }
  scheduleOverlay();
  availability();
  geometryEditor?.refresh(gpu?null:settings(),defaults,Boolean(gpu));
}
async function refreshCpu() {
  if (!photo || cpuRunning || opening || exporting || gpu || geometryEditor?.active) return;
  cpuRunning = true; const version=revision, id=photo.id;
  try {
    const image=await createImageBitmap(await (await postImage(id,"preview",{...settings(),geometry:geometryDefaults()})).blob());
    if (version === revision && photo.id === id) {
      if (cpuEdited !== cpuOriginal) closeBitmap(cpuEdited);
      cpuEdited=image;cpuRenderedRevision=version; clearDetail(); render(); scheduleHistogram(); scheduleDetail(); status("보정 완료 · CPU 미리보기");
    } else closeBitmap(image);
  } catch (e) { if (version === revision) error(e.message); }
  finally { cpuRunning=false; if (version !== revision && !opening && !exporting) refreshCpu(); }
}

function scheduleHistogram() { clearTimeout(histogramTimer);if(!interacting)histogramTimer=setTimeout(updateHistogram,220); }
// Keep full-resolution histogram readback off the slider/curve drag path.
document.addEventListener("pointerdown",event=>{if(event.target.matches('input[type="range"],#tone-curve')&&event.target.id!=="zoom-scale"){interacting=true;clearTimeout(histogramTimer);}},true);
for(const name of ["pointerup","pointercancel"])document.addEventListener(name,()=>{if(interacting){interacting=false;scheduleHistogram();}},true);
window.addEventListener("blur",()=>{interacting=false;scheduleHistogram();});
function updateHistogram() {
  if (!photo || opening || histogramRunning || maskBusy || interacting) return;
  if(!gpu&&(geometryEditor?.active ? geometryEditor.pending : cpuRenderedRevision!==revision))return;
  try {
    let pixels;
    if(gpu&&geometryEditor?.active){const state=geometryEditor.gpuState(canvas.width,canvas.height,true);pixels=gpu.histogram(settings(false),state.view,state.geometry);render();}
    else if (geometryEditor?.active && geometryEditor.bitmap) {
      const image=geometryEditor.bitmap,c=geometryEditor.editing?geometryEditor.value.crop:[0,0,1,1];
      const offscreen=document.createElement("canvas");offscreen.width=Math.max(1,Math.round(image.width*c[2]));offscreen.height=Math.max(1,Math.round(image.height*c[3]));
      const ctx=offscreen.getContext("2d");ctx.drawImage(image,c[0]*image.width,c[1]*image.height,c[2]*image.width,c[3]*image.height,0,0,offscreen.width,offscreen.height);pixels=ctx.getImageData(0,0,offscreen.width,offscreen.height).data;
    }
    else if (gpu) { pixels=gpu.histogram(settings(false),view()); render(); }
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
 $("fit").setAttribute("aria-pressed",mode==="fit");$("zoom").setAttribute("aria-pressed",mode!=="fit"&&Math.abs(zoomScale-1)<1e-6);
 $("split").setAttribute("aria-pressed",split);$("split-control").hidden=!split;
 $("canvas").classList.toggle("zoomed",mode!=="fit");
 const percent=view().scale*100;$("zoom-scale").value=Math.max(1,Math.min(200,percent));$("zoom-value").textContent=`${percent<10?percent.toFixed(1):Math.round(percent)}%`;
 $("preview").dataset.zoom=String(percent);
}
function setZoom(scale,point){
 if(!photo||opening||exporting||(geometryEditor?.active&&geometryEditor.editing))return;
 scale=Math.max(.01,Math.min(2,scale));const old=view(),anchor=point||[canvas.width/2,canvas.height/2];
 pan=anchoredPan(old,scale,anchor,outputSize(),[canvas.width,canvas.height]);zoomScale=scale;mode="zoom";
 updateViewButtons();queueRender();scheduleDetail();
}
function setMode(next,point){
 if(next==="zoom")return setZoom(1,point);
 if(!photo||opening||exporting)return;mode="fit";pan=[0,0];clearDetail();updateViewButtons();render();status("화면 맞춤 · 전체 사진");
}
function needsDetail(){return photo&&mode==="zoom"&&view().scale>Math.min(photo.preview_width/photo.width,photo.preview_height/photo.height)*.95;}
function scheduleDetail(){
 clearTimeout(detailTimer);detailRevision++;detailAbort?.abort();
 if(!needsDetail()||opening||zoomInteracting||drag)return;
 detailTimer=setTimeout(loadDetail,240);
}
async function loadDetail(){
 if(!needsDetail()||opening||zoomInteracting||drag)return;
 const v=view(),size=outputSize(),viewport=[canvas.width,canvas.height],id=photo.id,version=detailRevision;
 const geometry=geometryState(),visible=visibleRegion(v,size,viewport),expanded=visibleRegion(v,size,viewport,64);
 const inner=gpu?(geometry?projectedRegion(visible,geometry,[photo.width,photo.height],2):visible):visible;
 const region=gpu?(geometry?projectedRegion(expanded,geometry,[photo.width,photo.height],4):expanded):expanded;
 if(!region)return;
 const key=gpu?`${id}:${maskEditor.bakeKey}`:`${id}:${revision}:${JSON.stringify(geometryEditor?.active?geometryEditor.value:geometryDefaults())}`;
 if(detailCache?.key===key&&containsRegion(detailCache.region,inner)&&(!detailCache.sampled||v.scale<=detailCache.scale)){status("확대 · 캐시된 원본 영역 · 드래그로 이동");return;}
 detailAbort=new AbortController();const signal=detailAbort.signal;
 const query=`x=${region.x}&y=${region.y}&width=${region.width}&height=${region.height}`;
 status("확대 영역을 정밀하게 불러오는 중…");let original,edited;
 try{
   if(gpu){
     const values=gpuValues(),data=await linearData(await postImage(id,`region?${query}`,values,signal));
     if(version!==detailRevision||photo?.id!==id||mode!=="zoom")return;
     const masked=values.masks.length?await linearData(await postImage(id,`region?${query}&masked=true`,values,signal)):null;
     if(version!==detailRevision||photo?.id!==id||mode!=="zoom")return;
     gpu.loadDetail(data,masked);structureDetailKey="";
     detailCache={key,region:{x:data.x,y:data.y,width:data.span[0],height:data.span[1]},scale:v.scale,sampled:data.width!==data.span[0]||data.height!==data.span[1]};
     canvas.dataset.detail=`${data.x},${data.y},${data.span[0]},${data.span[1]}`;
   }else{
     const geo=geometryEditor?.active?geometryEditor.value:geometryDefaults();
     const responses=await Promise.all([postImage(id,`viewport?${query}`,{...defaults,geometry:geo},signal),postImage(id,`viewport?${query}`,{...settings(),geometry:geo},signal)]);
     original=await createImageBitmap(await responses[0].blob());edited=await createImageBitmap(await responses[1].blob());
     if(version!==detailRevision||photo?.id!==id||mode!=="zoom"){original.close();edited.close();return;}
     detail?.original.close();detail?.edited.close();
     const width=Number(responses[1].headers.get("X-Region-Width")),height=Number(responses[1].headers.get("X-Region-Height"));
     detail={original,edited,x:region.x,y:region.y,width,height};detailCache={key,scale:v.scale,sampled:edited.width!==width||edited.height!==height,region:{x:region.x,y:region.y,width,height}};
     canvas.dataset.detail=`${region.x},${region.y},${width},${height}`;
   }
   syncLocal();ensureStructure();render();status("확대 · 원본 영역 갱신 완료 · 드래그로 이동");
 }catch(e){original?.close();edited?.close();if(e.name!=="AbortError"&&version===detailRevision){error(e.message);status("축소 미리보기 표시 중 · 확대 영역을 다시 이동해 주세요");}}
}
let drag=null;
$("canvas").addEventListener("pointerdown",event=>{
 if(mode!=="zoom"||!photo||opening||exporting||(geometryEditor?.active&&geometryEditor.editing)||event.target.closest("#busy"))return;
 if(event.button!==1&&(event.button!==0||(!panSpace&&maskEditor?.active)))return;
 event.preventDefault();event.stopImmediatePropagation();$("canvas").setPointerCapture(event.pointerId);
 drag={id:event.pointerId,x:event.clientX,y:event.clientY,pan:[...view().origin],scale:view().scale};clearTimeout(detailTimer);detailRevision++;detailAbort?.abort();
},true);
$("canvas").addEventListener("pointermove",event=>{
 if(!drag||event.pointerId!==drag.id)return;event.preventDefault();event.stopImmediatePropagation();
 const box=canvas.getBoundingClientRect();pan=[drag.pan[0]-(event.clientX-drag.x)*canvas.width/box.width/drag.scale,drag.pan[1]-(event.clientY-drag.y)*canvas.height/box.height/drag.scale];queueRender();
},true);
for(const name of ["pointerup","pointercancel","lostpointercapture"])$("canvas").addEventListener(name,event=>{if(drag&&event.pointerId===drag.id){event.stopImmediatePropagation();drag=null;render();scheduleDetail();}},true);
$("canvas").addEventListener("wheel",event=>{
 if(!photo||opening||exporting||(geometryEditor?.active&&geometryEditor.editing))return;
 event.preventDefault();const box=canvas.getBoundingClientRect(),delta=event.deltaY*(event.deltaMode===1?16:event.deltaMode===2?box.height:1);
 setZoom(view().scale*Math.exp(-Math.max(-240,Math.min(240,delta))*.002),[(event.clientX-box.left)*canvas.width/box.width,(event.clientY-box.top)*canvas.height/box.height]);
},{passive:false});
$("canvas").addEventListener("dblclick",event=>{if(maskEditor?.active||(geometryEditor?.active&&geometryEditor.editing))return;const box=canvas.getBoundingClientRect();setMode(mode==="fit"?"zoom":"fit",[(event.clientX-box.left)*canvas.width/box.width,(event.clientY-box.top)*canvas.height/box.height]);});
$("fit").onclick=()=>setMode("fit");$("zoom").onclick=()=>setZoom(1);
$("zoom-scale").oninput=()=>setZoom(Number($("zoom-scale").value)/100);
$("zoom-scale").onchange=()=>scheduleDetail();
$("zoom-scale").addEventListener("pointerdown",()=>{zoomInteracting=true;clearTimeout(detailTimer);});
for(const name of ["pointerup","pointercancel"])document.addEventListener(name,()=>{if(zoomInteracting){zoomInteracting=false;scheduleDetail();}},true);
document.addEventListener("keydown",event=>{if(event.code==="Space"&&event.target.id!=="compare"&&!['TEXTAREA','SELECT'].includes(event.target.tagName)&&!(event.target.tagName==="INPUT"&&event.target.type!=="range")){panSpace=true;event.preventDefault();}});
document.addEventListener("keyup",event=>{if(event.code==="Space")panSpace=false;});
window.addEventListener("blur",()=>{panSpace=false;zoomInteracting=false;drag=null;});
$("split").onclick=() => {split=!split;comparing=false;updateViewButtons();render();};
$("split-position").oninput=queueRender;
inputs.forEach((input) => input.addEventListener("input",scheduleEdits));
$("reset").onclick=() => applySettings({...defaults,masks:[],geometry:geometryDefaults()});
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
const exportDialog=new ExportDialog(()=>{if(!photo)return null;const c=geometryEditor?.value.crop || [0,0,1,1];return {...photo,width:Math.round(photo.width*c[2]),height:Math.round(photo.height*c[3])};},async options=>{
  if (!photo || opening || exporting || maskBusy) return;
  compare(false);exporting=true;availability();busy("원본 해상도로 현상하고 JPEG를 만들고 있어요…");
  try {
    if(window.pywebview?.api?.export_jpeg){
      const result=await window.pywebview.api.export_jpeg(photo.id,settings(),{filename:options.filename,longEdge:options.longEdge,dpi:options.dpi,quality:options.quality,directory_token:options.directory?.native_token||null});
      status(result.cancelled?"저장을 취소했어요":`저장 완료 · ${result.folder} / ${result.filename}`);return;
    }
    const response=await postImage(photo.id,`export?long_edge=${options.longEdge}&dpi=${options.dpi}&quality=${options.quality}`,settings());
    const blob=await response.blob();
    if (options.directory) {
      const filename=await saveToDirectory(options.directory,options.filename,blob);
      status(`저장 완료 · ${options.directory.name} / ${filename}`);
    } else {
      const url=URL.createObjectURL(blob),link=document.createElement("a");
      link.href=url;link.download=options.filename;document.body.append(link);link.click();link.remove();
      setTimeout(()=>URL.revokeObjectURL(url),1000);status("JPEG 준비 완료 · 다운로드를 확인해 주세요");
    }
  } catch(e) {error("JPEG 저장 실패: "+e.message);}
  finally {exporting=false;busy(null);availability();if (!gpu) refreshCpu();}
});
$("export").onclick=()=>{if (photo&&!opening&&!exporting&&!maskBusy) exportDialog.show();};

document.addEventListener("keydown",(event) => {
  if (["TEXTAREA","SELECT"].includes(event.target.tagName) || (event.target.tagName==="INPUT" && !["range","checkbox"].includes(event.target.type))) return;
  if ((event.ctrlKey || event.metaKey) && ["z","y"].includes(event.key.toLowerCase())) {
    event.preventDefault(); travel(event.key.toLowerCase()==="y" || event.shiftKey ? 1 : -1); return;
  }
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.key.toLowerCase() === "o" && !opening && !exporting) $("file").click();
  if (event.key === "1") setMode("zoom"); if (event.key.toLowerCase() === "f") setMode("fit");
});
fetch("/api/health").then(checked).then((r) => r.json()).then((data) => {$("engine").textContent=`MyDarkRoom ${data.app_version || ""} · LibRaw ${data.libraw} · DNG ${data.dng_converter ? "감지됨" : "없음"}`;}).catch(() => error("서버에 연결할 수 없어요. 실행 상태를 확인해 주세요."));

let history=[], historyIndex=-1;
const storageKey="mydarkroom.presets.v1";
const builtins=[
 {id:"base",name:"기본 현상",values:defaults},
 {id:"mono",name:"클래식 흑백",values:{...defaults,monochrome:true,contrast:20}},
 {id:"portrait",name:"부드러운 인물",values:{...defaults,contrast:-15,shadows:20,saturation:-10}},
 {id:"warm",name:"따뜻한 필름",values:{...defaults,temperature:15,tone_hue:35,tone_amount:18,grain:25,grain_size:2}},
 {id:"silver",name:"실버 그레인",values:{...defaults,monochrome:true,mix_red:45,mix_green:45,mix_blue:10,contrast:30,grain:45}}
];
let custom=[];
function curveDefaults(v) {return {dehaze:0,clarity:0,texture:0,curve_points:null,...Object.fromEntries(Object.entries(defaults).filter(([k])=>k.startsWith("curve_"))),...v};}
function validValues(v) {
 v=curveDefaults(v);
 const points=["black","shadow","mid","light","white"].map(k=>v["curve_"+k]);
 return validCurve(v.curve_points) && points.every((v,i)=>!i||v>=points[i-1]) && v && typeof v.monochrome==="boolean" && inputs.every(i=>Number.isFinite(v[i.id]) && v[i.id]>=Number(i.min) && v[i.id]<=Number(i.max));
}
try {
 const saved=JSON.parse(localStorage.getItem(storageKey) || "[]");
 if (!Array.isArray(saved)) throw new Error("invalid presets");
 custom=saved.filter(p=>typeof p.id==="string" && p.id.startsWith("user-") && typeof p.name==="string" && p.name.length<=60 && validValues(p.values)).slice(0,100).map(p=>({...p,values:curveDefaults(p.values)}));
} catch {error("저장한 프리셋을 읽지 못했습니다. 기본 프리셋을 사용합니다.");}
function listPresets(selected="") {
 $("preset").replaceChildren();
 const placeholder=new Option("직접 보정",""); $("preset").add(placeholder);
 for (const p of [...builtins,...custom]) $("preset").add(new Option(p.name,p.id));
 $("preset").value=selected;
 $("delete-preset").disabled=!custom.some(p=>p.id===selected);
}
function setControls(v) {
 v=curveDefaults(v);
 customCurve=v.curve_points ? structuredClone(v.curve_points) : null;
 for (const input of inputs) input.value=v[input.id];
 $("monochrome").checked=v.monochrome;
 if (v.geometry && geometryEditor) geometryEditor.set(v.geometry);
 if (Array.isArray(v.masks) && maskEditor) maskEditor.masks=v.masks;
}
function historyButtons() {
 const locked=!photo || opening || exporting || closeSaving;
 $("undo").disabled=locked || historyIndex<=0; $("redo").disabled=locked || historyIndex>=history.length-1;
}
function record() {
 const next=settings();
 if (JSON.stringify(next)===JSON.stringify(history[historyIndex])) return;
 history=history.slice(0,historyIndex+1); history.push(next);
 if (history.length>100) history.shift();
 historyIndex=history.length-1; historyButtons();
 $("preset").value=""; $("delete-preset").disabled=true;
}
function applySettings(v) {setControls(v);scheduleEdits();record();}
function travel(direction) {
 if (!photo || opening || exporting || closeSaving) return;
 record();
 const index=historyIndex+direction;
 if (index<0 || index>=history.length) return;
 historyIndex=index;setControls(history[index]);scheduleEdits();historyButtons();$("preset").value="";$("delete-preset").disabled=true;
}
for (const input of inputs) input.addEventListener("change",record);
$("monochrome").onchange=()=>{scheduleEdits();record();};
$("undo").onclick=()=>travel(-1);$("redo").onclick=()=>travel(1);
$("preset").onchange=()=>{
 const p=[...builtins,...custom].find(p=>p.id===$("preset").value);
 if (p) {applySettings(p.values);$("preset").value=p.id;}
 $("delete-preset").disabled=!p || !custom.includes(p);
};
function persist(next) {
 try {localStorage.setItem(storageKey,JSON.stringify(next));custom=next;return true;}
 catch {error("프리셋을 저장하지 못했습니다. 브라우저 저장 공간/설정을 확인하세요.");return false;}
}
$("save-preset").onclick=()=>{
 const name=$("preset-name").value.trim();
 if (!name) return error("프리셋 이름을 입력해 주세요.");
 if (custom.length>=100) return error("사용자 프리셋은 최대 100개입니다.");
 const p={id:"user-"+crypto.randomUUID(),name,values:Object.fromEntries(Object.keys(defaults).map(k=>[k,settings()[k]]))};
 if (persist([...custom,p])) {listPresets(p.id);$("preset-name").value="";status("프리셋을 이 브라우저에 저장했습니다");}
};
$("delete-preset").onclick=()=>{
 const id=$("preset").value;
 if (persist(custom.filter(p=>p.id!==id))) listPresets();
};
listPresets("base");

$("edit-save").onclick=async()=>{try{await editSaver.flush();}catch(e){error(e.message);}};
$("edit-export").onclick=async()=>{
 if(!photo||opening||exporting||closeSaving)return;
 const id=photo.id,name=photo.filename.replace(/\.nef$/i,"");
 try{
  const response=await postImage(id,"edits/export",settings());
  const url=URL.createObjectURL(await response.blob()),link=document.createElement("a");
  link.href=url;link.download=name+".mydarkroom.json";document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
 }catch(e){error(e.message);}
};
$("edit-import").onclick=()=>$("edit-import-file").click();
$("edit-import-file").onchange=async()=>{
 const file=$("edit-import-file").files[0];if(!file||!photo||opening||exporting||closeSaving)return;
 const id=photo.id,version=revision;
 try{
  if(file.size>4*1024*1024)throw new Error("보정정보 파일은 최대 4 MB입니다.");
  const response=await checked(await fetch(`/api/photos/${id}/edits/import`,{method:"POST",headers:{"Content-Type":"application/json"},body:await file.text()}));
  const data=await response.json();
  if(photo?.id!==id||revision!==version)throw new Error("사진의 편집 상태가 바뀌었습니다. 보정정보를 다시 불러와 주세요.");
  geometryEditor.clear();$("mask-draw").checked=false;mode="fit";pan=[0,0];clearDetail();applySettings(data.settings);updateViewButtons();
  await editSaver.flush();status("보정정보를 불러와 저장했습니다");
 }catch(e){error(e.message);}finally{$("edit-import-file").value="";}
};
window.mydarkroomFlushEdits=async()=>{
 closeSaving=true;availability();
 try{await editSaver.flush();return {ok:true};}
 catch(e){error(e.message);return {ok:false,error:e.message};}
 finally{closeSaving=false;availability();}
};
window.addEventListener("beforeunload",event=>{
 if(editSaver.dirty||editSaver.running){editSaver.flush().catch(()=>{});event.preventDefault();event.returnValue="";}
});

$("preset-export").onclick=()=>{
 const data={version:1,presets:custom};
 const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:"application/json"}));
 const link=document.createElement("a");link.href=url;link.download="mydarkroom-presets.json";
 document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
};
$("preset-import").onclick=()=>$("preset-import-file").click();
$("preset-import-file").onchange=async()=>{
 const file=$("preset-import-file").files[0]; if (!file) return;
 try {
  if (file.size>1024*1024) throw new Error("프리셋 파일은 최대 1 MB입니다.");
  const data=JSON.parse(await file.text());
  if (data.version!==1 || !Array.isArray(data.presets) || !data.presets.length || custom.length+data.presets.length>100) throw new Error("프리셋 파일 형식 또는 개수를 확인하세요.");
  const entries=data.presets.map(p=>{
   if (!p || typeof p.name!=="string" || !p.name.trim() || p.name.length>60 || !validValues(p.values)) throw new Error("올바르지 않은 프리셋 값입니다.");
   return {id:"user-"+crypto.randomUUID(),name:p.name,values:Object.fromEntries(Object.keys(defaults).map(k=>[k,curveDefaults(p.values)[k]]))};
  });
  if (persist([...custom,...entries])) {applySettings(entries[0].values);listPresets(entries[0].id);status("프리셋 파일을 불러와 첫 프리셋을 적용했습니다.");}
 } catch (e) {error("프리셋을 불러오지 못했습니다: "+e.message);}
 finally {$("preset-import-file").value="";}
};

function drawMaskOverlay(v,project=null) {
 const layer=$("mask-overlay"),ctx=layer.getContext("2d");
 ctx.clearRect(0,0,layer.width,layer.height);
 if (photo && !comparing && maskEditor) {ctx.save();if(project){const size=outputSize(),r=[v.offset[0]-v.origin[0]*v.scale,v.offset[1]-v.origin[1]*v.scale,size[0]*v.scale,size[1]*v.scale];ctx.beginPath();ctx.rect(...r);ctx.clip();}maskEditor.draw(ctx,v,photo,layer.width,layer.height,{project,skipBitmap:Boolean(gpu)});ctx.restore();}
}
function scheduleMaskBake() {
 clearTimeout(maskTimer);const generation=++maskGeneration;
 maskBusy=true;availability();status("마스크 보정을 적용하고 있어요…");
 maskTimer=setTimeout(async()=>{
  if (maskRequestRunning || !photo || !gpu || opening) return;
  maskRequestRunning=true;
  const id=photo.id,values=gpuValues(),key=maskEditor.bakeKey;
  try {
   if (!values.masks.length) {gpu.clearMasked();appliedMaskKey=key;}
   else {
    maskAbort=new AbortController();
    const data=await linearData(await postImage(id,"masked-pixels",values,maskAbort.signal));
    if (photo.id!==id || key!==maskEditor.bakeKey) return;
    gpu.loadMasked(data);appliedMaskKey=key;
   }
   clearDetail();render();scheduleDetail();scheduleHistogram();status("마스크 보정 완료");
  } catch (e) {if (e.name!=="AbortError" && generation===maskGeneration) error("마스크 보정 실패: "+e.message);}
  finally {
    maskRequestRunning=false;
    if (generation!==maskGeneration && gpu && !opening && maskEditor.bakeKey!==appliedMaskKey) scheduleMaskBake();
    else {clearTimeout(maskTimer);maskBusy=false;availability();scheduleHistogram();}
  }
 },120);
}
function scheduleOverlay() {
 if (!photo || !maskEditor) return;
 if(!$("mask-show").checked){clearTimeout(overlayTimer);overlayAbort?.abort();overlayGeneration++;overlayPendingKey="";return;}
 const selected=maskEditor.selected,mask=maskEditor.current();
 const geometry=mask ? {...mask,adjustments:undefined} : null;
 const key=photo.id+":"+selected+":"+JSON.stringify(geometry);
 if (key===overlayKey||key===overlayPendingKey) return;
 clearTimeout(overlayTimer);overlayPendingKey=key;
 const generation=++overlayGeneration;overlayAbort?.abort();maskEditor.setOverlay(null);gpu?.setOverlay(null);overlayKey="";$("mask-overlay").dataset.ready="false";queueRender();
 if (!mask) {overlayKey=key;overlayPendingKey="";return;}
 overlayTimer=setTimeout(async()=>{
  const id=photo.id;
  try {
   overlayAbort=new AbortController();
   const response=await postImage(id,`mask-overlay?selected=${selected}`,settings(),overlayAbort.signal);
   const image=await createImageBitmap(await response.blob());
   if (generation!==overlayGeneration || photo.id!==id) {image.close();return;}
   maskEditor.setOverlay(image);gpu?.setOverlay(image);$("mask-overlay").dataset.ready="true";overlayKey=key;overlayPendingKey="";queueRender();
  } catch (e) {if(generation===overlayGeneration)overlayPendingKey="";if (e.name!=="AbortError" && generation===overlayGeneration) error("마스크 표시 실패: "+e.message);}
 },140);
}
maskEditor=new MaskEditor({onChange:(commit)=>{scheduleEdits();if (commit) record();},onView:()=>{queueRender();if (!maskEditor?.drawing) scheduleOverlay();},onError:error,
 onPick:async point=>{const response=await checked(await fetch(`/api/photos/${photo.id}/sample-color?x=${point.x}&y=${point.y}`));return (await response.json()).color;}});
function imagePoint(event) {
 if (!photo) return null;
 const rect=canvas.getBoundingClientRect();
 if(geometryEditor?.active){
   const state={geometry:geometryState(),view:view()},px=(event.clientX-rect.left)*canvas.width/rect.width,py=(event.clientY-rect.top)*canvas.height/rect.height;
   const q=[(px-state.view.offset[0])/state.view.scale+state.view.origin[0],(py-state.view.offset[1])/state.view.scale+state.view.origin[1]];
   if(q[0]<0||q[1]<0||q[0]>=state.geometry.size[0]||q[1]>=state.geometry.size[1])return null;
   q[0]+=state.geometry.cropOrigin[0];q[1]+=state.geometry.cropOrigin[1];const raw=transform(state.geometry.inverse,q);
   if(raw[0]<0||raw[1]<0||raw[0]>photo.width||raw[1]>photo.height)return null;
   const m=forwardMatrix([photo.width,photo.height],geometryEditor.value),d=[photo.width,0,0,0,photo.height,0,0,0,1],n=[1/photo.width,0,0,0,1/photo.height,0,0,0,1];
   return {x:raw[0]/photo.width,y:raw[1]/photo.height,paint:{x:q[0]/photo.width,y:q[1]/photo.height},transform:multiply(n,multiply(m,d)),brushScale:Math.min(...state.geometry.size)/Math.min(photo.width,photo.height)};
 }
 const v=view();
 const x=((event.clientX-rect.left)*canvas.width/rect.width-v.offset[0])/v.scale+v.origin[0];
 const y=((event.clientY-rect.top)*canvas.height/rect.height-v.offset[1])/v.scale+v.origin[1];
 if (x<0 || y<0 || x>photo.width || y>photo.height) return null;
 return {x:x/photo.width,y:y/photo.height};
}
let maskPointer=null;
$("canvas").addEventListener("pointerdown",event=>{
 if (!photo || opening || exporting || !maskEditor.active || (geometryEditor?.active&&geometryEditor.editing) || event.button!==0) return;
 if (!maskEditor.down(imagePoint(event))) return;
 event.preventDefault();event.stopImmediatePropagation();maskPointer=event.pointerId;$("canvas").setPointerCapture(event.pointerId);
},true);
$("canvas").addEventListener("pointermove",event=>{
 if (maskPointer!==event.pointerId) return;
 event.preventDefault();event.stopImmediatePropagation();maskEditor.move(imagePoint(event));
},true);
for (const name of ["pointerup","pointercancel","lostpointercapture"]) $("canvas").addEventListener(name,event=>{
 if (maskPointer!==event.pointerId) return;
 event.preventDefault();event.stopImmediatePropagation();maskPointer=null;maskEditor.up(name==="pointercancel");
},true);

const curveIds=["black","shadow","mid","light","white"].map(k=>"curve_"+k);
function constrainCurve(input) {
 const i=curveIds.indexOf(input.id);if (i<0) return;
 input.value=Math.max(i?Number($(curveIds[i-1]).value):0,Math.min(i<4?Number($(curveIds[i+1]).value):100,Number(input.value)));
}
function drawCurve() {
 const chart=$("tone-curve");if (!chart) return;
 const ctx=chart.getContext("2d"),w=chart.width,h=chart.height;ctx.clearRect(0,0,w,h);
 ctx.strokeStyle="#3b4540";ctx.lineWidth=1;
 for(let i=0;i<=4;i++){ctx.beginPath();ctx.moveTo(i*w/4,0);ctx.lineTo(i*w/4,h);ctx.moveTo(0,i*h/4);ctx.lineTo(w,i*h/4);ctx.stroke();}
 const values=settings(false),lut=curveLut(values);ctx.strokeStyle="#e8ad74";ctx.lineWidth=2;ctx.beginPath();
 lut.forEach((v,i)=>{const x=4+i/255*(w-8),y=h-4-v*(h-8);if(!i)ctx.moveTo(x,y);else ctx.lineTo(x,y);});ctx.stroke();
 for(const [x,y] of curvePoints(values)){ctx.beginPath();ctx.arc(4+x*(w-8),h-4-y*(h-8),4,0,Math.PI*2);ctx.fillStyle="#e8ad74";ctx.fill();}
}
let curveDrag=-1,curveBefore=null;
function curvePosition(event){const r=$("tone-curve").getBoundingClientRect();return [Math.max(0,Math.min(1,(event.clientX-r.left-4*r.width/256)/(r.width*248/256))),Math.max(0,Math.min(1,1-(event.clientY-r.top-4*r.height/180)/(r.height*172/180)))];}
function nearestCurve(point){const r=$("tone-curve").getBoundingClientRect();return curvePoints(settings(false)).findIndex(p=>Math.hypot((p[0]-point[0])*r.width,(p[1]-point[1])*r.height)<12);}
$("tone-curve").onpointerdown=event=>{
 if(!photo||opening||exporting||event.button!==0)return;
 const point=curvePosition(event);curveBefore=customCurve?structuredClone(customCurve):null;curveDrag=nearestCurve(point);
 const next=structuredClone(curvePoints(settings(false)));
 if(curveDrag<0){if(next.length>=32||next.some(p=>Math.abs(p[0]-point[0])<.002))return;next.push(point);next.sort((a,b)=>a[0]-b[0]);curveDrag=next.indexOf(point);}
 customCurve=next;
 $("tone-curve").setPointerCapture(event.pointerId);scheduleEdits();
};
function curveMove(event){
 if(curveDrag<0)return;
 const p=curvePosition(event),i=curveDrag,n=customCurve.length;
 p[0]=i===0?0:i===n-1?1:Math.max(customCurve[i-1][0]+.002,Math.min(customCurve[i+1][0]-.002,p[0]));customCurve[i]=p;scheduleEdits();
}
$("tone-curve").onpointermove=curveMove;
for(const name of ["pointerup","pointercancel","lostpointercapture"])$("tone-curve").addEventListener(name,()=>{if(curveDrag>=0){if(name==="pointercancel"){customCurve=curveBefore;scheduleEdits();}curveDrag=-1;record();}});
$("tone-curve").ondblclick=event=>{if(!photo||opening||exporting)return;const i=nearestCurve(curvePosition(event));if(i>0&&i<customCurve?.length-1){customCurve.splice(i,1);scheduleEdits();record();}};
$("curve-reset").onclick=()=>{customCurve=[[0,0],[1,1]];for(const id of curveIds)$(id).value=defaults[id];scheduleEdits();record();};
document.addEventListener("keydown",event=>{
 const input=event.target;
 if (!(input instanceof HTMLInputElement) || input.type!=="range" || !["ArrowLeft","ArrowRight"].includes(event.key) || input.disabled) return;
 if (input.closest("fieldset:disabled")) return;
 event.preventDefault();event.stopImmediatePropagation();
 if (event.key==="ArrowRight") input.stepUp();else input.stepDown();
 input.dispatchEvent(new Event("input",{bubbles:true}));input.dispatchEvent(new Event("change",{bubbles:true}));
},true);
drawCurve();
window.addEventListener("pagehide",event=>{
 if (event.persisted) return;
 geometryEditor?.clear();
 clearTimeout(structureTimer);structureAbort?.abort();structureDetailAbort?.abort();localSpatial.reset();worker.terminate();gpu?.dispose();
 if (cpuEdited!==cpuOriginal) closeBitmap(cpuEdited);closeBitmap(cpuOriginal);
 if (detail) {closeBitmap(detail.original);closeBitmap(detail.edited);}maskEditor?.setOverlay(null);
});

geometryEditor=new GeometryEditor({getPhoto:()=>photo,onChange:commit=>{mode="fit";pan=[0,0];clearDetail();updateViewButtons();availability();scheduleEdits();if(commit)record();},onView:()=>{queueRender();scheduleHistogram();if(!gpu&&mode==="zoom")scheduleDetail();},onError:error,onMessage:status});
let geometryPointer=null;
$("canvas").addEventListener("pointerdown",event=>{if(opening||exporting||!photo||event.button!==0)return;if(geometryEditor.down(event)){event.preventDefault();event.stopImmediatePropagation();geometryPointer=event.pointerId;$("canvas").setPointerCapture(event.pointerId);}},true);
$("canvas").addEventListener("pointermove",event=>{if(geometryPointer!==event.pointerId){geometryEditor.hover(event);return;}event.preventDefault();event.stopImmediatePropagation();geometryEditor.move(event);},true);
for(const name of ["pointerup","pointercancel","lostpointercapture"])$("canvas").addEventListener(name,event=>{if(geometryPointer!==event.pointerId)return;event.stopImmediatePropagation();geometryPointer=null;geometryEditor.up(name==="pointercancel");},true);

// Native ranges retain keyboard operation; double-click restores each own default.
document.addEventListener("dblclick",event=>{
 const input=event.target.closest('input[type="range"]');
 if(!input||input.matches(':disabled')||input.closest('fieldset:disabled'))return;
 input.value=input.defaultValue;input.dispatchEvent(new Event("input",{bubbles:true}));input.dispatchEvent(new Event("change",{bubbles:true}));
});
$("auto-light").onclick=async()=>{
 if(!photo||opening||exporting)return;const id=photo.id,version=revision;$("auto-light").disabled=true;
 try{const response=await checked(await fetch(`/api/photos/${id}/auto-light`,{method:"POST"}));const result=await response.json();
 if(photo?.id!==id||revision!==version)return;
 for(const [key,value] of Object.entries(result.adjustments))$(key).value=value;
 scheduleEdits();record();status("자동 밝기 보정 완료 · 채도와 색 설정 유지");
 }catch(e){error(e.message);}finally{$("auto-light").disabled=false;}
};
// Categories have stable DOM controls, keeping disabled states and preset semantics.
function category(element,title,key){
 const details=document.createElement("details"),summary=document.createElement("summary");details.className="category";details.dataset.category=key;summary.textContent=title;
 details.open=key==="빛";
 element.before(details);details.append(summary,element);
 return details;
}
category($("preset-tools"),"프리셋","presets");
const groups=[...document.querySelectorAll("#controls > .control-group")];
for(const name of ["빛","색","톤 커브","흑백 채널 믹서","색조","그레인"]){
 const group=groups.find(g=>g.querySelector("h3").firstChild.textContent.trim()===name);$("controls").append(group);const heading=group.querySelector("h3");heading.hidden=true;category(group,name,name);
}
category($("geometry-panel"),"크롭 · 수평 · 업라이트","geometry");category($("mask-panel"),"마스크","masks");
