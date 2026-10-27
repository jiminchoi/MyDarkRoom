"""Native save dialogs; image bytes never travel over the JavaScript bridge."""
from pathlib import Path
import logging
import re
import secrets
import tempfile
import threading
from .version import editor_url


def export_options(options):
    name=options.get('filename','')
    if not isinstance(name,str) or not name or len(name)>180 or re.search(r'[<>:"/\\|?*\x00-\x1f]',name) or name[-1] in '. ' or re.match(r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)',name,re.I):
        raise ValueError('Windows에서 사용할 수 있는 파일 이름을 입력하세요.')
    if not name.lower().endswith(('.jpg','.jpeg')):name+='.jpg'
    values={}
    for key,default,low,high in [('longEdge',0,0,20000),('dpi',300,1,2400),('quality',95,1,100)]:
        value=options.get(key,default)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or int(value)!=value or not low<=value<=high:raise ValueError('잘못된 JPEG 출력 설정입니다.')
        values[key]=int(value)
    return name,values


def save_unique(directory,name,data):
    stem=Path(name).stem;suffix=Path(name).suffix
    for i in range(1000):
        target=directory/(name if not i else f'{stem} ({i+1}){suffix}')
        try:file=target.open('xb')
        except FileExistsError:continue
        try:
            with file:file.write(data)
            return target
        except BaseException:target.unlink(missing_ok=True);raise
    raise ValueError('같은 이름의 파일이 너무 많습니다.')


class NativeApi:
    def __init__(self,runtime):
        self._runtime=runtime;self._window=None;self._folders={};self._lock=runtime.native_lock

    def _trusted(self):
        url=self._window.get_current_url() if self._window else None
        if url not in (self._runtime.url,self._runtime.url+'/',editor_url(self._runtime.url)) or self._runtime.closing:raise PermissionError('편집 화면에서만 저장할 수 있습니다.')

    def _dialog(self,*args,**kwargs):
        logging.getLogger('mydarkroom').info('Opening native file dialog')
        # pywebview API calls run on worker threads; Windows OLE dialogs need STA.
        native=getattr(self._window,'native',None)
        if native is not None and native.InvokeRequired:
            from System import Action
            result=[]
            native.Invoke(Action(lambda:result.append(self._window.create_file_dialog(*args,**kwargs))))
            return result[0]
        return self._window.create_file_dialog(*args,**kwargs)

    def choose_output(self):
        self._trusted()
        import webview
        selected=self._dialog(webview.FileDialog.FOLDER)
        if not selected:return None
        path=Path(selected[0]).resolve()
        if not path.is_dir():raise ValueError('폴더를 선택해 주세요.')
        token=secrets.token_urlsafe(24);self._folders={token:path}
        return {'native_token':token,'name':path.name}

    def export_jpeg(self,photo_id,settings,options):
        logging.getLogger('mydarkroom').info('Native JPEG export requested')
        self._trusted()
        from .imaging import Adjustments
        name,values=export_options(options);adjustments=Adjustments.model_validate(settings)
        token=options.get('directory_token');directory=self._folders.get(token) if token else None
        if token and directory is None:raise ValueError('저장 폴더를 다시 선택해 주세요.')
        with self._lock:
            self._trusted()
            target=None
            if directory is None:
                import webview
                selected=self._dialog(webview.FileDialog.SAVE,save_filename=name,file_types=('JPEG image (*.jpg;*.jpeg)',))
                logging.getLogger('mydarkroom').info('Native file dialog finished (selected=%s)',bool(selected))
                if not selected:return {'cancelled':True}
                target=Path(selected[0]).resolve()
                if target.suffix.lower() not in ('.jpg','.jpeg'):target=target.with_name(target.name+'.jpg')
            if self._runtime.closing:return {'cancelled':True}
            logging.getLogger('mydarkroom').info('Rendering native JPEG')
            data=self._runtime.app.state.studio.image(photo_id,adjustments,full=True,long_edge=values['longEdge'],dpi=values['dpi'],quality=values['quality'])
            if directory is not None:target=save_unique(directory,name,data)
            else:
                pending=None
                try:
                    with tempfile.NamedTemporaryFile(dir=target.parent,prefix='.mydarkroom-',suffix='.tmp',delete=False) as file:
                        pending=Path(file.name);file.write(data)
                    pending.replace(target)
                finally:
                    if pending:pending.unlink(missing_ok=True)
            logging.getLogger('mydarkroom').info('Native JPEG saved')
            return {'cancelled':False,'filename':target.name,'folder':target.parent.name}
