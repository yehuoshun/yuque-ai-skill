#!/usr/bin/env python3
"""
语雀知识库搜索管线 —— 基于实际数据模型的搜索/问答基础设施。

实际数据模型（与 SKILL.md 设计有差异）：
  - 索引总库 (wwqac0): JSON 条目，一个关键词 → 一个源文档（含 URL）
  - 索引子库 (rqgc16): Markdown 索引，一个关键词 → 多个源文档（含 book_id + namespace）
  - 源文档分散在多个知识库中（不在 default_book）
  - 内容库 (index-sub-1): 当前为空（仅测试文档）

用法：
    from yuque_search import SearchPipeline
    sp = SearchPipeline()

    # 搜索总库 → 拿源文档引用
    refs = sp.search_master(["Python 协程", "Python async"])

    # 搜索子库 → 拿索引文档 → 解析来源
    entries = sp.search_and_parse("Python 协程")

    # 读取源文档全文（跨知识库）
    docs = sp.read_source_docs_across_books(refs)
"""

import json
import os
import re
import sys
import urllib.parse
from yuque_api import YuqueAPI


class SearchPipeline:
    """
    语雀搜索管线。

    数据模型适配：
    - 索引总库: JSON 格式条目，解析 source_doc.url → namespace
    - 索引子库: Markdown 格式，解析 ## 文档索引 → 源文档引用
    - 跨知识库源文档读取：自动从不同知识库读取原文
    """

    def __init__(self, api=None, config_path=None):
        self.api = api or YuqueAPI(config_path)

    # ── 搜索索引总库（JSON 条目） ──────────────────

    def search_master(self, keywords):
        """
        搜索索引总库，返回匹配的源文档引用。

        Args:
            keywords: 搜索词列表，如 ["Python 协程", "Python asyncio"]

        Returns:
            list[dict]: 源文档引用 [{keyword, doc_id, title, url, namespace, synonyms}, ...]
        """
        master_ns = self.api.index_master_namespace
        master_bid = self.api.index_master_book_id
        if not master_ns or not master_bid:
            return []

        results = self.api.batch_search(keywords, scope=master_ns)
        refs = []
        seen = set()

        for query, result in results.items():
            docs = result.get("docs", []) if isinstance(result, dict) else []
            for doc in docs:
                doc_id = doc.get("id")
                if not doc_id or doc_id in seen:
                    continue
                seen.add(doc_id)

                try:
                    body = self.api.get_doc_body(master_bid, doc_id)
                    entry = json.loads(body) if body else {}
                except (json.JSONDecodeError, Exception):
                    continue

                if entry.get("type") != "index_entry":
                    continue

                source = entry.get("source_doc", {})
                url = source.get("url", "")
                namespace = self._url_to_namespace(url)

                refs.append({
                    "keyword": entry.get("keyword", ""),
                    "synonyms": entry.get("synonyms", []),
                    "doc_id": source.get("id"),
                    "title": source.get("title", ""),
                    "url": url,
                    "namespace": namespace,
                })

        return refs

    @staticmethod
    def _url_to_namespace(url):
        """从语雀 URL 提取 namespace（group/slug）"""
        if not url:
            return ""
        # https://www.yuque.com/yehuoshun/sk6rfn/kzcgcvnrn5o8884k
        # → yehuoshun/sk6rfn
        parsed = urllib.parse.urlparse(url)
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
        return ""

    # ── 搜索索引子库（Markdown 条目） ──────────────

    def search_sub_books(self, keywords, namespaces=None):
        """
        搜索索引子库，返回命中索引文档列表。

        Args:
            keywords: 搜索词列表
            namespaces: 子库 namespace 列表（默认用配置）

        Returns:
            list[dict]: [{"id": ..., "title": ..., "namespace": ...}, ...]
        """
        if namespaces is None:
            namespaces = self.api.index_namespaces
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

    def parse_sub_index_doc(self, body):
        """
        解析索引子库文档（Markdown 格式），提取源文档引用。

        子库文档格式：
            # [索引] 关键词 (N)
            ## 文档索引
            ### 文档标题
            - **源文档ID**: 222199176
            - **源知识库ID**: 65584944
            - **Namespace**: yehuoshun/sk6rfn

        Returns:
            list[dict]: 源文档引用 [{doc_id, book_id, title, namespace, keywords}, ...]
        """
        entries = []

        # 按 ### 标题分割（每个标题是一个源文档）
        blocks = re.split(r'\n(?=### )', body)

        for block in blocks:
            # 提取文档标题
            title_match = re.match(r'### (.+)', block)
            if not title_match:
                continue
            doc_title = title_match.group(1).strip()

            entry = {"title": doc_title}

            patterns = {
                "doc_id": r'源文档ID\**[：:]\s*(\d+)',
                "book_id": r'源知识库ID\**[：:]\s*(\d+)',
                "namespace": r'Namespace\**[：:]\s*(\S+)',
                "keywords": r'关键词\**[：:]\s*(.+)',
                "slug": r'Slug\**[：:]\s*(\S+)',
                "doc_type": r'类型\**[：:]\s*(.+)',
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

    def search_and_parse_sub(self, keywords, namespaces=None):
        """
        搜索子库 → 读命中索引文档 → 解析源文档引用。
        """
        hits = self.search_sub_books(keywords, namespaces)
        all_entries = []

        for hit in hits:
            book_id = None
            for ib in self.api.index_books:
                if ib.get("namespace") == hit.get("namespace"):
                    book_id = ib.get("book_id")
                    break
            if not book_id:
                continue

            try:
                body = self.api.get_doc_body(book_id, hit["id"])
                entries = self.parse_sub_index_doc(body)
                # 补上缺的 book_id（如果子库文档没写的话）
                for e in entries:
                    if "book_id" not in e:
                        e["book_id"] = book_id
                    if "namespace" not in e:
                        e["namespace"] = hit.get("namespace", "")
                all_entries.extend(entries)
            except Exception:
                continue

        # 按 doc_id 去重
        seen = set()
        result = []
        for e in all_entries:
            if e.get("doc_id") not in seen:
                seen.add(e["doc_id"])
                result.append(e)
        return result

    # ── 组合搜索（总库 + 子库） ────────────────────

    def combined_search(self, keywords, max_results=20):
        """
        组合搜索：同时搜索引总库和子库，合并去重。

        Returns:
            dict: {
                "from_master": [...],   # 来自索引总库的源文档引用
                "from_sub": [...],      # 来自索引子库的源文档引用
                "all_unique": [...],    # 去重合并（按 doc_id）
            }
        """
        master_refs = self.search_master(keywords)
        sub_entries = self.search_and_parse_sub(keywords)

        # 合并，总库优先
        seen = set()
        all_unique = []
        for r in master_refs:
            did = r.get("doc_id")
            if did and did not in seen:
                seen.add(did)
                all_unique.append({
                    "doc_id": did,
                    "title": r.get("title", ""),
                    "namespace": r.get("namespace", ""),
                    "url": r.get("url", ""),
                    "source": "master",
                    "keyword": r.get("keyword", ""),
                    "synonyms": r.get("synonyms", []),
                })
        for e in sub_entries:
            did = e.get("doc_id")
            if did and did not in seen:
                seen.add(did)
                all_unique.append({
                    "doc_id": did,
                    "title": e.get("title", ""),
                    "namespace": e.get("namespace", ""),
                    "book_id": e.get("book_id"),
                    "source": "sub",
                    "keywords": e.get("keywords", ""),
                })

        return {
            "from_master": master_refs,
            "from_sub": sub_entries,
            "all_unique": all_unique[:max_results],
        }

    # ── 读取源文档（跨知识库） ─────────────────────

    def read_source_docs_across_books(self, refs):
        """
        跨知识库读取源文档全文。

        Args:
            refs: 源文档引用列表，每项至少含 doc_id + namespace 或 book_id

        Returns:
            list[dict]: [{doc_id, title, body, namespace}, ...]
        """
        # 按 book_id 或 namespace 分组
        groups = {}  # key → [doc_ids]

        for ref in refs:
            book_id = ref.get("book_id")
            namespace = ref.get("namespace")
            doc_id = ref.get("doc_id")
            if not doc_id:
                continue

            if book_id:
                key = str(book_id)
            elif namespace:
                # 查 book_id from namespace
                key = namespace
            else:
                continue

            if key not in groups:
                groups[key] = {"book_id": book_id, "namespace": namespace, "doc_ids": []}
            groups[key]["doc_ids"].append(doc_id)

        results = []
        for key, group in groups.items():
            book_id = group["book_id"]
            namespace = group["namespace"]

            if not book_id and namespace:
                try:
                    book_id = self.api.resolve_book_id(namespace)
                except Exception:
                    continue
            if not book_id:
                continue

            # 批量读取
            bodies = self.api.batch_get_docs(book_id, group["doc_ids"])
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
        """
        读取单篇源文档。

        Args:
            doc_id: 文档 ID
            book_id: 知识库 ID（优先）
            namespace: 知识库 namespace（book_id 为空时自动解析）

        Returns:
            dict: {doc_id, title, body} 或 None
        """
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

    # ── 降级搜索（直搜内容库） ─────────────────────

    def degraded_search(self, keywords, scope=None):
        """
        降级模式：直接搜索内容库（不经过索引）。

        Args:
            keywords: 搜索词列表
            scope: 搜索范围（默认用 default_book.namespace）

        Returns:
            list[dict]: 搜索结果文档 [{id, title, summary}, ...]
        """
        scope = scope or self.api.default_namespace
        if not scope:
            return []

        results = self.api.batch_search(keywords, scope=scope)
        all_docs = {}
        for query, result in results.items():
            docs = result.get("docs", []) if isinstance(result, dict) else []
            for doc in docs:
                doc_id = doc.get("id")
                if doc_id and doc_id not in all_docs:
                    all_docs[doc_id] = {
                        "id": doc_id,
                        "title": doc.get("title", ""),
                        "summary": doc.get("summary", ""),
                        "url": doc.get("url", ""),
                    }
        return list(all_docs.values())

    def read_degraded_docs(self, search_results, book_id=None):
        """
        降级模式：读取搜索结果的全文。

        Args:
            search_results: degraded_search 返回的结果列表
            book_id: 知识库 ID（默认用 default_book）

        Returns:
            list[dict]: [{doc_id, title, body}, ...]
        """
        if book_id is None:
            book_id = self.api.default_book_id
        if not book_id:
            return []

        doc_ids = [d["id"] for d in search_results if d.get("id")]
        if not doc_ids:
            return []

        bodies = self.api.batch_get_docs(book_id, doc_ids)
        results = []
        for doc_id, body in bodies.items():
            if isinstance(body, dict):
                results.append({
                    "doc_id": doc_id,
                    "title": body.get("title", ""),
                    "body": body.get("body") or body.get("body_draft") or "",
                })
            else:
                results.append({
                    "doc_id": doc_id,
                    "title": "",
                    "body": str(body) if body else "",
                })
        return results

    # ── 直接文档短路 ────────────────────────────────

    def direct_doc_search(self, doc_title, scope=None):
        """用户指定文档名时直接搜索"""
        scope = scope or self.api.default_namespace
        result = self.api.search(doc_title, scope=scope)
        return result.get("docs", []) if isinstance(result, dict) else []

    # ── LLM 辅助函数 ──────────────────────────────

    def get_context_for_llm(self, refs, max_chars=8000):
        """
        读取源文档并格式化为 LLM 上下文。

        Args:
            refs: combined_search 返回的 all_unique 或类似结构
            max_chars: 最大字符数

        Returns:
            str: 格式化的 LLM 上下文，含来源引用
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
        print("  python yuque_search.py master <kw1> [kw2 ...]")
        print("  python yuque_search.py sub <kw1> [kw2 ...]")
        print("  python yuque_search.py combined <kw1> [kw2 ...]")
        print("  python yuque_search.py read <doc_id> <book_id>")
        print("  python yuque_search.py degraded <kw> [scope]")
        print("  python yuque_search.py parse <doc_body_file>")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "master":
        keywords = sys.argv[2:]
        print(f"搜索索引总库: {keywords}")
        refs = sp.search_master(keywords)
        for r in refs:
            print(f"  [{r['doc_id']}] {r['title']}")
            print(f"    namespace={r['namespace']}, keyword={r['keyword']}")
        print(f"共 {len(refs)} 条")

    elif cmd == "sub":
        keywords = sys.argv[2:]
        print(f"搜索+解析索引子库: {keywords}")
        entries = sp.search_and_parse_sub(keywords)
        for e in entries:
            print(f"  [{e['doc_id']}] {e['title']}")
            print(f"    book_id={e.get('book_id')}, namespace={e.get('namespace')}")
        print(f"共 {len(entries)} 条")

    elif cmd == "combined":
        keywords = sys.argv[2:]
        print(f"组合搜索: {keywords}")
        result = sp.combined_search(keywords)
        print(f"总库: {len(result['from_master'])} 条")
        print(f"子库: {len(result['from_sub'])} 条")
        print(f"去重合并: {len(result['all_unique'])} 条")
        for r in result["all_unique"][:5]:
            print(f"  [{r['doc_id']}] {r['title']} ({r['source']})")

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

    elif cmd == "degraded":
        query = sys.argv[2]
        scope = sys.argv[3] if len(sys.argv) > 3 else None
        print(f"降级搜索: '{query}' in {scope or 'default'}")
        results = sp.degraded_search([query], scope)
        for r in results:
            print(f"  [{r['id']}] {r['title']}")
        print(f"共 {len(results)} 命中")

    elif cmd == "parse":
        filepath = sys.argv[2]
        with open(filepath, "r", encoding="utf-8") as f:
            body = f.read()
        entries = sp.parse_sub_index_doc(body)
        print(f"解析到 {len(entries)} 个源文档引用")
        for e in entries[:5]:
            print(f"  [{e.get('doc_id')}] {e.get('title')}")
            print(f"    book_id={e.get('book_id')}, namespace={e.get('namespace')}")

    else:
        print(f"未知命令: {cmd}")
