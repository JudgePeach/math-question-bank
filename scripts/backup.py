"""Create and verify a complete MathBank backup archive."""

from __future__ import annotations

import argparse

from mathbank.backup import create_full_backup, verify_full_backup


def main() -> int:
    parser = argparse.ArgumentParser(description="创建 MathBank 完整备份")
    parser.add_argument("--retain", type=int, default=10, help="保留最近多少份自动备份")
    args = parser.parse_args()
    backup_path = create_full_backup(retention=args.retain)
    manifest = verify_full_backup(backup_path)
    counts = manifest["database"]["row_counts"]
    print(f"完整备份已创建并验证: {backup_path}")
    print(f"数据表计数: {counts}，图片文件: {manifest['upload_file_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
