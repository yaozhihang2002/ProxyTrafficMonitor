"""Tk-free recovery UI when a machine cannot initialise Tcl/Tk."""
import ctypes,threading,webbrowser,os
import pystray
from PIL import Image,ImageDraw

def run(url,folder,smoke=False):
    image=Image.new('RGB',(64,64),'#176b60');d=ImageDraw.Draw(image)
    d.line([(10,40),(23,40),(29,17),(37,48),(45,28),(55,28)],fill='white',width=5)
    ready=threading.Event()
    def open_page(icon=None,item=None):webbrowser.open(url)
    def leave(icon,item):
        if ctypes.windll.user32.MessageBoxW(None,'退出将停止后台监测。确定退出？','代理流量观察台',0x24)==6:icon.stop()
    icon=pystray.Icon('ProxyTrafficMonitor',image,'代理流量观察台 · 后台记录中',pystray.Menu(
        pystray.MenuItem('打开监测网页 / 恢复界面',open_page,default=True),
        pystray.MenuItem('打开数据文件夹',lambda icon,item:os.startfile(folder)),
        pystray.Menu.SEPARATOR,pystray.MenuItem('退出监测',leave)))
    def setup(icon):
        icon.visible=True;ready.set()
        if smoke:threading.Timer(1,icon.stop).start()
        else:open_page()
    import notifier
    stop_notices=notifier.start(icon,folder/'traffic.sqlite3')
    try:icon.run(setup)
    finally:stop_notices()
    if smoke:assert ready.is_set(),'Fallback tray failed'
