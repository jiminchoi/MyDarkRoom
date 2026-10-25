import {inverse,transform} from "./geometry-math.js";
const $=id=>document.getElementById(id);
const clone=v=>structuredClone(v);
const localDefaults={exposure:0,contrast:0,highlights:0,shadows:0,temperature:0,tint:0,saturation:0,dehaze:0,clarity:0,texture:0,sharpness:0};

export class MaskEditor {
  constructor({onChange,onView,onError,onPick}) {
    this.revision=0;this.keyRevision=-1;this.snapshotRevision=-1;this.strokeSnapshots=new WeakMap();
    this.onChange=commit=>{this.revision++;onChange(commit);};this.onView=onView;this.onError=onError;this.onPick=onPick;this.picking=false;
    this.items=[];this.selected=-1;this.bitmap=null;this.drawing=null;
    $("mask-panel").innerHTML=`<fieldset id="mask-tools" disabled><legend>부분 보정 · 마스크</legend>
      <div class="mask-row"><select id="mask-kind" aria-label="새 마스크 종류"><option value="brush">브러시</option><option value="gradient">선형 그라디언트</option><option value="radial">원형 그라디언트</option><option value="color">색상 범위</option><option value="luminance">휘도 범위</option></select><button id="mask-add">추가</button></div>
      <div class="mask-row"><select id="mask-select" aria-label="마스크 선택"></select><button id="mask-delete">삭제</button></div>
      <label class="check-label"><input id="mask-show" type="checkbox" checked> 선택 영역 표시</label>
      <fieldset id="mask-properties" disabled>
        <label>이름 <input id="mask-name" maxlength="60"></label>
        <div class="mask-row"><label class="check-label"><input id="mask-enabled" type="checkbox"> 사용</label><label class="check-label"><input id="mask-invert" type="checkbox"> 반전</label></div>
        <label>마스크 불투명도 <input id="mask-opacity" value="1" type="range" min="0" max="1" step=".01"></label>
        <label id="mask-draw-label" class="check-label"><input id="mask-draw" type="checkbox"> 사진에서 그리기 (끄면 이동)</label>
        <div id="brush-properties"><label>브러시 크기 <input id="brush-radius" type="range" min=".002" max=".25" step=".002" value=".04"></label><label>경계 부드러움 <input id="brush-feather" type="range" min="0" max="1" step=".01" value=".6"></label><label class="check-label"><input id="brush-erase" type="checkbox"> 지우개</label></div>
        <div id="color-properties"><label>선택할 색 <input id="mask-color" type="color" value="#ff0000"></label><button id="mask-pick">사진에서 색 찍기</button><label>색 허용 범위 <input id="mask-tolerance" value=".25" type="range" min=".001" max="1" step=".001"></label></div>
        <div id="luminance-properties"><label>휘도 하한 <input id="mask-low" value=".2" type="range" min="0" max="1" step=".01"></label><label>휘도 상한 <input id="mask-high" value=".8" type="range" min="0" max="1" step=".01"></label></div>
        <label id="range-feather-label">범위 경계 부드러움 <input id="mask-feather" value=".15" type="range" min="0" max="1" step=".01"></label>
        <div id="gradient-properties"><h3>거리별 농도</h3><canvas id="density-curve" width="256" height="100" aria-label="거리별 마스크 농도"></canvas><div id="density-controls"></div><button id="density-reset">농도 기본값</button><small id="density-help"></small></div>
        <div id="local-adjustments"><h3>이 마스크의 보정</h3></div>
      </fieldset><small id="mask-help">마스크를 추가해 사진 일부만 보정하세요. 최대 8개.</small></fieldset>`;
    for (const [key,label] of Object.entries({exposure:"노출",contrast:"대비",highlights:"하이라이트",shadows:"그림자",temperature:"색온도",tint:"틴트",saturation:"채도",dehaze:"디헤이즈",clarity:"명료도",texture:"텍스처",sharpness:"선예도"})) {
      const limit=key==="exposure" ? 3 : 100,step=key==="exposure" ? .01 : 1;
      const row=document.createElement("label");row.textContent=label;
      row.title=({dehaze:"뿌연 대비를 줄이거나 안개를 더합니다",clarity:"큰 밝기 구조의 대비를 조절합니다",texture:"미세 질감을 강조하거나 부드럽게 합니다",sharpness:"작은 윤곽을 또렷하게 하거나 부드럽게 합니다"})[key]||label;
      const out=document.createElement("output");out.id=`local-${key}-value`;row.append(out);
      const input=document.createElement("input");Object.assign(input,{id:`local-${key}`,type:"range",min:-limit,max:limit,step,value:0,defaultValue:"0"});row.append(input);$("local-adjustments").append(row);
      input.oninput=()=>{const m=this.current();if (!m) return;m.adjustments[key]=Number(input.value);out.textContent=input.value;this.onChange(false);};
      input.onchange=()=>this.onChange(true);
    }
    $("mask-add").onclick=()=>this.add($("mask-kind").value);
    $("mask-delete").onclick=()=>{if (this.selected<0) return;this.items.splice(this.selected,1);this.selected=Math.min(this.selected,this.items.length-1);this.refresh();this.onChange(true);};
    $("mask-select").onchange=()=>{this.selected=Number($("mask-select").value);this.refresh();this.onView();};
    $("mask-show").onchange=$("mask-draw").onchange=()=>this.onView();
    $("mask-name").onchange=()=>{if (!this.current()) return;this.current().name=$("mask-name").value.trim() || "마스크";this.refresh();this.onChange(true);};
    for (const key of ["enabled","invert","opacity","tolerance","low","high","feather"]) {
      const input=$("mask-"+key);
      const update=()=>{
        const m=this.current();if (!m) return;
        m[key]=input.type==="checkbox" ? input.checked : Number(input.value);
        if (m.low>m.high) {if (key==="low") m.high=m.low;else m.low=m.high;$("mask-low").value=m.low;$("mask-high").value=m.high;}
        this.onChange(false);
      };
      if (input.type==="checkbox") input.onchange=()=>{update();this.onChange(true);};
      else {input.oninput=update;input.onchange=()=>this.onChange(true);}
    }
    $("mask-color").oninput=()=>{const m=this.current();if (!m) return;const hex=$("mask-color").value;m.color=[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)/255);this.onChange(false);};
    $("mask-color").onchange=()=>this.onChange(true);
    $("mask-pick").onclick=()=>{this.picking=!this.picking;$("mask-pick").textContent=this.picking ? "사진 클릭 대기 · 취소" : "사진에서 색 찍기";this.onView();};
    for (let i=0;i<5;i++) {
      const row=document.createElement("label");row.textContent=`거리 ${i*25}%`;
      const output=document.createElement("output");output.id=`density-value-${i}`;row.append(output);
      const input=document.createElement("input");Object.assign(input,{id:`density-${i}`,type:"range",min:0,max:100,step:.1});row.append(input);$("density-controls").append(row);
      input.oninput=()=>{const m=this.current();if (!m) return;m.density=this.densities(m);m.density[i]=Number(input.value)/100;this.drawDensity();this.onChange(false);};
      input.onchange=()=>this.onChange(true);
    }
    $("density-reset").onclick=()=>{if (!this.current())return;this.current().density=null;this.refresh();this.onChange(true);};
    this.refresh();
  }
  densities(m) {return m.density ? [...m.density] : (m.kind==="radial" ? [1,.84375,.5,.15625,0] : [0,.15625,.5,.84375,1]);}
  drawDensity() {
    const m=this.current();if (!m) return;
    const values=this.densities(m),ctx=$("density-curve").getContext("2d"),w=256,h=100,knots=[0,.15625,.5,.84375,1];
    ctx.clearRect(0,0,w,h);ctx.strokeStyle="#3b4540";ctx.lineWidth=1;
    for (let i=0;i<5;i++) {ctx.beginPath();ctx.moveTo(i*(w-4)/4+2,2);ctx.lineTo(i*(w-4)/4+2,h-2);ctx.stroke();$("density-value-"+i).textContent=(values[i]*100).toFixed(1)+"%";}
    ctx.strokeStyle="#e8ad74";ctx.lineWidth=2;ctx.beginPath();
    for (let n=0;n<=128;n++) {
      const d=n/128,p=d*d*(3-2*d);let i=0;while (i<3 && p>knots[i+1]) i++;
      const t=(p-knots[i])/(knots[i+1]-knots[i]),value=values[i]*(1-t)+values[i+1]*t;
      if (!n)ctx.moveTo(2+d*(w-4),h-2-value*(h-4));else ctx.lineTo(2+d*(w-4),h-2-value*(h-4));
    }ctx.stroke();
  }
  current() {return this.items[this.selected];}
  get key() {if (this.keyRevision!==this.revision) {this.keyValue=JSON.stringify(this.items);this.keyRevision=this.revision;}return this.keyValue;}
  get colorMasks() {
    if(this.colorRevision!==this.revision){
      this.colorSnapshot=this.masks.map(m=>({...m,adjustments:Object.fromEntries(Object.entries(m.adjustments).filter(([k])=>!["dehaze","clarity","texture","sharpness"].includes(k)))})).filter(m=>Object.values(m.adjustments).some(v=>v!==0));
      this.colorKey=JSON.stringify(this.colorSnapshot);this.colorRevision=this.revision;
    }return this.colorSnapshot;
  }
  get bakeKey(){this.colorMasks;return this.colorKey;}
  get masks() {
    if (this.snapshotRevision!==this.revision) {
      this.snapshot=this.items.map(m=>{
        if (!this.strokeSnapshots.has(m.strokes)) this.strokeSnapshots.set(m.strokes,clone(m.strokes));
        return {...clone({...m,strokes:[]}),strokes:this.strokeSnapshots.get(m.strokes)};
      });this.snapshotRevision=this.revision;
    }
    return this.snapshot;
  }
  set masks(value) {this.revision++;this.items=clone(value).map(m=>({...m,adjustments:{...localDefaults,...m.adjustments}}));this.selected=Math.min(this.selected,this.items.length-1);if (this.selected<0 && this.items.length) this.selected=0;this.drawing=null;this.refresh();}
  add(kind) {
    if (this.items.length>=8) return this.onError("마스크는 최대 8개입니다.");
    const names={brush:"브러시",gradient:"그라디언트",radial:"원형",color:"색상",luminance:"휘도"};
    this.items.push({name:`${names[kind]} ${this.items.length+1}`,kind,enabled:true,invert:false,opacity:1,start:{x:kind==="radial"?.5:.2,y:.5},end:{x:kind==="radial"?.7:.8,y:.5},color:[1,0,0],tolerance:.25,low:.2,high:.8,feather:.15,strokes:[],adjustments:{...localDefaults}});
    this.selected=this.items.length-1;this.refresh();$("mask-draw").checked=["brush","gradient","radial"].includes(kind);this.onChange(true);
  }
  refresh() {
    this.picking=false;$("mask-pick").textContent="사진에서 색 찍기";
    $("mask-select").replaceChildren();
    for (const [i,m] of this.items.entries()) $("mask-select").add(new Option(`${i+1}. ${m.name}`,i));
    $("mask-select").value=String(this.selected);
    const m=this.current();$("mask-properties").disabled=!m;$("mask-delete").disabled=!m;
    if (!m) {$("mask-draw").checked=false;return;}
    $("mask-name").value=m.name;
    for (const key of ["enabled","invert","opacity","tolerance","low","high","feather"]) {
      const input=$("mask-"+key);if (input.type==="checkbox") input.checked=m[key];else input.value=m[key];
    }
    $("mask-color").value="#"+m.color.map(v=>Math.round(v*255).toString(16).padStart(2,"0")).join("");
    for (const key of Object.keys(localDefaults)) {$("local-"+key).value=m.adjustments[key];$("local-"+key+"-value").textContent=m.adjustments[key];}
    for (const type of ["brush","color","luminance"]) $(type+"-properties").hidden=m.kind!==type;
    $("range-feather-label").hidden=!["color","luminance"].includes(m.kind);
    $("mask-draw-label").hidden=!["brush","gradient","radial"].includes(m.kind);
    if (!["brush","gradient","radial"].includes(m.kind)) $("mask-draw").checked=false;
    $("gradient-properties").hidden=!["gradient","radial"].includes(m.kind);
    if (["gradient","radial"].includes(m.kind)) {
      const values=this.densities(m),base=this.densities({...m,density:null});for (let i=0;i<5;i++) {$("density-"+i).value=values[i]*100;$("density-"+i).defaultValue=base[i]*100;}
      $("density-help").textContent=m.kind==="radial" ? "거리 0%=중심, 100%=반경 경계. 바깥은 0%. 반전하면 외부를 보정합니다." : "거리 0%=시작점, 100%=끝점. 시작 전/끝 후에는 양 끝 농도가 유지됩니다.";
      this.drawDensity();
    }
    $("mask-help").textContent=m.kind==="radial" ? "사진의 중심점에서 반경 끝점까지 드래그해 원을 그리세요." : m.kind==="gradient" ? "사진에서 시작점→끝점을 드래그하고 거리별 농도를 조절하세요." : "선택 영역에만 보정됩니다. 반전·불투명도를 조절할 수 있습니다.";
  }
  get active() {return Boolean(this.current() && (this.picking || ($("mask-draw").checked && ["brush","gradient","radial"].includes(this.current().kind))));}
  down(point) {
    if (!this.active || !point) return false;
    const m=this.current();
    if (this.picking) {
      this.picking=false;$("mask-pick").textContent="사진에서 색 찍기";
      this.onPick(point).then(color=>{if (this.current()!==m) return;m.color=color;this.refresh();this.onChange(true);}).catch(e=>this.onError(e.message));
      return true;
    }
    if (m.kind==="brush" && (m.strokes.length>=64 || m.strokes.reduce((n,s)=>n+s.points.length,0)>=2048)) {this.onError("브러시 한도에 도달했습니다. 새 마스크를 추가하세요.");return true;}
    const paint=point.paint||{x:point.x,y:point.y};
    this.drawing=m.kind==="brush" ? {points:[paint],transform:point.transform||null,radius:Number($("brush-radius").value)*(point.brushScale||1),feather:Number($("brush-feather").value),erase:$("brush-erase").checked} : {start:paint,end:paint,transform:point.transform||null};
    this.onView();return true;
  }
  move(point) {
    if (!this.drawing || !point) return;
    if(this.drawing.transform){const p=transform(this.drawing.transform,[point.x,point.y]);point={x:p[0],y:p[1]};}else point={x:point.x,y:point.y};
    if (this.current().kind==="brush") {
      let points=this.drawing.points;const last=points.at(-1);
      if (Math.hypot(point.x-last.x,point.y-last.y)<.001) return;
      if (points.length>=256) this.drawing.points=points=points.filter((_,i)=>i%2===0);
      points.push(point);
    } else this.drawing.end=point;
    this.onView();
  }
  up(cancel=false) {
    if (!this.drawing) return false;
    const m=this.current();
    if (!cancel && m) {
      if (m.kind==="brush") {
        if (m.strokes.reduce((n,s)=>n+s.points.length,0)+this.drawing.points.length>2048) this.onError("브러시 포인트 한도에 도달했습니다. 새 마스크를 추가하세요.");
        else m.strokes=[...m.strokes,this.drawing];
      } else {m.start=this.drawing.start;m.end=this.drawing.end;m.transform=this.drawing.transform;}
    }
    this.drawing=null;if (!cancel) this.onChange(true);else this.onView();return true;
  }
  setOverlay(image) {this.bitmap?.close();this.bitmap=image;}
  warpOverlay(ctx,project,photo){
    const image=this.bitmap,n=16;
    const triangle=(a,b,c)=>{
      const src=[a,b,c].map(p=>[p[0]*image.width,p[1]*image.height]),dst=[a,b,c].map(p=>project({x:p[0],y:p[1]}));
      const [p,q,r]=src,den=(q[0]-p[0])*(r[1]-p[1])-(r[0]-p[0])*(q[1]-p[1]);if(!den||dst.flat().some(v=>!Number.isFinite(v)))return;
      const coeff=k=>{const u=((dst[1][k]-dst[0][k])*(r[1]-p[1])-(dst[2][k]-dst[0][k])*(q[1]-p[1]))/den,v=((q[0]-p[0])*(dst[2][k]-dst[0][k])-(r[0]-p[0])*(dst[1][k]-dst[0][k]))/den;return [u,v,dst[0][k]-u*p[0]-v*p[1]];};
      const x=coeff(0),y=coeff(1);ctx.save();ctx.beginPath();ctx.moveTo(...dst[0]);ctx.lineTo(...dst[1]);ctx.lineTo(...dst[2]);ctx.closePath();ctx.clip();ctx.transform(x[0],y[0],x[1],y[1],x[2],y[2]);ctx.drawImage(image,0,0);ctx.restore();
    };
    for(let y=0;y<n;y++)for(let x=0;x<n;x++){const a=[x/n,y/n],b=[(x+1)/n,y/n],c=[(x+1)/n,(y+1)/n],d=[x/n,(y+1)/n];triangle(a,b,c);triangle(a,c,d);}
  }
  draw(ctx,v,photo,width,height,{project=null,skipBitmap=false}={}) {
    ctx.clearRect(0,0,width,height);
    if (!$("mask-show").checked || !this.current()) return;
    const x=v.offset[0]-v.origin[0]*v.scale,y=v.offset[1]-v.origin[1]*v.scale;
    if(this.bitmap&&!skipBitmap){if(project)this.warpOverlay(ctx,project,photo);else ctx.drawImage(this.bitmap,x,y,photo.width*v.scale,photo.height*v.scale);}
    const data=this.drawing||this.current(),space=data.transform?inverse(data.transform):null;
    const pos=p=>{let q=[p.x,p.y];if(space)q=transform(space,q);return project?project({x:q[0],y:q[1]}):[x+q[0]*photo.width*v.scale,y+q[1]*photo.height*v.scale];};
    if (["gradient","radial"].includes(this.current().kind) && this.active) {
      const m=this.drawing || this.current();const a=pos(m.start),b=pos(m.end);
      ctx.strokeStyle="white";ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(...a);ctx.lineTo(...b);ctx.stroke();
      if (this.current().kind==="radial") {
        const r=Math.hypot((m.end.x-m.start.x)*photo.width,(m.end.y-m.start.y)*photo.height);ctx.beginPath();
        for(let i=0;i<=64;i++){const t=i/64*Math.PI*2,p=pos({x:m.start.x+Math.cos(t)*r/photo.width,y:m.start.y+Math.sin(t)*r/photo.height});if(!i)ctx.moveTo(...p);else ctx.lineTo(...p);}ctx.stroke();
      }
      for (const p of [a,b]) {ctx.beginPath();ctx.arc(...p,5,0,Math.PI*2);ctx.stroke();}
    }
    if (this.drawing?.points) {
      ctx.strokeStyle=this.drawing.erase ? "#52cfff99" : "#ff3c7099";ctx.fillStyle=ctx.strokeStyle;
      ctx.lineWidth=this.drawing.radius*2*Math.min(photo.width,photo.height)*v.scale;ctx.lineCap="round";ctx.lineJoin="round";
      ctx.beginPath();const points=this.drawing.points;ctx.moveTo(...pos(points[0]));for (const p of points.slice(1)) ctx.lineTo(...pos(p));ctx.stroke();
      if (points.length===1) {ctx.beginPath();ctx.arc(...pos(points[0]),ctx.lineWidth/2,0,Math.PI*2);ctx.fill();}
    }
  }
}
