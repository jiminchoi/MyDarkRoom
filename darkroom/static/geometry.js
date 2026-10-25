const $=id=>document.getElementById(id);
export const geometryDefaults=()=>({rotation:0,vertical:0,horizontal:0,upright:[1,0,0,0,1,0,0,0,1],crop:[0,0,1,1]});

export class GeometryEditor {
  constructor({getPhoto,onChange,onView,onError,onMessage}) {
    Object.assign(this,{getPhoto,onChange,onView,onError,onMessage});this.data=geometryDefaults();this.editing=false;this.paused=false;this.bitmap=null;this.before=null;this.token=0;this.drag=null;
    $("geometry-panel").innerHTML=`<fieldset id="geometry-tools" disabled><legend>크롭 · 수평 · 업라이트</legend>
      <div class="geometry-row"><button id="auto-level">자동 수평</button><button id="auto-vertical">자동 수직</button><button id="auto-upright">자동 업라이트</button></div>
      <label>회전 <output id="rotation-value">0</output><input id="geo-rotation" type="range" min="-20" max="20" step=".01" value="0"></label>
      <label>수직 원근 <output id="vertical-value">0</output><input id="geo-vertical" type="range" min="-.6" max=".6" step=".001" value="0"></label>
      <label>수평 원근 <output id="horizontal-value">0</output><input id="geo-horizontal" type="range" min="-.6" max=".6" step=".001" value="0"></label>
      <label>크롭 비율 <select id="crop-ratio"><option value="free">자유</option><option value="original">원본 비율</option><option value="1">1:1</option><option value="1.5">3:2</option><option value="1.3333333333">4:3</option><option value="1.7777777778">16:9</option><option value=".6666666667">2:3</option><option value=".75">3:4</option><option value=".5625">9:16</option></select></label>
      <div class="geometry-row"><button id="crop-edit">크롭 편집</button><button id="crop-full">크롭 해제</button><button id="geometry-reset">기하 초기화</button></div>
      <label class="check-label"><input id="geometry-source" type="checkbox"> 원본 좌표 보기 · 마스크 편집</label>
      <small id="geometry-message">크롭 편집 후 사진을 드래그해 영역을 선택하세요.</small></fieldset>`;
    for (const key of ["rotation","vertical","horizontal"]) {
      const input=$("geo-"+key);input.oninput=()=>{this.data[key]=Number(input.value);$(key+"-value").textContent=input.value;this.paused=false;$("geometry-source").checked=false;this.onChange(false);};input.onchange=()=>this.onChange(true);
    }
    $("crop-edit").onclick=()=>{this.editing=!this.editing;this.paused=false;this.sync();this.onChange(false);};
    $("crop-full").onclick=()=>{this.data.crop=[0,0,1,1];this.onChange(true);};
    $("geometry-reset").onclick=()=>{this.clear();this.onChange(true);};
    $("geometry-source").onchange=()=>{this.paused=$("geometry-source").checked;this.onChange(false);};
    $("auto-level").onclick=()=>this.auto("level");$("auto-upright").onclick=()=>this.auto("upright");$("auto-vertical").onclick=()=>this.auto("vertical");
    $("crop-ratio").onchange=()=>{
      const photo=this.getPhoto(),ratio=$("crop-ratio").value;if(!photo||ratio==="free")return;
      const r=ratio==="original"?1:Number(ratio)/(photo.width/photo.height),[x,y,w,h]=this.data.crop;
      const width=Math.min(w,h*r),height=width/r;this.data.crop=[x+(w-width)/2,y+(h-height)/2,width,height];this.editing=true;this.paused=false;this.sync();this.onChange(true);
    };
  }
  get value() {return structuredClone(this.data);}
  set(value) {this.data={...geometryDefaults(),...structuredClone(value)};this.sync();}
  sync() {for(const key of ["rotation","vertical","horizontal"]) {$("geo-"+key).value=this.data[key];$(key+"-value").textContent=this.data[key];}$("geometry-source").checked=this.paused;$("crop-edit").textContent=this.editing?"크롭 적용":"크롭 편집";}
  get active() {return !this.paused&&(this.editing||JSON.stringify(this.data)!==JSON.stringify(geometryDefaults()));}
  async auto(mode) {
    const photo=this.getPhoto();if (!photo)return;
    $("auto-level").disabled=$("auto-vertical").disabled=$("auto-upright").disabled=true;const previous=JSON.stringify(this.data);
    try {
      this.onMessage("직선을 분석하고 있어요…");
      const response=await fetch(`/api/photos/${photo.id}/auto-geometry?mode=${mode}`,{method:"POST"});const result=await response.json();
      if (!response.ok) throw new Error(result.detail||"자동 분석 실패");if (this.getPhoto()?.id!==photo.id||JSON.stringify(this.data)!==previous)return;
      this.data=result.geometry;$("crop-ratio").value="original";
      this.editing=false;this.paused=false;this.sync();$("geometry-message").textContent=result.message;this.onChange(true);
    } catch(e) {this.onError(e.message);}finally {$("auto-level").disabled=$("auto-vertical").disabled=$("auto-upright").disabled=false;}
  }
  refresh(values,defaults) {
    clearTimeout(this.timer);const token=++this.token;this.abort?.abort();this.pending=this.active;
    if (!this.active)return this.onView();
    this.onMessage("기하 보정 미리보기 갱신 중…");
    this.timer=setTimeout(async()=>{
      const photo=this.getPhoto();if (!photo)return;
      const geometry=this.value;if(this.editing)geometry.crop=[0,0,1,1];
      const controller=new AbortController();this.abort=controller;let edited,original;
      try {
        const request=async settings=>{const response=await fetch(`/api/photos/${photo.id}/preview`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...settings,geometry}),signal:controller.signal});if(!response.ok){const error=await response.json();throw new Error(error.detail||"기하 미리보기 실패");}return createImageBitmap(await response.blob());};
        edited=await request(values);original=await request(defaults);
        if(token!==this.token||this.getPhoto()?.id!==photo.id){edited.close();original.close();return;}
        this.bitmap?.close();this.before?.close();this.bitmap=edited;this.before=original;this.pending=false;this.onView();this.onMessage("기하 보정 미리보기 완료");
      }catch(e){edited?.close();original?.close();if(e.name!=="AbortError")this.onError(e.message);}
    },160);
  }
  displayRect(w,h) {
    const photo=this.getPhoto(),crop=this.editing?[0,0,1,1]:this.data.crop;
    const aspect=photo.width*crop[2]/(photo.height*crop[3]);let width=w,height=w/aspect;if(height>h){height=h;width=h*aspect;}
    return [(w-width)/2,(h-height)/2,width,height];
  }
  draw(ctx,w,h,{before,split,fraction}) {
    ctx.fillStyle="#101212";ctx.fillRect(0,0,w,h);const rect=this.displayRect(w,h);
    if(this.bitmap)ctx.drawImage(before?this.before:this.bitmap,...rect);
    if(this.bitmap&&split&&!before){ctx.save();ctx.beginPath();ctx.rect(0,0,w*fraction,h);ctx.clip();ctx.drawImage(this.before,...rect);ctx.restore();}
    if(this.editing){
      const [x,y,cw,ch]=this.drag?.rect||this.data.crop;const [left,top,width,height]=rect;
      ctx.strokeStyle="#e8ad74";ctx.lineWidth=2;ctx.strokeRect(left+x*width,top+y*height,cw*width,ch*height);
      const handleSize=8*($("geometry-view").width/$("geometry-view").getBoundingClientRect().width);
      ctx.fillStyle="#e8ad74";
      for(const [hx,hy] of this.handles([x,y,cw,ch]))ctx.fillRect(left+hx*width-handleSize/2,top+hy*height-handleSize/2,handleSize,handleSize);
      ctx.strokeStyle="#ffffff66";ctx.lineWidth=1;for(let i=1;i<3;i++){ctx.beginPath();ctx.moveTo(left+(x+cw*i/3)*width,top+y*height);ctx.lineTo(left+(x+cw*i/3)*width,top+(y+ch)*height);ctx.moveTo(left+x*width,top+(y+ch*i/3)*height);ctx.lineTo(left+(x+cw)*width,top+(y+ch*i/3)*height);ctx.stroke();}
    }
  }
  point(event) {const layer=$("geometry-view"),box=layer.getBoundingClientRect();const [x,y,w,h]=this.displayRect(layer.width,layer.height);return [Math.max(0,Math.min(1,((event.clientX-box.left)*layer.width/box.width-x)/w)),Math.max(0,Math.min(1,((event.clientY-box.top)*layer.height/box.height-y)/h))];}
  handles([x,y,w,h]) {return [[x,y],[x+w/2,y],[x+w,y],[x+w,y+h/2],[x+w,y+h],[x+w/2,y+h],[x,y+h],[x,y+h/2]];}
  hit(point) {
    const layer=$("geometry-view"),box=layer.getBoundingClientRect(),[,,w,h]=this.displayRect(layer.width,layer.height),scale=box.width/layer.width;
    return this.handles(this.data.crop).findIndex(p=>Math.hypot((p[0]-point[0])*w*scale,(p[1]-point[1])*h*scale)<12);
  }
  hover(event) {
    if(!this.active||!this.editing){$("canvas").style.cursor="";return;}
    const point=this.point(event),i=this.hit(point),[x,y,w,h]=this.data.crop;
    $("canvas").style.cursor=i>=0?["nwse-resize","ns-resize","nesw-resize","ew-resize","nwse-resize","ns-resize","nesw-resize","ew-resize"][i]:point[0]>x&&point[0]<x+w&&point[1]>y&&point[1]<y+h?"move":"crosshair";
  }
  down(event) {
    if(!this.active||!this.editing)return false;
    const point=this.point(event),[x,y,w,h]=this.data.crop;
    this.drag={handle:this.hit(point),start:point,old:[x,y,w,h],moving:point[0]>x&&point[0]<x+w&&point[1]>y&&point[1]<y+h&&(w<.99||h<.99),rect:[x,y,w,h]};return true;
  }
  move(event) {
    if(!this.drag)return;const point=this.point(event),d=this.drag;
    if(d.handle>=0){
      const [x,y,w,h]=d.old,i=d.handle,left=[0,6,7].includes(i),right=[2,3,4].includes(i),top=[0,1,2].includes(i),bottom=[4,5,6].includes(i);
      let x1=x,x2=x+w,y1=y,y2=y+h;
      if(left)x1=Math.min(x2-.01,point[0]);if(right)x2=Math.max(x1+.01,point[0]);if(top)y1=Math.min(y2-.01,point[1]);if(bottom)y2=Math.max(y1+.01,point[1]);
      const ratio=$("crop-ratio").value;
      if(ratio==="free")d.rect=[x1,y1,x2-x1,y2-y1];
      else {
        const photo=this.getPhoto(),r=ratio==="original"?1:Number(ratio)/(photo.width/photo.height),ax=left?x+w:right?x:x+w/2,ay=top?y+h:bottom?y:y+h/2;
        let width=x2-x1,height=y2-y1;
        if(!left&&!right)width=height*r;else if(!top&&!bottom)height=width/r;else{width=Math.max(width,height*r);height=width/r;}
        const maxW=left?ax:right?1-ax:2*Math.min(ax,1-ax),maxH=top?ay:bottom?1-ay:2*Math.min(ay,1-ay),factor=Math.min(1,maxW/width,maxH/height);
        width*=factor;height*=factor;
        d.rect=[left?ax-width:right?ax:ax-width/2,top?ay-height:bottom?ay:ay-height/2,width,height];
      }
    }
    else if(d.moving){const [x,y,w,h]=d.old;d.rect=[Math.max(0,Math.min(1-w,x+point[0]-d.start[0])),Math.max(0,Math.min(1-h,y+point[1]-d.start[1])),w,h];}
    else {
      let w=Math.abs(point[0]-d.start[0]),h=Math.abs(point[1]-d.start[1]);const ratio=$("crop-ratio").value,photo=this.getPhoto();
      if(ratio!=="free"){const r=ratio==="original"?1:Number(ratio)/(photo.width/photo.height);h=w/r;const limitY=point[1]>=d.start[1]?1-d.start[1]:d.start[1];if(h>limitY){h=limitY;w=h*r;}}
      d.rect=[point[0]>=d.start[0]?d.start[0]:d.start[0]-w,point[1]>=d.start[1]?d.start[1]:d.start[1]-h,w,h];
    }this.onView();
  }
  up(cancel=false) {if(!this.drag)return;const rect=this.drag.rect;this.drag=null;if(!cancel&&rect[2]>=.01&&rect[3]>=.01){this.data.crop=rect;this.onChange(true);}else this.onView();}
  clear() {this.token++;clearTimeout(this.timer);this.abort?.abort();this.bitmap?.close();this.before?.close();this.bitmap=this.before=null;this.data=geometryDefaults();this.editing=this.paused=false;this.sync();}
}
