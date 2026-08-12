"""Verify or explicitly restore a complete MathBank backup."""

from __future__ import annotations

import argparse
from pathlib import Path

from mathbank.backup import RuntimeLockError, restore_full_backup, verify_full_backup


def main() -> int:
    parser = argparse.ArgumentParser(description="验证或恢复 MathBank 完整备份")
    parser.add_argument("archive", type=Path, help="mathbank-backup-*.zip 路径")
    parser.add_argument("--apply", action="store_true", help="实际恢复；默认仅验证")
    parser.add_argument("--yes", action="store_true", help="确认已关闭题库服务并允许覆盖")
    args = parser.parse_args()

    manifest = verify_full_backup(args.archive)
    print(f"备份验证通过: {manifest['created_at']}")
    print(f"数据表计数: {manifest['database']['row_counts']}")
    if not args.apply:
        print("当前仅验证，未修改任何数据。")
        return 0
    if not args.yes:
        parser.error("实际恢复必须同时指定 --apply --yes，并先关闭题库服务")
    try:
        safety_backup = restore_full_backup(args.archive)
    except RuntimeLockError as exc:
        parser.error(str(exc))
    print(f"恢复完成；恢复前数据已备份到: {safety_backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
