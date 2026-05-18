#!/usr/bin/env python3
"""
语雀索引构建器 —— 全量/增量索引构建。

流程：
  1. 列出源知识库全部文档
  2. 读取每篇文档（LLM 提取关键词 + 内容段由 Agent 完成）
  3. 写入索引子库（JSON 格式，按关键词归类）
  4. 写入索引总库（JSON 格式，关键词→子库路由）

用法：
    from yuque_index import IndexBuilder
    ib = IndexBuilder()

    docs = ib.list_source_docs(book_id)
    doc = ib.read_doc_for_indexing(book_id, doc_id)
    ib.write_master_entry("Python", [{"title": "...", "doc_id": 1, "book_id": 2, "namespace": "..."}])
    ib.rebuild_sub_index_doc("Python", [{"doc_id": 1, "title": "...", ...}])

    # 增量构建（需调用方提供 since 时间戳）
    new_docs = ib.get_changed_docs(book_id, since="2026-05-01T00:00:00+08:00")
"""

import json
import sys
from datetime import datetime, timezone, timedelta
from yuque_api import YuqueAPI

TZ_SHANGHAI = timezone(timedelta(hours=8))


class IndexBuilder:
    """
    索引构建器。

    - 全量构建：遍历源知识库所有文档
    - 增量构建：筛选 updated_at > since 的变更文档（since 由调用方管理）
    - 总库写入：路由文档（keyword + sub_docs）
    - 子库写入：索引文档（keyword + source_entries）
    """

    def __init__(self, api=None, config_path=None):
        self.api = api or YuqueAPI(config_path)

    # ── 源文档读取 ────────────────────────────────

    def list_source_docs(self, book_id):
        """列出源知识库全部文档"""
        return self.api.list_all_docs(book_id)

    def get_changed_docs(self, book_id, since):
        """
        获取 since 之后有变更的文档。

        Args:
            book_id: 源知识库 ID
            since: ISO 时间戳（必填），如 "2026-05-01T00:00:00+08:00"

        Returns:
            list[dict]: 变更文档列表
        """
        all_docs = self.list_source_docs(book_id)
        since_dt = datetime.fromisoformat(since)
        changed = []
        for doc in all_docs:
            updated = doc.get("updated_at", "")
            if updated:
                try:
                    if datetime.fromisoformat(updated) > since_dt:
                        changed.append(doc)
                except (ValueError, TypeError):
                    changed.append(doc)
            else:
                # 无 updated_at 字段，跳过（无法判断是否变更）
                pass
        return changed

    def read_doc_for_indexing(self, book_id, doc_id):
        """
        读取文档内容供 LLM 分析。

        Returns:
            dict: {doc_id, title, body, updated_at, slug} 或 None
        """
        try:
            result = self.api.get_doc(book_id, doc_id, raw=True)
        except Exception:
            return None

        if isinstance(result, dict):
            return {
                "doc_id": doc_id,
                "title": result.get("title", ""),
                "body": result.get("body") or result.get("body_draft") or "",
                "updated_at": result.get("updated_at", ""),
                "slug": result.get("slug", ""),
            }
        return {
            "doc_id": doc_id,
            "title": "",
            "body": str(result) if result else "",
            "updated_at": "",
            "slug": "",
        }

    # ── 写入索引总库 ──────────────────────────────

    def write_master_entry(self, keyword, sub_docs):
        """
        在索引总库中创建路由文档。

        格式: {"keyword": "Python", "sub_docs": [{"title":"...", "doc_id":1, "book_id":2, "namespace":"..."}]}

        Returns:
            int: 创建的文档 ID
        """
        master_bid = self.api.index_master_book_id
        if not master_bid:
            raise ValueError("未配置 index_books（索引总库）")

        body = json.dumps({"keyword": keyword, "sub_docs": sub_docs}, ensure_ascii=False, indent=2)
        title = f"[索引] {keyword}"

        doc = self.api.create_doc(master_bid, title, body)
        doc_id = doc["id"]
        try:
            self.api.append_to_toc(master_bid, doc_id)
        except Exception:
            pass
        return doc_id

    # ── 写入索引子库 ──────────────────────────────

    def find_or_create_sub_index_doc(self, keyword):
        """查找或创建关键词对应的索引子库文档"""
        sub_book_id = None
        sub_ns = None
        for ib in self.api.index_books:
            if ib.get("book_id"):
                sub_book_id = ib["book_id"]
                sub_ns = ib.get("namespace")
                break
        if not sub_book_id:
            raise ValueError("未配置 index_books")

        result = self.api.search(f"[索引] {keyword}", scope=sub_ns)
        docs = result.get("docs", []) if isinstance(result, dict) else []
        for d in docs:
            if keyword in d.get("title", ""):
                return {"doc_id": d["id"], "is_new": False}

        body = json.dumps({"keyword": keyword, "source_entries": []}, ensure_ascii=False, indent=2)
        doc = self.api.create_doc(sub_book_id, f"[索引] {keyword}", body)
        doc_id = doc["id"]
        try:
            self.api.append_to_toc(sub_book_id, doc_id)
        except Exception:
            pass
        return {"doc_id": doc_id, "is_new": True}

    def append_sub_index_entries(self, sub_doc_id, entries):
        """向索引子库文档追加源文档条目（去重合并）"""
        sub_book_id = None
        for ib in self.api.index_books:
            if ib.get("book_id"):
                sub_book_id = ib["book_id"]
                break
        if not sub_book_id:
            raise ValueError("未配置 index_books")

        from yuque_search import parse_sub_index_body
        current = self.api.get_doc_body(sub_book_id, sub_doc_id)
        existing = parse_sub_index_body(current)

        seen_ids = {e.get("doc_id") for e in existing if e.get("doc_id")}
        new_entries = [e for e in entries if e.get("doc_id") not in seen_ids]
        all_entries = existing + new_entries

        keyword = ""
        try:
            keyword = json.loads(current.strip()).get("keyword", "")
        except Exception:
            pass

        body = json.dumps({"keyword": keyword, "source_entries": all_entries}, ensure_ascii=False, indent=2)
        self.api.update_doc(sub_book_id, sub_doc_id, title=f"[索引] {keyword} ({len(all_entries)})", body=body)

    def rebuild_sub_index_doc(self, keyword, entries):
        """
        重建索引子库文档（全量替换）。

        JSON 格式: {"keyword": "Python", "source_entries": [{"doc_id":1, "book_id":2, "title":"...", ...}]}
        """
        sub_book_id = None
        for ib in self.api.index_books:
            if ib.get("book_id"):
                sub_book_id = ib["book_id"]
                break
        if not sub_book_id:
            raise ValueError("未配置 index_books")

        count = len(entries)
        body = json.dumps({"keyword": keyword, "source_entries": entries}, ensure_ascii=False, indent=2)

        ns = self.api.index_namespaces[0] if self.api.index_namespaces else None
        result = self.api.search(f"[索引] {keyword}", scope=ns) if ns else {"docs": []}
        docs = result.get("docs", []) if isinstance(result, dict) else []
        existing = None
        for d in docs:
            if d.get("title", "").startswith(f"[索引] {keyword}"):
                existing = d
                break

        title = f"[索引] {keyword} ({count})"
        if existing:
            self.api.update_doc(sub_book_id, existing["id"], title=title, body=body)
            return {"doc_id": existing["id"], "is_new": False}
        else:
            doc = self.api.create_doc(sub_book_id, title, body)
            doc_id = doc["id"]
            try:
                self.api.append_to_toc(sub_book_id, doc_id)
            except Exception:
                pass
            return {"doc_id": doc_id, "is_new": True}


# ── CLI ────────────────────────────────────────────

if __name__ == "__main__":
    ib = IndexBuilder()

    if len(sys.argv) < 2:
        print("用法:")
        print("  python yuque_index.py list <book_id>")
        print("  python yuque_index.py read <book_id> <doc_id>")
        print("  python yuque_index.py changed <book_id> <since>")
        print("  python yuque_index.py find-sub <keyword>")
        sys.exit(0)

    cmd = sys.argv[1]

    try:
        if cmd == "list":
            book_id = int(sys.argv[2])
            docs = ib.list_source_docs(book_id)
            print(f"共 {len(docs)} 篇文档")
            for d in docs[:10]:
                print(f"  [{d['id']}] {d['title']}")

        elif cmd == "read":
            book_id = int(sys.argv[2])
            doc_id = int(sys.argv[3])
            doc = ib.read_doc_for_indexing(book_id, doc_id)
            if doc:
                print(f"标题: {doc['title']}")
                print(f"正文长度: {len(doc['body'])}")
                print(doc['body'][:500])
            else:
                print("读取失败")

        elif cmd == "changed":
            book_id = int(sys.argv[2])
            since = sys.argv[3]
            docs = ib.get_changed_docs(book_id, since)
            print(f"变更文档: {len(docs)} 篇")
            for d in docs[:10]:
                print(f"  [{d['id']}] {d['title']} (updated={d.get('updated_at','?')})")

        elif cmd == "find-sub":
            keyword = sys.argv[2]
            result = ib.find_or_create_sub_index_doc(keyword)
            print(json.dumps(result, ensure_ascii=False))

        else:
            print(f"未知命令: {cmd}")

    except Exception as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
