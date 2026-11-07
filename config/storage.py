# config/storage.py
"""用户级数据目录的初始化与一次性迁移。

打包后的应用装在 Program Files 这类只读位置，配置和会话必须放到用户目录。
为了不让人「升级后配置全没了」，首次启动会把仓库里的旧 ``data/`` / ``memory/``
复制过去（只在目标为空时复制，原目录保留不动，可随时回退）。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from config.settings import settings

LEGACY_NAMES = ("data", "memory")
LEGACY_HOME_NAME = "SmartAgent"
LEGACY_HOME_ENTRIES = ("data", "memory", "logs")


def _is_empty(path: Path) -> bool:
    try:
        next(path.iterdir())
        return False
    except StopIteration:
        return True
    except OSError:
        return False


def _migrate_legacy_home(home_path: Path) -> list[str]:
    """Copy data from ``%APPDATA%\\SmartAgent`` to the new ``Alembic`` home.

    Only runs for the default directory layout (``.../Alembic``); a custom
    ``ALEMBIC_HOME`` is left exactly where the user pointed it. The old
    directory is copied, never deleted, so the user can still roll back.
    """
    if home_path.name != "Alembic":
        return []
    legacy_home = home_path.parent / LEGACY_HOME_NAME
    if not legacy_home.is_dir() or legacy_home == home_path:
        return []

    migrated: list[str] = []
    for name in LEGACY_HOME_ENTRIES:
        source = legacy_home / name
        target = home_path / name
        if not source.is_dir():
            continue
        if target.exists() and not _is_empty(target):
            continue
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        migrated.append(f"{LEGACY_HOME_NAME}/{name}")
    return migrated


def ensure_storage(
    home: str | Path | None = None,
    legacy: str | Path | None = None,
) -> dict:
    """准备数据目录，必要时迁移旧数据。

    Args:
        home: 目标用户目录；默认 ``settings.app_home``。
        legacy: 旧数据所在的项目根目录；默认 ``settings.legacy_root``。

    Returns:
        ``{"home": Path, "migrated": [名字, ...]}``
    """
    home_path = Path(home or settings.app_home).expanduser().resolve()
    legacy_path = Path(legacy or settings.legacy_root).expanduser().resolve()
    home_path.mkdir(parents=True, exist_ok=True)

    # 旧品牌目录优先于仓库示例数据：用户真实数据比 demo data/ 更重要。
    migrated: list[str] = list(_migrate_legacy_home(home_path))
    if home_path != legacy_path:
        for name in LEGACY_NAMES:
            source = legacy_path / name
            target = home_path / name
            if not source.is_dir():
                continue
            # 目标已经有内容就不动，避免覆盖用户后来改过的配置
            if target.exists() and not _is_empty(target):
                continue
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target)
            migrated.append(name)

    return {"home": home_path, "migrated": migrated}
