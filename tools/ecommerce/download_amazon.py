"""
Amazon Reviews 2023 数据下载器（MarketLens）。

针对 mcauleylab.ucsd.edu 这类“慢且会中途断连”的源做了健壮处理：
- 支持断点续传（Range 头），每次中断后从已写入字节处继续；
- 自动重试，直到下载完整（校验 Content-Range 的 total 或 Content-Length）；
- 支持只下载前 N 字节（--max-bytes），配合 import_amazon.py 的截断容错做快速 demo。

用法示例：
    python tools/ecommerce/download_amazon.py \
        --url "https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/meta_categories/meta_All_Beauty.jsonl.gz" \
        --out data/amazon/meta_All_Beauty.jsonl.gz
    # 快速 demo：评论只下载前 35MB
    python tools/ecommerce/download_amazon.py \
        --url "https://.../review_categories/All_Beauty.jsonl.gz" \
        --out data/amazon/All_Beauty.jsonl.gz --max-bytes 35000000
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.request
from pathlib import Path

CHUNK = 128 * 1024
RETRY_DELAY = 3


def download(url: str, dest: Path, max_bytes: int | None = None, max_attempts: int = 200) -> int:
    """
    下载文件，支持断点续传与自动重试。

    Returns:
        最终写入的字节数。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    existing = dest.stat().st_size if dest.exists() else 0
    target = max_bytes  # None 表示下载完整文件

    attempt = 0
    while True:
        attempt += 1
        if attempt > max_attempts:
            raise RuntimeError(f"下载失败：重试 {max_attempts} 次仍未完成")

        if target is not None and existing >= target:
            return existing

        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        if existing > 0:
            req.add_header("Range", f"bytes={existing}-")

        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                status = getattr(resp, "status", 200)

                # 服务端不支持断点（返回 200 而非 206）→ 从头重写
                if status == 200 and existing > 0:
                    existing = 0
                    mode = "wb"
                else:
                    mode = "ab"

                # 解析总大小
                total: int | None = target
                content_range = resp.headers.get("Content-Range")
                if content_range and "/" in content_range:
                    total = int(content_range.rsplit("/", 1)[-1])
                elif resp.headers.get("Content-Length"):
                    total = existing + int(resp.headers["Content-Length"])
                if total is None:
                    total = int(resp.headers.get("Content-Length", 0)) or None

                written_before = existing
                with open(dest, mode) as f:
                    while True:
                        chunk = resp.read(CHUNK)
                        if not chunk:
                            break
                        f.write(chunk)
                        existing += len(chunk)
                        if target is not None and existing >= target:
                            break

                if target is not None and existing >= target:
                    print(f"  完成（按 --max-bytes 截断）：{existing:,} 字节", flush=True)
                    return existing
                if total is not None and existing >= total:
                    print(f"  完成：{existing:,} 字节", flush=True)
                    return existing
                if written_before == existing and total is None:
                    # 无法确定总大小且无新数据，视为完成
                    return existing

        except Exception as exc:  # noqa: BLE001
            print(
                f"  [第 {attempt} 次尝试] 中断（已写入 {existing:,} 字节）：{exc}，"
                f"{RETRY_DELAY}s 后从断点继续",
                flush=True,
            )
            time.sleep(RETRY_DELAY)


def main() -> int:
    parser = argparse.ArgumentParser(description="下载 Amazon Reviews 2023 数据（断点续传）")
    parser.add_argument("--url", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-bytes", type=int, default=None, help="只下载前 N 字节（快速 demo）")
    args = parser.parse_args()

    dest = Path(args.out)
    print(f"下载: {args.url}")
    print(f"目标: {dest}")
    if args.max_bytes:
        print(f"截断: 前 {args.max_bytes:,} 字节")
    n = download(args.url, dest, max_bytes=args.max_bytes)
    print(f"下载结束，共 {n:,} 字节")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
