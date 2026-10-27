// No snapshot cloning/stringifying or HTTP requests on the slider/pointer hot path.
export class EditSaver {
  constructor({getSettings,onState,onError,delay=1500}) {
    Object.assign(this,{getSettings,onState,onError,delay});this.photo=null;this.dirty=false;this.generation=0;this.running=null;
  }
  attach(photo) {
    this.cancelTimer();this.photo=photo;this.revision=photo.edits.revision;this.writable=photo.edits.writable;
    this.generation=0;this.dirty=false;this.failed=false;this.lastSettings=JSON.stringify(this.getSettings());
    this.state(!this.writable?"error":photo.edits.settings?"saved":"ready");
  }
  state(value) {if(this.lastState!==value){this.lastState=value;this.onState(value);}}
  cancelTimer() {clearTimeout(this.timer);if(this.idle!=null)window.cancelIdleCallback?.(this.idle);this.idle=null;}
  mark() {
    if(!this.photo)return;
    this.generation++;this.dirty=true;this.failed=false;this.state(this.writable?"dirty":"error");this.schedule();
  }
  schedule() {
    this.cancelTimer();
    if(!this.writable)return;
    this.timer=setTimeout(()=>{
      const save=()=>{this.idle=null;this.save().catch(()=>{});};
      if(window.requestIdleCallback)this.idle=window.requestIdleCallback(save,{timeout:1500});else save();
    },this.delay);
  }
  async save() {
    if(this.running)return this.running;
    if(!this.dirty)return;
    if(!this.writable)throw new Error("자동 저장이 중지되어 있습니다. 보정정보를 파일로 내보내 주세요.");
    this.running=this.write().finally(()=>{this.running=null;});
    return this.running;
  }
  async write() {
    const generation=this.generation,values=JSON.stringify(this.getSettings());
    try {
      if(values!==this.lastSettings) {
        this.state("saving");
        const response=await fetch(`/api/photos/${this.photo.id}/edits`,{method:"PUT",headers:{"Content-Type":"application/json"},body:`{"revision":${this.revision},"settings":${values}}`});
        const data=await response.json();
        if(!response.ok){if(response.status===409)this.writable=false;throw new Error(data.detail||"보정정보를 저장하지 못했습니다.");}
        this.revision=data.revision;this.lastSettings=values;
      }
      this.dirty=this.generation!==generation;this.failed=false;
      this.state(this.dirty?"dirty":"saved");
      if(this.dirty)this.schedule();
    } catch(e) {this.failed=true;this.state("error");this.onError(e.message);throw e;}
  }
  async flush() {
    this.cancelTimer();
    if(this.running)await this.running;
    while(this.dirty){await this.save();this.cancelTimer();}
  }
}
