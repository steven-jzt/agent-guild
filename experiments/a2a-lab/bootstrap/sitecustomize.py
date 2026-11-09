"""让 Python 在本机 DSH 沙箱里创建“可写”的临时目录。

背景：sandbox 下 tempfile 用 mode=0o700 建目录，生成的 DACL 会把沙箱进程
自己也挡在外面（PermissionError / WinError 5）。这里把 os.mkdir 的 mode
统一放宽到 0o777，使临时目录继承可写 ACL。

用法：PYTHONPATH 指向本文件所在目录，Python 启动时会自动 import sitecustomize。
"""

import os

_real_mkdir = os.mkdir


def _mkdir(path, mode=0o777, *, dir_fd=None):
    if dir_fd is None:
        return _real_mkdir(path, 0o777)
    return _real_mkdir(path, 0o777, dir_fd=dir_fd)


os.mkdir = _mkdir
