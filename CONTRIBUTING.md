# 开发与验证

建议 Windows 10/11、Python 3.12 或更新版本，Node.js 仅用于图表数学测试。

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-build.txt
.venv\Scripts\python desktop.py
```

后台程序仅连接本机 Clash 控制接口，数据保存在程序目录 data 下。请在独立测试目录运行；不要使用实际历史数据做破坏性测试。源码后台可用 `python monitor.py` 运行，不依赖第三方 Python 包；桌面托盘与 EXE 构建使用上述依赖。

```powershell
python -m unittest discover -s . -p 'test_*.py'
node test_chart.cjs
.\build.ps1
```

界面由 index.html、ui.css 和 trend-chart.js 组成。图表必须区分缺失样本和零流量，显示字节数/分钟而不是字节数/秒；最近活动独立于累计观察范围。提醒默认关闭。关闭网页、暂停刷新和折叠图表不能停止采集。

提交问题时只提供脱敏错误和客户端版本，不公开控制口令、订阅信息或个人流量记录。提交前检查桌面与窄屏布局、鼠标读数、固定读数、方向键、缺失样本、两条序列和 CSV 占用补写。

分发前运行独立 EXE 启动及回退检查，记录验证结果与 EXE SHA256，再用 `python package_release.py --verification release/verification.json` 生成只含白名单文件的 ZIP 与校验文件。验证 JSON 应为脱敏汇总，不包含个人数据。
