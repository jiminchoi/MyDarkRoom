// Cache source-space mask weights and fine-edge fields. Strength changes are uniforms.
export const spatialKeys=["dehaze","clarity","texture","sharpness"];
export const hasLocalSpatial=masks=>masks.some(m=>m.enabled&&m.opacity&&spatialKeys.some(k=>m.adjustments[k]));
const shapeKey=masks=>JSON.stringify(masks.map(({adjustments,name,enabled,opacity,...shape})=>shape));

export class LocalSpatial {
  constructor({post,linear,onReady,onError}){Object.assign(this,{post,linear,onReady,onError});this.reset();}
  reset(){
    this.abort?.abort();this.detailAbort?.abort();clearTimeout(this.timer);clearTimeout(this.detailTimer);
    this.key=this.pending=this.detailKey=this.detailPending="";this.photo=null;
  }
  sync(photo,gpu,masks,detail=null){
    if(!photo||!gpu)return;
    this.photo=photo.id;
    const active=hasLocalSpatial(masks),shape=shapeKey(masks),key=photo.id+shape;
    this.currentKey=key;this.active=active;
    if(this.pending&&this.pending!==key){this.abort?.abort();clearTimeout(this.timer);this.pending="";}
    gpu.localAmounts.fill(0);
    masks.forEach((m,i)=>spatialKeys.forEach((k,j)=>gpu.localAmounts[i*4+j]=m.enabled?(m.adjustments[k]||0)*m.opacity:0));
    gpu.localEnabled=active&&key===this.key;
    const needsSharp=masks.some(m=>m.enabled&&m.opacity&&m.adjustments.sharpness);
    // A strength change while preparation is running reuses the same request.
    if(active&&(key!==this.key||(needsSharp&&!gpu.sharpTexture))&&this.pending!==key){
      this.abort?.abort();clearTimeout(this.timer);this.pending=key;const controller=new AbortController();this.abort=controller;
      this.timer=setTimeout(async()=>{
        try{
          if(this.key!==key){
            const data=await this.weights(await this.post(photo.id,"mask-fields",{masks},controller.signal));
            if(controller.signal.aborted||this.photo!==photo.id||this.currentKey!==key)return;
            gpu.loadLocal(data);this.key=key;
          }
          // Recheck live strengths: a user may enable sharpness during weight preparation.
          if(gpu.localAmounts.some((v,i)=>i%4===3&&v!==0)&&!gpu.sharpTexture){
            const data=await this.linear(await fetch(`/api/photos/${photo.id}/structure?kind=sharpness`,{signal:controller.signal}),1);
            if(controller.signal.aborted||this.photo!==photo.id||this.currentKey!==key)return;gpu.loadSharpness(data);
          }
          gpu.localEnabled=this.active&&key===this.key;this.onReady();
        }catch(e){if(e.name!=="AbortError")this.onError("마스크 질감 준비 실패: "+e.message);}
        finally{if(this.abort===controller)this.pending="";}
      },120);
    }
    // Drop old geometry maps immediately; they must not follow a different set of masks.
    if(!masks.length){gpu.clearLocal();gpu.clearLocal(true);this.key=this.detailKey="";}
    const dkey=detail?key+":"+detail.region:"";
    this.currentDetail=dkey;
    if(this.detailPending&&this.detailPending!==dkey){this.detailAbort?.abort();clearTimeout(this.detailTimer);this.detailPending="";}
    gpu.localDetailEnabled=active&&dkey===this.detailKey;
    if(!active||!detail||this.detailPending===dkey)return;
    if(dkey===this.detailKey&&gpu.localDetailTextures&&(!needsSharp||gpu.sharpDetailTexture||detail.sampled))return;
    this.detailAbort?.abort();clearTimeout(this.detailTimer);this.detailPending=dkey;const controller=new AbortController();this.detailAbort=controller;
    this.detailTimer=setTimeout(async()=>{
      const [x,y,width,height]=detail.region.split(",");const query=`x=${x}&y=${y}&width=${width}&height=${height}`;
      try{
        const data=await this.weights(await this.post(photo.id,`mask-fields?${query}`,{masks},controller.signal));
        if(controller.signal.aborted||this.photo!==photo.id||this.currentDetail!==dkey)return;gpu.loadLocal(data,true);this.detailKey=dkey;
        if(gpu.localAmounts.some((v,i)=>i%4===3&&v!==0)&&!detail.sampled){
          const sharp=await this.linear(await fetch(`/api/photos/${photo.id}/structure?kind=sharpness&${query}`,{signal:controller.signal}),1);
          if(controller.signal.aborted||this.photo!==photo.id||this.currentDetail!==dkey)return;gpu.loadSharpness(sharp,true);
        }
        gpu.localDetailEnabled=true;this.onReady();
      }catch(e){if(e.name!=="AbortError")this.onError("확대 마스크 질감 준비 실패: "+e.message);}
      finally{if(this.detailAbort===controller)this.detailPending="";}
    },160);
  }
  async weights(response){
    const width=Number(response.headers.get("X-Image-Width")),height=Number(response.headers.get("X-Image-Height")),planes=Number(response.headers.get("X-Mask-Planes"));
    const pixels=new Uint8Array(await response.arrayBuffer());
    if(!width||!height||planes<1||planes>2||pixels.length!==width*height*4*planes)throw new Error("마스크 데이터가 올바르지 않습니다.");
    return {width,height,planes,pixels};
  }
}
