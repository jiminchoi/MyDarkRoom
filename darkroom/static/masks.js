const $=id=>document.getElementById(id);
const clone=v=>structuredClone(v);
const localDefaults={exposure:0,contrast:0,highlights:0,shadows:0,temperature:0,tint:0,saturation:0};

export class MaskEditor {
  constructor({onChange,onView,onError,onPick}) {
    this.onChange=onChange;this.onView=onView;this.onError=onError;this.onPick=onPick;this.picking=false;
    this.items=[];this.selected=-1;this.bitmap=null;this.drawing=null;
    $("mask-panel").innerHTML=`<fieldset id="mask-tools" disabled><legend>부분 보정 · 마스크</legend>
      <div class="mask-row"><select id="mask-kind" aria-label="새 마스크 종류"><option value="brush">브러시</option><option value="gradient">선형 그라디언트</option><option value="color">색상 범위</option><option value="luminance">휘도 범위</option></select><button id="mask-add">추가</button></div>
      <div class="mask-row"><select id="mask-select" aria-label="마스크 선택"></select><button id="mask-delete">삭제</button></div>
      <label class="check-label"><input id="mask-show" type="checkbox" checked> 선택 영역 표시</label>
      <fieldset id="mask-properties" disabled>
        <label>이름 <input id="mask-name" maxlength="60"></label>
        <div class="mask-row"><label class="check-label"><input id="mask-enabled" type="checkbox"> 사용</label><label class="check-label"><input id="mask-invert" type="checkbox"> 반전</label></div>
        <label>마스크 불투명도 <input id="mask-opacity" type="range" min="0" max="1" step=".01"></label>
        <label id="mask-draw-label" class="check-label"><input id="mask-draw" type="checkbox"> 사진에서 그리기 (끄면 이동)</label>
        <div id="brush-properties"><label>브러시 크기 <input id="brush-radius" type="range" min=".002" max=".25" step=".002" value=".04"></label><label>경계 부드러움 <input id="brush-feather" type="range" min="0" max="1" step=".01" value=".6"></label><label class="check-label"><input id="brush-erase" type="checkbox"> 지우개</label></div>
        <div id="color-properties"><label>선택할 색 <input id="mask-color" type="color" value="#ff0000"></label><button id="mask-pick">사진에서 색 찍기</button><label>색 허용 범위 <input id="mask-tolerance" type="range" min=".001" max="1" step=".001"></label></div>
        <div id="luminance-properties"><label>휘도 하한 <input id="mask-low" type="range" min="0" max="1" step=".01"></label><label>휘도 상한 <input id="mask-high" type="range" min="0" max="1" step=".01"></label></div>
        <label id="range-feather-label">범위 경계 부드러움 <input id="mask-feather" type="range" min="0" max="1" step=".01"></label>
        <div id="local-adjustments"><h3>이 마스크의 보정</h3></div>
      </fieldset><small id="mask-help">마스크를 추가해 사진 일부만 보정하세요. 최대 8개.</small></fieldset>`;
    for (const [key,label] of Object.entries({exposure:"노출",contrast:"대비",highlights:"하이라이트",shadows:"그림자",temperature:"색온도",tint:"틴트",saturation:"채도"})) {
      const limit=key==="exposure" ? 3 : 100,step=key==="exposure" ? .05 : 1;
      const row=document.createElement("label");row.textContent=label;
      const out=document.createElement("output");out.id=`local-${key}-value`;row.append(out);
      const input=document.createElement("input");Object.assign(input,{id:`local-${key}`,type:"range",min:-limit,max:limit,step,value:0});row.append(input);$("local-adjustments").append(row);
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
    this.refresh();
  }
  current() {return this.items[this.selected];}
  get masks() {return clone(this.items);}
  set masks(value) {this.items=clone(value);this.selected=Math.min(this.selected,this.items.length-1);if (this.selected<0 && this.items.length) this.selected=0;this.drawing=null;this.refresh();}
  add(kind) {
    if (this.items.length>=8) return this.onError("마스크는 최대 8개입니다.");
    const names={brush:"브러시",gradient:"그라디언트",color:"색상",luminance:"휘도"};
    this.items.push({name:`${names[kind]} ${this.items.length+1}`,kind,enabled:true,invert:false,opacity:1,start:{x:.2,y:.5},end:{x:.8,y:.5},color:[1,0,0],tolerance:.25,low:.2,high:.8,feather:.15,strokes:[],adjustments:{...localDefaults}});
    this.selected=this.items.length-1;this.refresh();$("mask-draw").checked=["brush","gradient"].includes(kind);this.onChange(true);
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
    $("mask-draw-label").hidden=!["brush","gradient"].includes(m.kind);
    if (!["brush","gradient"].includes(m.kind)) $("mask-draw").checked=false;
    $("mask-help").textContent=m.kind==="gradient" ? "사진에서 시작점→끝점을 드래그하세요. 시작 0%, 끝 100%." : "선택 영역에만 보정됩니다. 반전·불투명도를 조절할 수 있습니다.";
  }
  get active() {return Boolean(this.current() && (this.picking || ($("mask-draw").checked && ["brush","gradient"].includes(this.current().kind))));}
  down(point) {
    if (!this.active || !point) return false;
    const m=this.current();
    if (this.picking) {
      this.picking=false;$("mask-pick").textContent="사진에서 색 찍기";
      this.onPick(point).then(color=>{if (this.current()!==m) return;m.color=color;this.refresh();this.onChange(true);}).catch(e=>this.onError(e.message));
      return true;
    }
    if (m.kind==="brush" && (m.strokes.length>=64 || m.strokes.reduce((n,s)=>n+s.points.length,0)>=2048)) {this.onError("브러시 한도에 도달했습니다. 새 마스크를 추가하세요.");return true;}
    this.drawing=m.kind==="brush" ? {points:[point],radius:Number($("brush-radius").value),feather:Number($("brush-feather").value),erase:$("brush-erase").checked} : {start:point,end:point};
    this.onView();return true;
  }
  move(point) {
    if (!this.drawing || !point) return;
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
        else m.strokes.push(this.drawing);
      } else {m.start=this.drawing.start;m.end=this.drawing.end;}
    }
    this.drawing=null;if (!cancel) this.onChange(true);else this.onView();return true;
  }
  setOverlay(image) {this.bitmap?.close();this.bitmap=image;}
  draw(ctx,v,photo,width,height) {
    ctx.clearRect(0,0,width,height);
    if (!$("mask-show").checked || !this.current()) return;
    const x=v.offset[0]-v.origin[0]*v.scale,y=v.offset[1]-v.origin[1]*v.scale;
    if (this.bitmap) ctx.drawImage(this.bitmap,x,y,photo.width*v.scale,photo.height*v.scale);
    const pos=p=>[x+p.x*photo.width*v.scale,y+p.y*photo.height*v.scale];
    if (this.current().kind==="gradient" && this.active) {
      const m=this.drawing || this.current();const a=pos(m.start),b=pos(m.end);
      ctx.strokeStyle="white";ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(...a);ctx.lineTo(...b);ctx.stroke();
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
