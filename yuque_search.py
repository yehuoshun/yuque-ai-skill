#!/usr/bin/env python3
"""
语雀知识库搜索管线 —— 两级索引 + 降级搜索。

数据模型：
  - 索引总库 (wwqac0): 路由层。Format A — 标题=[索引] keyword，正文=JSON {keyword, sub_docs:[{doc_id, book_id, namespace}]}
    一个路由文档 → 一个或多个子库索引文档（一对多，sub_docs 数组）
  - 索引子库 (rqgc16): 索引层。JSON 格式 {keyword, source_entries:[{doc_id, book_id, title, namespace, content_segment}]}
    一个索引文档 → 多个源文档引用
  - 源文档分散在多个知识库中，跨库读取

搜索流程：
  LLM 生成搜索词 → search_master(路由) → 命中子库 namespace + doc_id
  → search_and_parse_sub(子库索引) → 解析 source_entries
  → read_source_docs_across_books(跨库读原文)
  → LLM 生成答案

用法：
    from yuque_search import SearchPipeline
    sp = SearchPipeline()

    routes = sp.search_master(["Java 面试", "Java 面试题"])
    entries = sp.search_and_parse_sub(["Java 面试", "Java 面试题"])
    docs = sp.read_source_docs_across_books(entries)
    ctx = sp.get_context_for_llm(entries)
"""

import json
import os
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from yuque_api import YuqueAPI


def _get_available_memory_mb():
    """获取可用内存 (MB)，用于 OOM 保护"""
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                if line.startswith('MemAvailable:'):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 1024  # 默认 1GB


def _llm_safe_concurrency(default=3):
    """
    LLM 轨并发数：clamp(floor(mem_mb / 1024), 1, 3)。

    即：可用内存 ≥3GB→3, ≥2GB→2, <2GB→1。
    用于索引文档读取、源文档跨库读取等 LLM 轨批量操作。
    """
    mem = _get_available_memory_mb()
    return max(1, min(mem // 1024, default))


class LLMConcurrencyTracker:
    """
    LLM 轨超时降级追踪器。

    连续 3 次批量读取耗时 >10s → 降 1 级并发
    单次 >30s → 暂停（并发=0，本轮跳过）
    """

    def __init__(self):
        self.slow_count = 0
        self.base = _llm_safe_concurrency(3)
        self._level = self.base
        self.paused = False

    def record_batch(self, elapsed_seconds, doc_count):
        """记录一次批量读取的耗时，自动调整并发级别"""
        if elapsed_seconds > 30:
            self._level = 0
            self.paused = True
            return
        if elapsed_seconds > 10:
            self.slow_count += 1
            if self.slow_count >= 3:
                self._level = max(1, self._level - 1)
                self.slow_count = 0
        else:
            self.slow_count = 0
            # 恢复：如果没暂停且低于基准，逐步回升
            if not self.paused and self._level < self.base:
                self._level = min(self._level + 1, self.base)

    def reset(self):
        """新一轮搜索时重置"""
        self.slow_count = 0
        self._level = self.base
        self.paused = False

    @property
    def workers(self):
        return max(0, self._level)


# ── 工具函数 ────────────────────────────────────────

def parse_master_body(body):
    """解析索引总库文档正文（可能被 markdown 代码块包裹）"""
    if not body or not body.strip():
        return None
    text = body.strip()
    # 去掉 ```json / ``` 包裹
    text = re.sub(r'^```(?:json)?\s*\n?', '', text)
    text = re.sub(r'\n?```\s*$', '', text)
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        return None


def parse_sub_index_body(body):
    """
    解析索引子库文档正文。

    支持两种格式：
    1. JSON（新格式，优先）
       {"keyword":"...", "source_entries":[{"doc_id":..., "book_id":..., "title":"...", "namespace":"..."}]}
    2. Markdown（旧格式，兼容）
       ### 文档标题\n- **源文档ID**: xxx\n- **源知识库ID**: xxx\n...
    """
    if not body or not body.strip():
        return []

    text = body.strip()

    # 尝试 JSON
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "source_entries" in data:
            return data["source_entries"]
        # 也可能是数组
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    # 回退：Markdown 格式
    return _parse_sub_index_markdown(text)


def _parse_sub_index_markdown(body):
    """解析 Markdown 格式的子库索引文档（兼容旧数据）"""
    entries = []
    blocks = re.split(r'\n(?=### )', body)

    for block in blocks:
        title_match = re.match(r'### (.+)', block)
        if not title_match:
            continue
        doc_title = title_match.group(1).strip()

        entry = {"title": doc_title}
        patterns = {
            "doc_id":     r'\**源文档ID\**[：:]\s*(\d+)',
            "book_id":    r'\**源知识库ID\**[：:]\s*(\d+)',
            "namespace":  r'\**Namespace\**[：:]\s*(\S+)',
            "keywords":   r'\**关键词\**[：:]\s*(.+)',
            "slug":       r'\**Slug\**[：:]\s*(\S+)',
            "doc_type":   r'\**类型\**[：:]\s*(.+)',
            "content_segment": r'\**内容段\**[：:]\s*(.+)',
        }
        for key, pattern in patterns.items():
            m = re.search(pattern, block)
            if m:
                val = m.group(1).strip()
                if key in ("doc_id", "book_id"):
                    entry[key] = int(val)
                else:
                    entry[key] = val

        if entry.get("doc_id"):
            entries.append(entry)

    return entries


# ── 搜索管线 ────────────────────────────────────────

class SearchPipeline:
    """
    语雀搜索管线。

    - 索引总库路由：search_master() 搜索路由文档，返回子库引用
    - 索引子库搜索：search_and_parse_sub() 搜索+解析子库索引文档
    - 跨库原文读取：read_source_docs_across_books()
    - 降级搜索：degraded_search() 搜全库（无 scope）
    """

    def __init__(self, api=None, config_path=None):
        self.api = api or YuqueAPI(config_path)
        self._llm_tracker = LLMConcurrencyTracker()  # LLM 轨超时降级
        self._sub_books_cache = None  # 动态发现的子库缓存

    # ── 路由：搜索索引总库 ──────────────────────────

    def search_master(self, keywords):
        """
        搜索索引总库，返回命中的子库引用列表。

        Args:
            keywords: 搜索词列表，如 ["Java 面试", "Java 面试题"]

        Returns:
            list[dict]: 子库引用，去重（按 sub_doc_id）
              [{keyword, sub_doc_id, sub_book_id, sub_namespace, sub_title}, ...]
              
        只处理 Format A（路由文档，含 sub_docs 字段）。
        """
        master_ns = self.api.index_master_namespace
        master_bid = self.api.index_master_book_id
        if not master_ns or not master_bid:
            return []

        results = self.api.batch_search(keywords, scope=master_ns)
        # 收集所有命中 doc_id
        doc_ids = []
        seen = set()
        for query, result in results.items():
            docs = result.get("docs", []) if isinstance(result, dict) else []
            for doc in docs:
                doc_id = doc.get("id")
                if doc_id and doc_id not in seen:
                    seen.add(doc_id)
                    doc_ids.append(doc_id)

        if not doc_ids:
            return []

        # 并发读取路由文档全文（带 OOM 保护，LLM 轨公式）
        max_w = _llm_safe_concurrency(3)
        bodies = self.api.batch_get_docs(master_bid, doc_ids, max_workers=max_w)

        refs = []
        seen_sub = set()
        for doc_id, body in bodies.items():
            if not body:
                continue
            body_text = body.get("body") or body.get("body_draft") or "" if isinstance(body, dict) else str(body)
            # OOM 保护：单篇路由文档上限 100KB
            if len(body_text) > 102400:
                body_text = body_text[:102400]
            try:
                data = parse_master_body(body_text)
            except Exception:
                continue

            if not data or "sub_docs" not in data:
                continue

            keyword = data.get("keyword", "")
            for sd in data["sub_docs"]:
                sub_doc_id = sd.get("doc_id")
                if not sub_doc_id or sub_doc_id in seen_sub:
                    continue
                seen_sub.add(sub_doc_id)
                refs.append({
                    "keyword": keyword,
                    "sub_doc_id": sub_doc_id,
                    "sub_book_id": sd.get("book_id"),
                    "sub_namespace": sd.get("namespace", ""),
                    "sub_title": sd.get("title", ""),
                })

        return refs

    # ── 搜索索引子库 ────────────────────────────────

    def _get_sub_namespaces(self):
        """动态发现子库 namespace 列表（从总库路由文档），带缓存"""
        if self._sub_books_cache is None:
            self._sub_books_cache = self.api.discover_sub_index_books()
        return [b["namespace"] for b in self._sub_books_cache if b.get("namespace")]

    def _get_namespace_book_map(self):
        """动态发现 namespace → book_id 映射表，带缓存"""
        if self._sub_books_cache is None:
            self._sub_books_cache = self.api.discover_sub_index_books()
        return {b["namespace"]: b["book_id"] for b in self._sub_books_cache if b.get("namespace") and b.get("book_id")}

    def search_sub_books(self, keywords, namespaces=None):
        """
        搜索索引子库，返回命中索引文档列表。

        Returns:
            list[dict]: [{id, title, summary, namespace}, ...]
        """
        if namespaces is None:
            namespaces = self._get_sub_namespaces()
        if not namespaces:
            return []

        all_hits = {}
        for ns in namespaces:
            results = self.api.batch_search(keywords, scope=ns)
            for query, result in results.items():
                docs = result.get("docs", []) if isinstance(result, dict) else []
                for doc in docs:
                    doc_id = doc.get("id")
                    if doc_id and doc_id not in all_hits:
                        all_hits[doc_id] = {
                            "id": doc_id,
                            "title": doc.get("title", ""),
                            "summary": doc.get("summary", ""),
                            "namespace": ns,
                        }
        return list(all_hits.values())

    def search_and_parse_sub(self, keywords, namespaces=None):
        """
        搜索子库 → 并发读命中索引文档全文 → 解析 source_entries。

        Returns:
            list[dict]: 去重后的源文档引用（按 doc_id）
        """
        hits = self.search_sub_books(keywords, namespaces)
        if not hits:
            return []

        # 按 book_id 分组，准备并发读取
        ns_to_book = self._get_namespace_book_map()
        groups = {}
        for hit in hits:
            namespace = hit.get("namespace")
            book_id = ns_to_book.get(namespace) if namespace else None
            if not book_id:
                # 尝试从 index_books[0]（总库）兜底
                book_id = self.api.index_book_ids[0] if self.api.index_book_ids else None
            if not book_id:
                continue
            if book_id not in groups:
                groups[book_id] = {"doc_ids": [], "namespace": hit.get("namespace", "")}
            groups[book_id]["doc_ids"].append(hit["id"])

        # 并发读取（带 OOM 保护，LLM 轨公式）
        max_w = _llm_safe_concurrency(3)
        all_entries = []
        for book_id, group in groups.items():
            try:
                bodies = self.api.batch_get_docs(book_id, group["doc_ids"], max_workers=max_w)
            except Exception:
                continue
            for doc_id, body in bodies.items():
                if not body:
                    continue
                body_text = body.get("body") or body.get("body_draft") or "" if isinstance(body, dict) else str(body)
                # OOM 保护：单篇索引文档上限 200KB
                if len(body_text) > 204800:
                    body_text = body_text[:204800]
                try:
                    entries = parse_sub_index_body(body_text)
                except Exception:
                    continue
                # 补全缺失字段
                for e in entries:
                    if "book_id" not in e:
                        e.setdefault("book_id", book_id)
                    if "namespace" not in e:
                        e.setdefault("namespace", group.get("namespace", ""))
                all_entries.extend(entries)

        # 按 doc_id 去重
        seen = set()
        result = []
        for e in all_entries:
            did = e.get("doc_id")
            if did and did not in seen:
                seen.add(did)
                result.append(e)
        return result

    # ── 组合搜索（总库路由 + 子库直搜，双路并行） ────

    def combined_search(self, keywords, max_results=20):
        """
        双路并行搜索：总库路由 + 子库直搜，合并去重。

        search_master() 和 search_and_parse_sub() 并发执行，减少串行等待。
        每次调用自动重置 LLM 轨超时降级状态。

        Returns:
            dict: {
                "from_master_routes": [...],  # 总库命中的子库引用
                "from_sub": [...],             # 子库直搜结果
                "all_unique": [...],           # 去重合并（按 doc_id）
            }
        """
        self._llm_tracker.reset()

        # 双路并行：总库路由 + 子库直搜
        with ThreadPoolExecutor(max_workers=2) as executor:
            master_future = executor.submit(self.search_master, keywords)
            sub_future = executor.submit(self.search_and_parse_sub, keywords)
            master_routes = master_future.result()
            sub_entries = sub_future.result()

        seen = set()
        all_unique = []

        # 1) 总库路由 → 按 book_id 分组并发读子库索引文档 → 解析 source_entries
        route_groups = {}
        for route in master_routes:
            sub_book_id = route.get("sub_book_id")
            sub_doc_id = route.get("sub_doc_id")
            if not sub_book_id or not sub_doc_id:
                continue
            if sub_book_id not in route_groups:
                route_groups[sub_book_id] = []
            route_groups[sub_book_id].append(sub_doc_id)

        if route_groups:
            max_w = _llm_safe_concurrency(3)
            for book_id, doc_ids in route_groups.items():
                try:
                    bodies = self.api.batch_get_docs(book_id, doc_ids, max_workers=max_w)
                except Exception:
                    continue
                for doc_id, body in bodies.items():
                    if not body:
                        continue
                    body_text = body.get("body") or body.get("body_draft") or "" if isinstance(body, dict) else str(body)
                    if len(body_text) > 204800:
                        body_text = body_text[:204800]
                    try:
                        entries = parse_sub_index_body(body_text)
                    except Exception:
                        continue
                    for e in entries:
                        did = e.get("doc_id")
                        if did and did not in seen:
                            seen.add(did)
                            all_unique.append({
                                "doc_id": did,
                                "title": e.get("title", ""),
                                "namespace": e.get("namespace", ""),
                                "book_id": e.get("book_id"),
                                "source": "master_route",
                            })

        # 2) 子库直搜 → 补充未覆盖的
        for e in sub_entries:
            did = e.get("doc_id")
            if did and did not in seen:
                seen.add(did)
                all_unique.append({
                    "doc_id": did,
                    "title": e.get("title", ""),
                    "namespace": e.get("namespace", ""),
                    "book_id": e.get("book_id"),
                    "source": "sub_index",
                })

        return {
            "from_master_routes": master_routes,
            "from_sub": sub_entries,
            "all_unique": all_unique[:max_results],
        }

    # ── 读取源文档（跨知识库） ─────────────────────

    def read_source_docs_across_books(self, refs, max_workers=None):
        """
        跨知识库读取源文档全文。

        Args:
            refs: 源文档引用列表，每项至少含 doc_id + namespace 或 book_id
            max_workers: 并发数（None=LLM 轨公式 + 超时降级）

        Returns:
            list[dict]: [{doc_id, title, body, book_id, namespace}, ...]
        """
        # 按 book_id 分组
        groups = {}
        for ref in refs:
            doc_id = ref.get("doc_id")
            book_id = ref.get("book_id")
            namespace = ref.get("namespace")
            if not doc_id:
                continue

            key = str(book_id) if book_id else (namespace or "")
            if not key:
                continue
            if key not in groups:
                groups[key] = {"book_id": book_id, "namespace": namespace, "doc_ids": []}
            groups[key]["doc_ids"].append(doc_id)

        results = []
        ns_cache = {}  # namespace → book_id 缓存，避免重复 API 调用
        for key, group in groups.items():
            book_id = group["book_id"]
            namespace = group["namespace"]

            if not book_id and namespace:
                if namespace not in ns_cache:
                    try:
                        ns_cache[namespace] = self.api.resolve_book_id(namespace)
                    except Exception:
                        ns_cache[namespace] = None
                book_id = ns_cache[namespace]
            if not book_id:
                continue

            # LLM 轨超时降级：暂停时跳过本轮
            if self._llm_tracker.paused:
                continue

            workers = max_workers if max_workers is not None else self._llm_tracker.workers
            if workers <= 0:
                continue

            t0 = time.time()
            bodies = self.api.batch_get_docs(book_id, group["doc_ids"], max_workers=workers)
            elapsed = time.time() - t0
            self._llm_tracker.record_batch(elapsed, len(group["doc_ids"]))
            for doc_id, body in bodies.items():
                if isinstance(body, dict):
                    results.append({
                        "doc_id": doc_id,
                        "title": body.get("title", ""),
                        "body": body.get("body") or body.get("body_draft") or "",
                        "book_id": book_id,
                        "namespace": namespace,
                    })
                elif isinstance(body, str):
                    results.append({
                        "doc_id": doc_id,
                        "title": "",
                        "body": body,
                        "book_id": book_id,
                        "namespace": namespace,
                    })

        return results

    def read_single_source_doc(self, doc_id, book_id=None, namespace=None):
        """读取单篇源文档"""
        if not book_id and namespace:
            try:
                book_id = self.api.resolve_book_id(namespace)
            except Exception:
                return None
        if not book_id:
            return None
        try:
            body = self.api.get_doc_body(book_id, doc_id)
            return {"doc_id": doc_id, "title": "", "body": body, "book_id": book_id}
        except Exception:
            return None

    # ── 降级搜索（全库，无 scope） ─────────────────

    def degraded_search(self, keywords):
        """
        降级模式：搜全库（不限制 scope），语雀原生搜索。

        Args:
            keywords: 搜索词列表

        Returns:
            list[dict]: [{id, title, summary, url, doc_id, book_id, namespace}, ...]
        """
        results = self.api.batch_search(keywords)  # 不传 scope = 搜全库
        all_docs = {}
        for query, result in results.items():
            docs = result.get("docs", []) if isinstance(result, dict) else []
            for doc in docs:
                doc_id = doc.get("id")
                if doc_id and doc_id not in all_docs:
                    target = doc.get("target", {})
                    book = target.get("book", {})
                    all_docs[doc_id] = {
                        "id": doc_id,
                        "title": doc.get("title", ""),
                        "summary": doc.get("summary", ""),
                        "url": doc.get("url", ""),
                        "doc_id": target.get("id") or doc_id,
                        "book_id": target.get("book_id") or book.get("id"),
                        "namespace": book.get("namespace", ""),
                        "target": target,
                    }
        return list(all_docs.values())

    # ── 直接文档短路 ────────────────────────────────

    def direct_doc_search(self, doc_title):
        """用户指定文档名时直接搜索全库"""
        result = self.api.search(doc_title)  # 无 scope
        return result.get("docs", []) if isinstance(result, dict) else []

    # ── LLM 上下文生成 ─────────────────────────────

    def get_context_for_llm(self, refs, max_chars=8000):
        """
        读取源文档并格式化为 LLM 上下文。

        Returns:
            str: 格式化的上下文，含来源标注
        """
        docs = self.read_source_docs_across_books(refs)
        if not docs:
            return ""

        parts = []
        total = 0
        for doc in docs:
            body = doc.get("body", "")
            if not body:
                continue
            header = f"【来源: {doc.get('title', '无标题')}】(doc_id={doc['doc_id']})"
            chunk = f"{header}\n{body[:1500]}\n"
            if total + len(chunk) > max_chars:
                remaining = max_chars - total - 200
                if remaining > 200:
                    chunk = f"{header}\n{body[:remaining]}...\n"
                else:
                    break
            parts.append(chunk)
            total += len(chunk)
        return "\n---\n".join(parts)


# ── CLI 测试入口 ────────────────────────────────────

if __name__ == "__main__":
    sp = SearchPipeline()

    if len(sys.argv) < 2:
        print("用法:")
        print("  python yuque_search.py master <kw1> [kw2 ...]    # 路由搜索")
        print("  python yuque_search.py sub <kw1> [kw2 ...]       # 子库搜索+解析")
        print("  python yuque_search.py combined <kw1> [kw2 ...]  # 组合搜索")
        print("  python yuque_search.py degraded <kw1> [kw2 ...]  # 降级全库搜索")
        print("  python yuque_search.py read <doc_id> <book_id>    # 读源文档")
        print("  python yuque_search.py parse <file>               # 解析索引文档")
        sys.exit(0)

    cmd = sys.argv[1]

    try:
        if cmd == "master":
            keywords = sys.argv[2:]
            print(f"搜索索引总库: {keywords}")
            refs = sp.search_master(keywords)
            for r in refs:
                print(f"  keyword={r['keyword']} → sub: [{r['sub_doc_id']}] {r['sub_title']} (bid={r['sub_book_id']})")
            print(f"共 {len(refs)} 条路由")

        elif cmd == "sub":
            keywords = sys.argv[2:]
            print(f"搜索+解析索引子库: {keywords}")
            entries = sp.search_and_parse_sub(keywords)
            for e in entries:
                print(f"  [{e['doc_id']}] {e['title']}")
                print(f"    book_id={e.get('book_id')}, ns={e.get('namespace')}")
            print(f"共 {len(entries)} 条")

        elif cmd == "combined":
            keywords = sys.argv[2:]
            print(f"组合搜索: {keywords}")
            result = sp.combined_search(keywords)
            print(f"总库路由: {len(result['from_master_routes'])} 条")
            print(f"子库直搜: {len(result['from_sub'])} 条")
            print(f"去重合并: {len(result['all_unique'])} 条")
            for r in result["all_unique"][:5]:
                print(f"  [{r['doc_id']}] {r['title']} ({r['source']})")

        elif cmd == "degraded":
            keywords = sys.argv[2:]
            print(f"降级全库搜索: {keywords}")
            results = sp.degraded_search(keywords)
            for r in results[:10]:
                print(f"  [{r['id']}] {r['title']}")
            print(f"共 {len(results)} 命中")

        elif cmd == "read":
            doc_id = int(sys.argv[2])
            book_id = int(sys.argv[3]) if len(sys.argv) > 3 else None
            namespace = sys.argv[4] if len(sys.argv) > 4 else None
            doc = sp.read_single_source_doc(doc_id, book_id, namespace)
            if doc:
                print(f"标题: {doc.get('title', '无')}")
                print(doc["body"][:1000])
            else:
                print("读取失败")

        elif cmd == "parse":
            filepath = sys.argv[2]
            with open(filepath, "r", encoding="utf-8") as f:
                body = f.read()
            entries = parse_sub_index_body(body)
            print(f"解析到 {len(entries)} 个源文档引用")
            for e in entries[:5]:
                print(f"  [{e.get('doc_id')}] {e.get('title')}")
                print(f"    book_id={e.get('book_id')}, ns={e.get('namespace')}")

        else:
            print(f"未知命令: {cmd}")

    except Exception as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
