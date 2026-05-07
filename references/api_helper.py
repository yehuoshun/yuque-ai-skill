#!/usr/bin/env python3
# 语雀 API 通用工具函数（参考骨架）
# 用法：直接复制需要的函数到执行脚本中，或作为模板参考
# 依赖：仅 Python 标准库（urllib.request, json, concurrent.futures, time）

import urllib.request, urllib.error
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = "https://www.yuque.com/api/v2"
TIMEOUT = 30
MAX_RETRIES = 3
MAX_CONCURRENCY = 5


# ── 基础请求 ──────────────────────────────────────────────

def yuque_request(method, path, token, body=None, timeout=TIMEOUT):
    """发送语雀 API 请求，自动处理认证、超时、429 重试。
    
    Args:
        method: HTTP 方法 (GET/POST/PUT/DELETE)
        path: API 路径，如 "/user" 或 "/repos/123/docs"
        token: 语雀 Token
        body: 请求体 dict（可选）
        timeout: 超时秒数
        
    Returns:
        (response_data: dict, rate_remaining: int)
    """
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    headers = {
        "X-Auth-Token": token,
        "Content-Type": "application/json",
    }

    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            resp = urllib.request.urlopen(req, timeout=timeout)
            result = json.loads(resp.read())
            remaining = int(resp.headers.get("X-RateLimit-Remaining", -1))
            return result, remaining
        except urllib.error.HTTPError as e:
            remaining = int(e.headers.get("X-RateLimit-Remaining", -1))
            status = e.code
            if status == 429:
                if remaining == 0:
                    # 5000/h 配额耗尽，不硬等，抛异常让 Agent 层面通知用户
                    now = time.localtime()
                    wait_min = 60 - now.tm_min
                    raise RuntimeError(
                        f"小时配额耗尽（5000/h），需等待至 {now.tm_hour + 1:02d}:00（约 {wait_min} 分钟）。"
                        "请整点后重新触发任务。"
                    )
                else:
                    # 100/s 限制，等 1s 重试
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(1)
                        continue
            raise
        except (urllib.error.URLError, OSError) as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(1)
                continue
            raise

    raise RuntimeError(f"{method} {path} 重试 {MAX_RETRIES} 次后仍失败")


# ── 便捷封装 ──────────────────────────────────────────────

def yuque_get(path, token):
    return yuque_request("GET", path, token)

def yuque_post(path, token, body):
    return yuque_request("POST", path, token, body=body)

def yuque_put(path, token, body):
    return yuque_request("PUT", path, token, body=body)

def yuque_delete(path, token):
    return yuque_request("DELETE", path, token)


# ── 速率检查 ──────────────────────────────────────────────

def check_rate(remaining, label=""):
    """检查剩余配额，< 100 时等待 2s。返回 True 表示可继续。"""
    if remaining == 0:
        now = time.localtime()
        wait = 3600 - now.tm_min * 60 - now.tm_sec
        print(f"⏳ {label}小时配额耗尽，等待 {wait}s...")
        return False  # 需要等待整点后重试
    if remaining > 0 and remaining < 100:
        print(f"⚡ {label}剩余 {remaining} 次，等待 2s...")
        time.sleep(2)
    return True


# ── 并行批量请求 ──────────────────────────────────────────

def parallel_get(paths, token):
    """并行 GET 多个路径，返回 {path: (data, remaining)}"""
    results = {}
    with ThreadPoolExecutor(max_workers=min(MAX_CONCURRENCY, len(paths))) as pool:
        futures = {pool.submit(yuque_get, p, token): p for p in paths}
        for f in as_completed(futures):
            path = futures[f]
            try:
                results[path] = f.result()
            except Exception as e:
                results[path] = (None, -1)
                print(f"❌ {path} 失败: {e}")
    return results


# ── 分页获取全部文档 ─────────────────────────────────────

def fetch_all_docs(book_id, token):
    """分页获取知识库全部文档列表。"""
    docs = []
    offset = 0
    limit = 100
    while True:
        path = f"/repos/{book_id}/docs?offset={offset}&limit={limit}"
        data, _ = yuque_get(path, token)
        batch = data.get("data", [])
        docs.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return docs


# ── 检索 .data 字段 ──────────────────────────────────────

def get_data(path, token):
    """GET 请求并返回 .data 字段。"""
    result, remaining = yuque_get(path, token)
    return result.get("data"), remaining


# ── TOC 目录管理 ──────────────────────────────────────────

def add_docs_to_toc(book_id, doc_ids, token, max_retries=3):
    """安全地将文档添加到知识库目录，带重试和验证。
    
    语雀 POST 创建文档不会自动入目录，必须调 PUT /toc。
    本函数封装：调用 TOC API → 等待 → 验证文档是否出现在目录中 → 不在则重试。
    
    Args:
        book_id: 知识库 ID
        doc_ids: 要加入目录的文档 ID 列表（int 或 str）
        token: 语雀 Token
        max_retries: 最大重试次数（默认 3）
        
    Returns:
        (success: bool, failed_ids: list)
    """
    if not doc_ids:
        return True, []
    
    doc_ids = [int(d) for d in doc_ids]
    target_set = set(doc_ids)
    
    for attempt in range(max_retries):
        try:
            body = {
                "action": "appendNode",
                "action_mode": "sibling",
                "type": "DOC",
                "doc_ids": doc_ids
            }
            yuque_put(f"/repos/{book_id}/toc", token, body)
        except Exception as e:
            print(f"  ⚠️ TOC API 调用失败 (尝试 {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            return False, doc_ids
        
        # 等待语雀异步处理
        time.sleep(1)
        
        # 验证：检查这些文档是否出现在目录中
        try:
            toc_data, _ = yuque_get(f"/repos/{book_id}/toc", token)
            toc_nodes = toc_data.get("data", [])
            toc_doc_ids = {n["id"] for n in toc_nodes if n.get("type") == "DOC"}
            
            still_missing = target_set - toc_doc_ids
            if not still_missing:
                return True, []  # 全部入目录成功
            
            if attempt < max_retries - 1:
                doc_ids = list(still_missing)  # 只重试缺失的
                print(f"  🔄 仍有 {len(still_missing)} 篇未入目录，重试 ({attempt+2}/{max_retries})...")
                time.sleep(1)
                continue
            else:
                return False, list(still_missing)
                
        except Exception as e:
            print(f"  ⚠️ 目录验证失败: {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
                continue
            return False, doc_ids
    
    return False, doc_ids


def verify_toc_integrity(book_id, token, auto_fix=True):
    """校验知识库目录完整性，对比文档列表和目录。
    
    Args:
        book_id: 知识库 ID
        token: 语雀 Token
        auto_fix: 是否自动修复缺失（默认 True）
        
    Returns:
        dict: {
            "total_docs": int,       # 文档总数
            "toc_docs": int,         # 目录中文档数
            "missing": int,          # 缺失数
            "missing_ids": list,     # 缺失文档 ID 列表
            "fixed": int,            # 已修复数（auto_fix=True 时）
            "still_missing": int     # 修复后仍缺失数
        }
    """
    # 获取全部文档
    docs = fetch_all_docs(book_id, token)
    all_ids = {d["id"] for d in docs}
    
    # 获取目录
    toc_data, _ = yuque_get(f"/repos/{book_id}/toc", token)
    toc_nodes = toc_data.get("data", [])
    toc_ids = {n["id"] for n in toc_nodes if n.get("type") == "DOC"}
    
    missing_ids = sorted(all_ids - toc_ids)
    result = {
        "total_docs": len(all_ids),
        "toc_docs": len(toc_ids),
        "missing": len(missing_ids),
        "missing_ids": missing_ids,
        "fixed": 0,
        "still_missing": 0
    }
    
    if auto_fix and missing_ids:
        success, still_missing = add_docs_to_toc(book_id, missing_ids, token)
        result["fixed"] = len(missing_ids) - len(still_missing)
        result["still_missing"] = len(still_missing)
    
    return result


# ── CLI 入口 ──────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    usage = "用法: python3 api_helper.py <config.json> <method> <path> [body_json|paths...]"
    if len(sys.argv) < 3:
        print(usage)
        print("  method: get | post | put | delete | parallel_get | get_data | fetch_all_docs <book_id>")
        print("          verify_toc <book_id> | add_toc <book_id> <doc_id1> [doc_id2 ...]")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        config = json.load(f)
    token = config["token"]
    method = sys.argv[2]

    if method == "verify_toc":
        book_id = int(sys.argv[3])
        result = verify_toc_integrity(book_id, token, auto_fix=True)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif method == "add_toc":
        book_id = int(sys.argv[3])
        doc_ids = [int(x) for x in sys.argv[4:]]
        success, failed = add_docs_to_toc(book_id, doc_ids, token)
        print(json.dumps({"success": success, "failed": failed}, ensure_ascii=False))
    elif method == "get":
        data, _ = yuque_get(sys.argv[3], token)
        print(json.dumps(data, indent=2, ensure_ascii=False))
    elif method == "post":
        body = json.loads(sys.argv[4]) if len(sys.argv) > 4 else {}
        data, _ = yuque_post(sys.argv[3], token, body)
        print(json.dumps(data, indent=2, ensure_ascii=False))
    elif method == "put":
        body = json.loads(sys.argv[4]) if len(sys.argv) > 4 else {}
        data, _ = yuque_put(sys.argv[3], token, body)
        print(json.dumps(data, indent=2, ensure_ascii=False))
    elif method == "delete":
        data, _ = yuque_delete(sys.argv[3], token)
        print(json.dumps(data, indent=2, ensure_ascii=False))
    elif method == "parallel_get":
        paths = sys.argv[3:]
        results = parallel_get(paths, token)
        for p, (data, _) in results.items():
            print(f"\n=== {p} ===")
            print(json.dumps(data, indent=2, ensure_ascii=False) if data else "❌ 失败")
    elif method == "get_data":
        data, _ = get_data(sys.argv[3], token)
        print(json.dumps(data, indent=2, ensure_ascii=False) if data else "null")
    elif method == "fetch_all_docs":
        docs = fetch_all_docs(int(sys.argv[3]), token)
        print(json.dumps(docs, indent=2, ensure_ascii=False))
    else:
        print(f"未知方法: {method}")
        sys.exit(1)
