"""Remember the user's window-close choice independently of collector settings."""
import json
import tkinter as tk
from pathlib import Path

def load_action(path):
    try:
        action=json.loads(Path(path).read_text(encoding='utf-8')).get('close_action')
        return action if action in ('hide','exit') else 'ask'
    except (OSError,ValueError,TypeError,AttributeError):
        return 'ask'

def save_action(path,action):
    if action not in ('ask','hide','exit'):raise ValueError('Invalid close action')
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps({'close_action':action}),encoding='utf-8')
    temp.replace(path)

class CloseDialog:
    def __init__(self,parent,path,hide,exit_app):
        self.path=path;self.hide=hide;self.exit_app=exit_app
        self.window=tk.Toplevel(parent);self.window.title('关闭窗口')
        self.window.resizable(False,False);self.window.transient(parent)
        self.choice=tk.StringVar(self.window,value='hide')
        self.remember=tk.BooleanVar(self.window,value=False)
        box=tk.Frame(self.window,padx=20,pady=16);box.pack()
        tk.Label(box,text='请选择关闭方式',font=('Microsoft YaHei UI',12,'bold')).pack(anchor='w',pady=(0,8))
        tk.Radiobutton(box,text='隐藏到托盘：后台继续记录',variable=self.choice,value='hide').pack(anchor='w',pady=4)
        tk.Radiobutton(box,text='彻底退出：停止后台记录',variable=self.choice,value='exit').pack(anchor='w',pady=4)
        tk.Checkbutton(box,text='不再提醒，记住本次选择',variable=self.remember).pack(anchor='w',pady=(10,4))
        tk.Label(box,text='可在主窗口的“关闭行为设置”恢复提醒。',fg='#65766d').pack(anchor='w')
        buttons=tk.Frame(box);buttons.pack(anchor='e',pady=(14,0))
        tk.Button(buttons,text='取消',command=self.cancel,width=9).pack(side='left',padx=5)
        self.confirm=tk.Button(buttons,text='确定',command=self.accept,width=9);self.confirm.pack(side='left')
        self.window.protocol('WM_DELETE_WINDOW',self.cancel)
        self.window.bind('<Escape>',lambda event:self.cancel())
        self.window.update_idletasks()
        x=parent.winfo_rootx()+(parent.winfo_width()-self.window.winfo_width())//2
        y=parent.winfo_rooty()+(parent.winfo_height()-self.window.winfo_height())//2
        self.window.geometry(f'+{max(0,x)}+{max(0,y)}')
        self.window.grab_set();self.confirm.focus_set()
    def cancel(self):
        self.window.grab_release();self.window.destroy()
    def accept(self):
        action=self.choice.get()
        if self.remember.get():save_action(self.path,action)
        self.cancel()
        (self.hide if action=='hide' else self.exit_app)()
