"""Manage opt-in local staging and external asset backing (never the main DB)."""
from __future__ import annotations

import argparse
import json
import os

from mathbank.paths import TIERED_STORAGE_CONFIG
from mathbank.tiered_storage import TieredStorage, initialize


def main():
    parser = argparse.ArgumentParser(description="外置资料存储：配置、查看、回填与安全停用")
    parser.add_argument("action", choices=["enable", "resume", "status", "restore-local", "disable"])
    parser.add_argument("--volume-uuid")
    parser.add_argument("--directory", default="业务资料/MathBank")
    args = parser.parse_args()
    if args.action == "status":
        if not TIERED_STORAGE_CONFIG.is_file():
            print(json.dumps({"enabled": False}))
        else:
            config = json.loads(TIERED_STORAGE_CONFIG.read_text(encoding="utf-8"))
            result = TieredStorage(config=config).status() if config.get("enabled") else {"enabled": False}
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    # A second CLI process has no knowledge of the server's worker thread.
    # Use the same OS runtime lock as restore; never disable a running server.
    from mathbank.backup import acquire_runtime_lock
    with acquire_runtime_lock():
        if args.action == "enable":
            if not args.volume_uuid:
                parser.error("enable 需要 --volume-uuid；请先正常关闭题库服务")
            storage = initialize(volume_uuid=args.volume_uuid, directory=args.directory)
            print(json.dumps(storage.status(), ensure_ascii=False, indent=2))
        elif args.action == "resume":
            config = json.loads(TIERED_STORAGE_CONFIG.read_text(encoding="utf-8"))
            storage = TieredStorage(config=config)  # Validate the existing catalog; no new baseline.
            config["enabled"] = True
            temporary = TIERED_STORAGE_CONFIG.with_suffix(".resume.tmp")
            with temporary.open("x", encoding="utf-8") as stream:
                os.chmod(temporary, 0o600)
                json.dump(config, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, TIERED_STORAGE_CONFIG)
            print(json.dumps({"resumed": True}))
        else:
            config = json.loads(TIERED_STORAGE_CONFIG.read_text(encoding="utf-8"))
            storage = TieredStorage(config=config)
            count = storage.restore_local()
            if args.action == "disable":
                config["enabled"] = False
                temporary = TIERED_STORAGE_CONFIG.with_suffix(".disable.tmp")
                with temporary.open("x", encoding="utf-8") as stream:
                    os.chmod(temporary, 0o600)
                    json.dump(config, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, TIERED_STORAGE_CONFIG)
            print(json.dumps({"local_verified": count, "disabled": args.action == "disable"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
