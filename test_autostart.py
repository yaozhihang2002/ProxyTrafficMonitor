import types,unittest
from unittest.mock import patch
import autostart


class AutostartTests(unittest.TestCase):
    def test_enable_disable_and_read_back(self):
        values={}
        class Key:
            def __enter__(self):return self
            def __exit__(self,*args):pass
        def query(key,name):
            if name not in values:raise FileNotFoundError()
            return values[name],1
        def delete(key,name):
            if name not in values:raise FileNotFoundError()
            del values[name]
        registry=types.SimpleNamespace(HKEY_CURRENT_USER=1,KEY_SET_VALUE=2,REG_SZ=1,
            OpenKey=lambda *a:Key(),CreateKeyEx=lambda *a:Key(),QueryValueEx=query,
            SetValueEx=lambda key,name,res,kind,value:values.update({name:value}),DeleteValue=delete)
        with patch.dict('sys.modules',{'winreg':registry}),patch.object(autostart,'command',return_value='"C:\\My Apps\\Monitor.exe" --recovered'):
            self.assertFalse(autostart.enabled())
            autostart.set_enabled(True);self.assertTrue(autostart.enabled())
            self.assertIn('--recovered',values[autostart.NAME])
            autostart.set_enabled(False);self.assertFalse(autostart.enabled())
            autostart.set_enabled(False)

    def test_source_run_is_not_registered(self):
        with patch.object(autostart.sys,'frozen',False,create=True):
            with self.assertRaises(RuntimeError):autostart.command()


if __name__=='__main__':unittest.main()
