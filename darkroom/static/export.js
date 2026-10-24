const $=id=>document.getElementById(id);

export class ExportDialog {
  constructor(getPhoto,onSave) {
    this.getPhoto=getPhoto;this.onSave=onSave;this.directory=null;this.size="original";
    const note=document.createElement("p");note.id="export-error";note.setAttribute("role","alert");$("export-dialog").append(note);
    for (const size of ["original","small","custom"]) $("size-"+size).onclick=()=>{this.size=size;this.update();};
    $("export-edge").oninput=()=>this.update();
    if (!window.showDirectoryPicker) {
      $("choose-output").disabled=true;
      $("folder-support").textContent="폴더 직접 선택은 Windows Chrome/Edge에서 지원합니다. 이 브라우저에서는 다운로드로 저장합니다.";
    } else $("folder-support").textContent="폴더를 선택하면 해당 위치에 직접 저장합니다. 같은 이름은 번호를 붙여 보존합니다.";
    $("choose-output").onclick=async()=>{
      try {this.directory=await window.showDirectoryPicker({id:"mydarkroom-exports",mode:"readwrite"});$("output-folder").textContent=this.directory.name;note.textContent="";}
      catch(e) {if (e.name!=="AbortError") note.textContent="폴더를 선택하지 못했습니다: "+e.message;}
    };
    $("export-start").onclick=()=>{
      note.textContent="";
      const filename=$("export-name").value.trim();
      if (!filename || filename.length>180 || /[<>:"/\\|?*\x00-\x1f]/.test(filename) || /[. ]$/.test(filename) || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(filename)) {note.textContent="Windows에서 사용할 수 있는 파일 이름을 입력하세요. 경로는 폴더 선택으로 지정합니다.";return;}
      const dpi=Number($("export-dpi").value),quality=Number($("export-quality").value),edge=Number($("export-edge").value);
      if (!Number.isInteger(dpi)||dpi<1||dpi>2400||!Number.isInteger(quality)||quality<1||quality>100 || (this.size==="custom"&&(!Number.isInteger(edge)||edge<1||edge>20000))) {note.textContent="DPI(1–2400), 품질(1–100), 장축(1–20000)을 확인하세요.";return;}
      const options={filename:/\.jpe?g$/i.test(filename)?filename:filename+".jpg",directory:this.directory,longEdge:this.size==="original"?0:this.size==="small"?2000:edge,dpi,quality};
      $("export-dialog").close();this.onSave(options);
    };
  }
  update() {
    for (const size of ["original","small","custom"]) $("size-"+size).setAttribute("aria-selected",size===this.size);
    $("custom-edge-label").hidden=this.size!=="custom";
    const photo=this.getPhoto();if (!photo) return;
    const edge=this.size==="original"?Math.max(photo.width,photo.height):this.size==="small"?2000:Number($("export-edge").value)||1;
    const factor=Math.min(1,edge/Math.max(photo.width,photo.height));
    $("export-size-info").textContent=`출력 ${Math.max(1,Math.round(photo.width*factor))} × ${Math.max(1,Math.round(photo.height*factor))} 픽셀 · sRGB`;
  }
  show() {
    const photo=this.getPhoto();if (!photo) return;
    $("export-name").value=photo.filename.replace(/\.nef$/i,"")+"-edited.jpg";$("export-error").textContent="";this.update();$("export-dialog").showModal();
  }
}

export async function saveToDirectory(directory,filename,blob) {
  let candidate=filename;
  for (let i=0;i<1000;i++) {
    candidate=i ? filename.replace(/(\.jpe?g)$/i,` (${i+1})$1`) : filename;
    try {await directory.getFileHandle(candidate);}
    catch(e) {
      if (e.name!=="NotFoundError") throw e;
      const handle=await directory.getFileHandle(candidate,{create:true});
      const writer=await handle.createWritable();
      try {await writer.write(blob);await writer.close();}
      catch(error) {try{await writer.abort();}catch{} throw error;}
      return candidate;
    }
  }
  throw new Error("같은 이름의 파일이 너무 많습니다. 다른 파일 이름을 사용하세요.");
}
