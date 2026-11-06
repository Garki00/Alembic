"""PyInstaller 打包入口：等价于 ``python -m web``。

单独放一个文件是因为 PyInstaller 需要一个脚本作为入口，而
``web/__main__.py`` 是给 ``-m`` 用的。
"""

from web.__main__ import main

if __name__ == "__main__":
    main()
