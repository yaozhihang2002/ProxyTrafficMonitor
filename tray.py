"""Tray callbacks cross into Tk only through a main-thread queue."""
import queue,threading
import pystray
from PIL import Image,ImageDraw

class Tray:
    def __init__(self,window,open_web,open_data,exit_app):
        self.window=window;self.actions=queue.Queue();self.ready=threading.Event();self.closed=False
        self.handlers={'restore':self.restore,'web':open_web,'data':open_data,'exit':exit_app}
        image=Image.new('RGBA',(64,64),(0,0,0,0));draw=ImageDraw.Draw(image)
        draw.rounded_rectangle((2,2,61,61),radius=14,fill='#176b60')
        draw.line([(12,39),(22,39),(28,20),(36,47),(43,30),(53,30)],fill='white',width=5)
        def action(name):return lambda icon,item:self.actions.put(name)
        self.icon=pystray.Icon('ProxyTrafficMonitor',image,'代理流量观察台 · 后台记录中',pystray.Menu(
            pystray.MenuItem('显示监测窗口',action('restore'),default=True),
            pystray.MenuItem('打开监测网页',action('web')),
            pystray.MenuItem('打开数据文件夹',action('data')),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('退出监测',action('exit'))))
        def setup(icon):icon.visible=True;self.ready.set()
        self.thread=threading.Thread(target=lambda:self.icon.run(setup),daemon=True)
        self.thread.start();self.timer=window.after(100,self.poll)
        window.bind('<Unmap>',self.on_minimize,add='+')
    def poll(self):
        if self.closed:return
        while not self.actions.empty():
            self.handlers[self.actions.get_nowait()]()
            if self.closed:return
        self.timer=self.window.after(100,self.poll)
    def hide(self):
        # Keep an accessible taskbar window if Explorer cannot create the icon.
        if self.ready.is_set() and self.icon.visible:self.window.withdraw()
        else:self.window.iconify()
    def on_minimize(self,event):
        if event.widget==self.window and self.window.state()=='iconic' and self.ready.is_set():self.window.after_idle(self.hide)
    def restore(self):
        self.window.deiconify();self.window.lift();self.window.focus_force()
    def stop(self):
        if self.closed:return
        self.closed=True
        self.window.after_cancel(self.timer)
        self.icon.stop();self.thread.join(timeout=3)
