"""Windows portable launcher: bundled Python, local controller only."""
import json,os,sys,threading,urllib.request,webbrowser,time,ctypes
if getattr(sys,'frozen',False):
    # Do not inherit a different Python/Tcl installation from the target PC.
    os.environ['TCL_LIBRARY']=os.path.join(sys._MEIPASS,'_tcl_data')
    os.environ['TK_LIBRARY']=os.path.join(sys._MEIPASS,'_tk_data')
    os.environ.pop('TCLLIBPATH',None)
import tkinter as tk
from tkinter import messagebox
import monitor
from paths import ROOT
from tray import Tray
import tray_fallback
import close_ui
import lifecycle

def window_or_tray(url,smoke=False):
    try:
        if '--simulate-tk-failure' in sys.argv:
            raise tk.TclError("Can't find a usable init.tcl (injected startup test)")
        if '--tray-only' in sys.argv:raise tk.TclError('Requested tray-only mode')
        return tk.Tk()
    except tk.TclError as error:
        (ROOT/'data'/'window-fallback.log').write_text(str(error),encoding='utf-8')
        tray_fallback.run(url,ROOT/'data',smoke=smoke)
        return None

def main():
    lifecycle.setup()
    lifecycle.LOG.info('Desktop starting')
    url=f'http://127.0.0.1:{monitor.PORT}'
    smoke='--smoke-test' in sys.argv
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url+'/api/status',timeout=2) as r:
            existing=json.load(r)
        if existing.get('product')=='proxy-traffic-monitor':
            if not smoke:webbrowser.open(url)
            return
    except Exception:pass
    monitor.initialize()
    server=monitor.ThreadingHTTPServer(('127.0.0.1',monitor.PORT),monitor.Handler)
    stop=threading.Event()
    collector=threading.Thread(target=lifecycle.resilient,args=(stop,monitor.worker,stop),name='collector',daemon=True)
    service=threading.Thread(target=server.serve_forever,name='http',daemon=True)
    collector.start();service.start()
    def watch_http():
        while not stop.wait(2):
            if not service.is_alive():
                lifecycle.LOG.error('HTTP service terminated; restarting desktop')
                os._exit(70)
    threading.Thread(target=watch_http,name='health',daemon=True).start()
    def shutdown():
        stop.set();server.shutdown();collector.join(timeout=20);server.server_close()
    if smoke:
        if '--tray-only' in sys.argv or '--simulate-tk-failure' in sys.argv:
            assert window_or_tray(url,smoke=True) is None
            (ROOT/'fallback-test.json').write_text('{"tk_free_tray":true}',encoding='utf-8')
            shutdown();return
        probe=tk.Tk();probe.withdraw();probe.update()
        tray=Tray(probe,lambda:None,lambda:None,lambda:None)
        assert tray.ready.wait(5),'Tray icon not ready'
        tray.restore();probe.update();tray.hide();probe.update()
        assert probe.state()=='withdrawn'
        tray.actions.put('restore');tray.poll();probe.update()
        assert probe.state()=='normal'
        preference=ROOT/'data'/'close-choice-smoke.json'
        close_ui.save_action(preference,'ask')
        assert close_ui.load_action(preference)=='ask'
        actions=[]
        dialog=close_ui.CloseDialog(probe,preference,lambda:(actions.append('hide'),tray.hide()),lambda:actions.append('exit'))
        probe.update();dialog.remember.set(True);dialog.confirm.invoke();probe.update()
        assert actions==['hide'] and probe.state()=='withdrawn' and close_ui.load_action(preference)=='hide'
        tray.restore();probe.update()
        dialog=close_ui.CloseDialog(probe,preference,lambda:actions.append('hide'),lambda:actions.append('exit'))
        probe.update();dialog.choice.set('exit');dialog.remember.set(True);dialog.confirm.invoke()
        assert actions==['hide','exit'] and close_ui.load_action(preference)=='exit'
        close_ui.save_action(preference,'ask')
        dialog=close_ui.CloseDialog(probe,preference,lambda:actions.append('hide'),lambda:actions.append('exit'))
        probe.update();dialog.cancel()
        assert actions==['hide','exit'] and close_ui.load_action(preference)=='ask'
        (ROOT/'close-ui-test.json').write_text(json.dumps({'hide_and_remember':True,'exit_and_remember':True,'cancel':True,'restore_prompt':True}),encoding='utf-8')
        tray.stop();probe.destroy()
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url,timeout=2) as response:
            assert response.status==200 and b'<html' in response.read().lower()
        time.sleep(12)
        for action in ('start','all'):
            request=urllib.request.Request(url+'/api/view',data=json.dumps({'action':action}).encode(),headers={'Content-Type':'application/json','X-Local-Request':'1'})
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=3) as response:
                assert json.load(response)['ok']
            assert bool(monitor.report()['meta']['view_active'])==(action=='start')
        result=monitor.report()
        (ROOT/'smoke-result.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
        shutdown();return
    try:window=window_or_tray(url)
    except Exception:
        shutdown();raise
    if window is None:shutdown();return
    window.report_callback_exception=lambda kind,value,trace:lifecycle.LOG.error('Window callback failed',exc_info=(kind,value,trace))
    window.title('代理流量观察台');window.geometry('460x320');window.resizable(False,False)
    tk.Label(window,text='代理流量观察台',font=('Microsoft YaHei UI',18,'bold')).pack(pady=(20,8))
    tk.Label(window,text='后台正在监测 · 关闭网页仍会记录\n隐藏到托盘后继续记录，双击托盘图标可恢复。',font=('Microsoft YaHei UI',10)).pack(pady=8)
    tk.Button(window,text='打开监测网页 / 连接设置',command=lambda:webbrowser.open(url),width=32).pack(pady=5)
    tk.Button(window,text='打开数据文件夹',command=lambda:os.startfile(ROOT/'data'),width=32).pack(pady=5)
    preference=ROOT/'data'/'desktop-settings.json'
    close_dialog=None
    def exit_now():
        lifecycle.request_exit()
        tray.stop();window.destroy()
    def close():
        if messagebox.askyesno('退出监测','退出将停止后台记录。确定退出？',parent=window):exit_now()
    def on_close():
        nonlocal close_dialog
        action=close_ui.load_action(preference)
        if action=='hide':tray.hide()
        elif action=='exit':exit_now()
        elif close_dialog is not None and close_dialog.window.winfo_exists():
            close_dialog.window.lift();close_dialog.confirm.focus_set()
        else:close_dialog=close_ui.CloseDialog(window,preference,tray.hide,exit_now)
    def reset_close_choice():
        close_ui.save_action(preference,'ask')
        messagebox.showinfo('关闭行为设置','已恢复提醒。下次点击窗口叉号时，可重新选择隐藏到托盘或彻底退出。',parent=window)
    tray=Tray(window,lambda:webbrowser.open(url),lambda:os.startfile(ROOT/'data'),close)
    import notifier
    stop_notices=notifier.start(tray.icon,monitor.DB)
    actions=tk.Frame(window);actions.pack(pady=5)
    tk.Button(actions,text='隐藏到托盘',command=tray.hide,width=15).pack(side='left',padx=4)
    tk.Button(actions,text='退出监测',command=close,width=15).pack(side='left',padx=4)
    tk.Button(window,text='关闭行为设置',command=reset_close_choice,relief='flat',fg='#397568').pack(pady=3)
    window.protocol('WM_DELETE_WINDOW',on_close)
    if '--recovered' in sys.argv:tray.hide()
    else:webbrowser.open(url)
    try:window.mainloop()
    finally:
        stop_notices()
        if not tray.closed:tray.stop()
        shutdown()
    if not lifecycle.EXIT_REQUESTED:
        raise RuntimeError('监测窗口意外结束，守护进程将恢复后台')

if __name__=='__main__':
    try:
        if '--managed' not in sys.argv and '--smoke-test' not in sys.argv:
            lifecycle.launch_supervisor(monitor.PORT)
        else:main()
    except Exception as error:
        lifecycle.setup();lifecycle.LOG.exception('Desktop failure')
        if '--managed' in sys.argv:sys.exit(0 if lifecycle.EXIT_REQUESTED else 70)
        if '--smoke-test' in sys.argv:raise
        ctypes.windll.user32.MessageBoxW(None,f'无法启动监测：{error}\n请解压到有写入权限的文件夹，并检查端口是否被占用。','启动失败',0x10)
