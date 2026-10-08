"""Bounded local diagnostics and recovery for unexpected thread/process exits."""
import hashlib, logging, logging.handlers, os, subprocess, sys, threading, time
from pathlib import Path
from paths import ROOT

LOG=logging.getLogger('proxy-monitor.lifecycle')
EXIT_REQUESTED=False

def setup(name='lifecycle.log'):
    folder=ROOT/'data';folder.mkdir(parents=True,exist_ok=True)
    if not LOG.handlers:
        handler=logging.handlers.RotatingFileHandler(folder/name,maxBytes=1024*1024,backupCount=3,encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(process)d %(threadName)s %(levelname)s %(message)s'))
        LOG.addHandler(handler);LOG.setLevel(logging.INFO)
    def exception(args):
        LOG.error('Thread terminated: %s',args.thread.name,exc_info=(args.exc_type,args.exc_value,args.exc_traceback))
    threading.excepthook=exception

def request_exit():
    global EXIT_REQUESTED
    EXIT_REQUESTED=True
    LOG.info('User requested exit')

def resilient(stop,target,*args,retry=2):
    while not stop.is_set():
        try:
            target(*args)
            if not stop.is_set():LOG.error('Background task returned unexpectedly: %s',target.__name__)
        except Exception:
            LOG.exception('Background task failed: %s; restarting',target.__name__)
        if not stop.is_set():stop.wait(retry)

class ChildJob:
    """Close descendant collectors on Windows when a crashed desktop is replaced."""
    def __init__(self,child):
        self.handle=None
        if os.name!='nt' or not isinstance(child,subprocess.Popen):return
        import ctypes
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',w.DWORD),('min_ws',ctypes.c_size_t),('max_ws',ctypes.c_size_t),('active',w.DWORD),('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
        class IO(ctypes.Structure):
            _fields_=[(n,ctypes.c_uint64) for n in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
        class Extended(ctypes.Structure):
            _fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR];self.kernel.CreateJobObjectW.restype=w.HANDLE
        self.kernel.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
        self.kernel.CloseHandle.argtypes=[w.HANDLE]
        handle=self.kernel.CreateJobObjectW(None,None)
        info=Extended();info.basic.flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if handle and self.kernel.SetInformationJobObject(handle,9,ctypes.byref(info),ctypes.sizeof(info)) and self.kernel.AssignProcessToJobObject(handle,int(child._handle)):
            self.handle=handle
        else:
            LOG.warning('Cannot attach child cleanup job: Windows error %s',ctypes.get_last_error())
            if handle:self.kernel.CloseHandle(handle)
    def close(self):
        if self.handle:self.kernel.CloseHandle(self.handle);self.handle=None

def supervise(command,popen=subprocess.Popen,clock=time.monotonic,sleep=time.sleep):
    """Exit code zero means deliberate/normal shutdown, never restart it."""
    failures=0
    while True:
        started=clock()
        LOG.info('Starting monitored desktop')
        child=popen(command+(['--recovered'] if failures else []),creationflags=0x08000000 if os.name=='nt' else 0)
        job=ChildJob(child)
        try:code=child.wait()
        finally:job.close()
        LOG.info('Desktop exited: code=%s, uptime=%.1fs',code,clock()-started)
        if code==0:return
        failures=1 if clock()-started>=60 else failures+1
        delay=min(60,2**min(failures,6))
        LOG.warning('Unexpected exit; restart in %ss',delay)
        sleep(delay)

def launch_supervisor(port):
    setup('supervisor.log')
    handle=None
    if os.name=='nt':
        import ctypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_bool,ctypes.c_wchar_p]
        kernel.CreateMutexW.restype=ctypes.c_void_p
        kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        key=hashlib.sha256((str(ROOT.resolve()).lower()+':'+str(port)).encode()).hexdigest()[:24]
        ctypes.set_last_error(0)
        handle=kernel.CreateMutexW(None,False,'Local\\ProxyTrafficMonitor-'+key)
        if not handle:raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error()==183:
            kernel.CloseHandle(handle)
            import webbrowser
            webbrowser.open(f'http://127.0.0.1:{port}')
            return
    command=[sys.executable]
    if not getattr(sys,'frozen',False):command.append(str(Path(__file__).resolve().parent/'desktop.py'))
    command+=['--managed']+[arg for arg in sys.argv[1:] if arg!='--managed']
    try:supervise(command)
    finally:
        if handle:kernel.CloseHandle(handle)
