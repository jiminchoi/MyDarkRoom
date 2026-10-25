"""Small native control window; photo rendering stays in Edge/Chrome."""
import logging
import os
import tkinter as tk
from tkinter import ttk
from .desktop import open_editor
from .version import VERSION


class Launcher:
    def __init__(self,runtime,auto_open=True):
        self.runtime=runtime;self.auto_open=auto_open;self.opened=False
        self.root=tk.Tk();self.root.title("MyDarkRoom")
        self.root.geometry("460x230");self.root.resizable(False,False)
        self.root.configure(background="#151818")
        self.root.protocol("WM_DELETE_WINDOW",self.close)
        style=ttk.Style();style.theme_use("clam")
        style.configure("TFrame",background="#151818")
        style.configure("TLabel",background="#151818",foreground="#eeeeea",font=("Segoe UI",10))
        style.configure("Title.TLabel",font=("Segoe UI",18,"bold"))
        style.configure("TButton",font=("Segoe UI",10),padding=(12,8))
        frame=ttk.Frame(self.root,padding=24);frame.pack(fill="both",expand=True)
        ttk.Label(frame,text="MyDarkRoom",style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame,text=f"나만의 작은 암실 · {VERSION}").pack(anchor="w",pady=(2,15))
        self.message=tk.StringVar(value="사진 편집기를 준비하고 있어요…")
        ttk.Label(frame,textvariable=self.message,wraplength=410).pack(anchor="w")
        buttons=ttk.Frame(frame);buttons.pack(fill="x",pady=(20,0))
        self.open=ttk.Button(buttons,text="편집 화면 열기",command=self.open_editor,state="disabled");self.open.pack(side="left")
        ttk.Button(buttons,text="로그 폴더",command=self.logs).pack(side="left",padx=8)
        self.quit=ttk.Button(buttons,text="종료",command=self.close);self.quit.pack(side="right")
        self.root.after(100,self.tick)

    def open_editor(self):
        try:
            if not open_editor(self.runtime.url):self.message.set("브라우저를 열지 못했습니다: "+self.runtime.url)
        except OSError:
            logging.exception("Could not open the editor browser");self.message.set("브라우저에서 열어 주세요: "+self.runtime.url)

    def logs(self):
        if os.name=="nt":os.startfile(self.runtime.directory/"logs")

    def close(self):
        if self.runtime.closing:return
        self.runtime.stop();self.open.configure(state="disabled");self.quit.configure(state="disabled")
        self.message.set("편집을 마무리하고 임시 파일을 정리하고 있어요…")

    def tick(self):
        if self.runtime.finished.is_set():
            if self.runtime.closing:self.root.destroy();return
            self.open.configure(state="disabled")
            self.message.set("실행에 문제가 생겼습니다. 로그 폴더를 확인해 주세요.")
        elif self.runtime.ready and not self.runtime.closing:
            self.open.configure(state="normal")
            if not self.opened:
                self.opened=True;self.message.set("준비됐어요. 브라우저에서 사진을 편집하세요.")
                if self.auto_open:self.open_editor()
        while not self.runtime.commands.empty():
            command=self.runtime.commands.get()
            if command=="shutdown":self.close()
            elif command=="activate" and not self.runtime.closing:
                self.root.deiconify();self.root.lift();self.open_editor()
        self.root.after(100,self.tick)

    def run(self):self.root.mainloop()
