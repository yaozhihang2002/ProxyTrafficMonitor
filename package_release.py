"""Package a previously verified EXE using an explicit, telemetry-free whitelist."""
import argparse, hashlib, json, zipfile
from pathlib import Path

root=Path(__file__).resolve().parent
parser=argparse.ArgumentParser()
parser.add_argument('--verification',type=Path,required=True,help='JSON evidence containing exe_sha256')
args=parser.parse_args()
exe=root/'release/ProxyTrafficMonitor.exe'
evidence=json.loads(args.verification.read_text(encoding='utf-8-sig'))
assert evidence['exe_sha256']==hashlib.sha256(exe.read_bytes()).hexdigest(), 'Evidence must match this EXE'
files={
    'ProxyTrafficMonitor.exe':exe,
    'LICENSE':root/'LICENSE',
    'README.md':root/'README.md',
    'verification.json':args.verification,
}
for path in sorted((root/'third_party').iterdir()):
    if path.is_file():files['licenses/'+path.name]=path
archive=root/'release/ProxyTrafficMonitor-Windows-x64.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as output:
    for name,path in files.items():output.write(path,'ProxyTrafficMonitor/'+name)
with zipfile.ZipFile(archive) as check:
    assert check.testzip() is None
    assert hashlib.sha256(check.read('ProxyTrafficMonitor/ProxyTrafficMonitor.exe')).hexdigest()==evidence['exe_sha256']
digest=hashlib.sha256(archive.read_bytes()).hexdigest()
(archive.with_suffix('.zip.sha256')).write_text(digest+'  '+archive.name+'\n',encoding='ascii')
print(json.dumps({'archive':str(archive),'sha256':digest,'files':len(files)},indent=2))
