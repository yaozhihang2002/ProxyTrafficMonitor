import os,sys
from pathlib import Path
ASSETS=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
ROOT=Path(os.environ.get('PROXY_MONITOR_HOME',Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else ASSETS))
