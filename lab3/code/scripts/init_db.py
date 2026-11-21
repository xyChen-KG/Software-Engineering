"""显式建立或检查本机数据库；不会插入演示用户或虚构账目。"""

from app.config import Settings
from app.storage.database import Database


def main() -> None:
    """按当前配置初始化结构，已有数据保持原样。"""
    database = Database(Settings.from_env().database_path)
    version = database.initialize()
    print(f"数据库已就绪：{database.path}，结构版本：{version}")


if __name__ == "__main__":
    main()
