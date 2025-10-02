"""测试 show_deleted 参数功能"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("STORAGE_DISK_PATH", "./data/uploads")
os.environ.setdefault("GOOGLE_SERVICE_ACCOUNT_CREDENTIALS", "{}")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, DN
from app.crud import search_dn_list


def test_show_deleted_parameter():
    """测试 show_deleted 参数是否正确过滤已删除的记录"""

    # 创建内存数据库
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    try:
        # 创建测试数据
        # 1. 未删除的记录
        dn1 = DN(dn_number="DN001", status="ON THE WAY", status_delivery="On the way", is_deleted="N")

        # 2. 已软删除的记录
        dn2 = DN(dn_number="DN002", status="POD", status_delivery="POD", is_deleted="Y")

        # 3. is_deleted 为 None 的记录（应视为未删除）
        dn3 = DN(dn_number="DN003", status="ARRIVED AT SITE", status_delivery="On Site", is_deleted=None)

        db.add_all([dn1, dn2, dn3])
        db.commit()

        # 测试 1: show_deleted=False (默认)，应该只返回未删除的记录
        total_active, items_active = search_dn_list(db, show_deleted=False)
        print(f"✓ show_deleted=False: 返回 {total_active} 条记录")
        assert total_active == 2, f"应该返回 2 条未删除记录，实际返回 {total_active}"
        dn_numbers_active = {item.dn_number for item in items_active}
        assert dn_numbers_active == {"DN001", "DN003"}, f"应该只包含 DN001 和 DN003，实际包含 {dn_numbers_active}"
        print(f"  包含的 DN: {', '.join(sorted(dn_numbers_active))}")

        # 测试 2: show_deleted=True，应该返回所有记录（包括已删除）
        total_all, items_all = search_dn_list(db, show_deleted=True)
        print(f"✓ show_deleted=True: 返回 {total_all} 条记录")
        assert total_all == 3, f"应该返回 3 条记录（包括已删除），实际返回 {total_all}"
        dn_numbers_all = {item.dn_number for item in items_all}
        assert dn_numbers_all == {"DN001", "DN002", "DN003"}, f"应该包含所有 DN，实际包含 {dn_numbers_all}"
        print(f"  包含的 DN: {', '.join(sorted(dn_numbers_all))}")

        # 测试 3: 验证返回的记录中哪些是已删除的
        deleted_records = [item for item in items_all if item.is_deleted == "Y"]
        print(f"✓ 已删除记录数: {len(deleted_records)}")
        assert len(deleted_records) == 1, f"应该有 1 条已删除记录，实际有 {len(deleted_records)}"
        assert deleted_records[0].dn_number == "DN002", "已删除记录应该是 DN002"

        print("\n✅ 所有测试通过！")
        print("\n功能总结:")
        print("- show_deleted=False (默认): 只返回未删除的记录")
        print("- show_deleted=True: 返回所有记录，包括已软删除的记录")
        print("- is_deleted=None 或 'N' 的记录被视为未删除")
        print("- is_deleted='Y' 的记录被视为已删除")

    finally:
        db.close()


if __name__ == "__main__":
    test_show_deleted_parameter()
