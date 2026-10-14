# 本地构建

Python 3.12，Windows，Tkinter。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller==6.22.3
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --windowed --name RainChoice --distpath dist app.py
```

程序的配置和历史记录写在程序旁边，API Key 和登录 cookies 不落盘。构建机器本地依赖版本已锁在 requirements.txt。
