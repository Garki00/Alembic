"""PyInstaller entry point: equivalent to ``python -m web``.

It lives in its own file because PyInstaller needs a script as its entry point,
while ``web/__main__.py`` serves ``-m``.
"""

from web.__main__ import main

if __name__ == "__main__":
    main()
