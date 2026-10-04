"""Local controller preferences. Secrets protected for the current Windows user."""
import base64, ctypes, json, os, tempfile
from ctypes import wintypes
from pathlib import Path
from urllib.parse import urlsplit

from paths import ROOT
FILE=ROOT/'data'/'settings.json'
DEFAULT={'mode':'auto','endpoint':'http://127.0.0.1:9090','pipe':'','secret_dpapi':'',
         'interval':1,'connection_days':7,'total_days':90,'target_mb':256,'csv_days':30,'csv_budget_mb':1024,'alert_enabled':False,'alert_window':10,'alert_mb':100}

def load(path=FILE):
    if not Path(path).exists():return dict(DEFAULT)
    return {**DEFAULT,**json.loads(Path(path).read_text(encoding='utf-8'))}

def public(config):
    return {**{k:v for k,v in config.items() if k!='secret_dpapi'},'has_secret':bool(config.get('secret_dpapi'))}

def protect(secret):
    if not secret:return ''
    class Blob(ctypes.Structure):_fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    raw=secret.encode('utf-8'); buf=ctypes.create_string_buffer(raw)
    src=Blob(len(raw),ctypes.cast(buf,ctypes.POINTER(ctypes.c_ubyte)));out=Blob()
    dll=ctypes.WinDLL('crypt32',use_last_error=True)
    dll.CryptProtectData.argtypes=[ctypes.POINTER(Blob),wintypes.LPCWSTR,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    if not dll.CryptProtectData(ctypes.byref(src),None,None,None,None,1,ctypes.byref(out)):
        raise ValueError('无法使用 Windows 加密 Secret')
    try:return base64.b64encode(ctypes.string_at(out.data,out.size)).decode('ascii')
    finally:
        kernel=ctypes.WinDLL('kernel32');kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree(out.data)

def validate(values, old=None):
    old=old or DEFAULT; result=dict(old)
    mode=values.get('mode',old['mode'])
    if mode not in ('auto','pipe','http'):raise ValueError('连接方式无效')
    endpoint=str(values.get('endpoint',old['endpoint'])).strip().rstrip('/')
    u=urlsplit(endpoint)
    if u.scheme not in ('http','https') or u.hostname not in ('127.0.0.1','localhost','::1') or not u.port or u.username or u.password or u.path or u.query or u.fragment:
        raise ValueError('请填写本机控制接口，例如 http://127.0.0.1:9090；不能填写代理端口、路径或订阅链接')
    pipe=str(values.get('pipe',old['pipe'])).strip()
    if pipe and (len(pipe)>200 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in pipe)):
        raise ValueError('管道名只允许字母、数字、下划线和连字符，不含路径前缀')
    result.update(mode=mode,endpoint=endpoint,pipe=pipe)
    for name,low,high in [('interval',1,10),('connection_days',1,30),('total_days',7,365),('target_mb',64,2048),('csv_days',1,365),('csv_budget_mb',64,10240),('alert_window',1,60),('alert_mb',1,102400)]:
        try:n=int(values.get(name,old[name]))
        except (ValueError,TypeError):raise ValueError('数值设置无效')
        if not low<=n<=high:raise ValueError('%s 必须在 %s–%s 之间'%(name,low,high))
        result[name]=n
    enabled=values.get('alert_enabled',old.get('alert_enabled',False))
    if not isinstance(enabled,bool):raise ValueError('提醒开关必须为布尔值')
    result['alert_enabled']=enabled
    if values.get('clear_secret'):result['secret_dpapi']=''
    elif values.get('secret'):
        secret=str(values['secret'])
        if len(secret)>4096 or '\r' in secret or '\n' in secret:raise ValueError('Secret 长度或格式无效')
        result['secret_dpapi']=protect(secret)
    return result

def save(config,path=FILE):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='settings-',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(config,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)
