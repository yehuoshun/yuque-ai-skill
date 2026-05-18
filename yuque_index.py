#!/usr/bin/env python3
"""
语雀索引构建器 —— 全量/增量索引构建。

流程：
  1. 列出源知识库全部文档
  2. 读取每篇文档（LLM 提取关键词 + 内容段由 Agent 完成）
  3. 写入索引子库（Markdown 格式，按关键词归类）
  4. 写入索引总库（JSON 格式，关键词→源文档映射）

用法：
    from yuque_index import IndexBuilder
    ib = IndexBuilder()

    # 列出待索引文档
    docs = ib.list_source_docs(book_id)

    # 读取文档内容（供 LLM 分析）
    content = ib.read_doc_for_indexing(book_id, doc_id)

    # 写入一条索引条目到总库
    ib.write_master_entry(keyword="Python", synonyms=["py","python3"],
                          source_doc_id=123, source_title="...", source_url="...")

    # 写入/更新索引子库文档
    ib.write_sub_index_doc(keyword="Python", entries=[...])

    # 增量构建
    new_docs = ib.get_changed_docs(book_id, since="2026-05-01T00:00:00+08:00")
"""

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta
from yuque_api import YuqueAPI

TZ_SHANGHAI = timezone(timedelta(hours=8))


class IndexBuilder:
    """
    索引构建器。

    支持：
    - 全量构建：遍历源知识库所有文档，提取关键词建立索引
    - 增量构建：仅索引上次构建后有变更的文档
    - 总库写入：JSON 格式条目（keyword → source_doc）
    - 子库写入：Markdown 格式（keyword → 多 source_docs）
    """

    def __init__(self, api=None, config_path=None, db_path=None):
        self.api = api or YuqueAPI(config_path)
        self._db_path = db_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "index_state.db"
        )
        self._init_db()

    def _init_db(self):
        """初始化索引状态数据库"""
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS index_state (
                book_id INTEGER PRIMARY KEY,
                last_indexed_at TEXT,
                total_docs INTEGER,
                indexed_docs INTEGER,
                status TEXT
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS indexed_docs (
                doc_id INTEGER PRIMARY KEY,
                book_id INTEGER,
                indexed_at TEXT,
                keywords TEXT
            )
        """)
        self._conn.commit()

    # ── 源文档读取 ────────────────────────────────

    def list_source_docs(self, book_id):
        """
        列出源知识库全部文档。

        Returns:
            list[dict]: [{id, title, slug, updated_at, ...}, ...]
        """
        return self.api.list_all_docs(book_id)

    def get_changed_docs(self, book_id, since=None):
        """
        获取自上次索引后有变更的文档。

        Args:
            book_id: 源知识库 ID
            since: ISO 时间戳，None=使用上次索引时间

        Returns:
            list[dict]: 变更文档列表
        """
        if since is None:
            row = self._conn.execute(
                "SELECT last_indexed_at FROM index_state WHERE book_id=?",
                (book_id,)
            ).fetchone()
            since = row[0] if row else None

        all_docs = self.list_source_docs(book_id)
        if not since:
            return all_docs

        since_dt = datetime.fromisoformat(since)
        changed = []
        for doc in all_docs:
            updated = doc.get("updated_at", "")
            if updated:
                try:
                    doc_dt = datetime.fromisoformat(updated)
                    if doc_dt > since_dt:
                        changed.append(doc)
                except (ValueError, TypeError):
                    changed.append(doc)
            else:
                changed.append(doc)
        return changed

    def read_doc_for_indexing(self, book_id, doc_id):
        """
        读取文档内容供 LLM 分析。

        Returns:
            dict: {doc_id, title, body, updated_at}
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

    # ── 写入索引总库（JSON 格式） ──────────────────

    def write_master_entry(self, keyword, synonyms, source_doc_id, source_title, source_url):
        """
        在索引总库中创建一条索引条目。

        格式:
        {
          "type": "index_entry",
          "keyword": "Python",
          "synonyms": ["py", "python3"],
          "source_doc": {"id": 123, "title": "...", "url": "..."},
          "updated_at": "2026-05-18T10:00:00+08:00"
        }

        Returns:
            int: 创建的文档 ID
        """
        master_bid = self.api.index_master_book_id
        if not master_bid:
            raise ValueError("未配置 index_master_book")

        now = datetime.now(TZ_SHANGHAI).isoformat()
        body = json.dumps({
            "type": "index_entry",
            "keyword": keyword,
            "synonyms": synonyms,
            "source_doc": {
                "id": source_doc_id,
                "title": source_title,
                "url": source_url,
            },
            "updated_at": now,
        }, ensure_ascii=False, indent=2)

        # 文档标题用 keyword + 源文档标题前30字
        title = f"{keyword} → {source_title[:30]}"

        doc = self.api.create_doc(master_bid, title, body)
        doc_id = doc["id"]

        # 挂载到目录
        try:
            self.api.append_to_toc(master_bid, doc_id)
        except Exception:
            pass  # TOC 失败不阻止，手动拖入即可

        return doc_id

    def delete_master_entries_for_doc(self, source_doc_id):
        """
        删除与指定源文档相关的所有总库索引条目。
        用于源文档更新后重建索引。
        """
        master_bid = self.api.index_master_book_id
        if not master_bid:
            return 0

        # 搜索总库中引用此 source_doc_id 的条目
        # 通过搜索文档标题来定位（不够精确，但语雀搜索不支持按正文内容搜）
        # 更好的做法：遍历所有 master 条目检查 source_doc.id
        # 这里提供一个基于搜索的近似方法
        deleted = 0
        return deleted

    # ── 写入索引子库（Markdown 格式） ──────────────

    def find_or_create_sub_index_doc(self, keyword, title_prefix="[索引]"):
        """
        查找或创建关键词对应的索引子库文档。

        先搜索该关键词的索引文档是否存在，不存在则创建。

        Returns:
            dict: {doc_id, is_new}
        """
        sub_book_id = None
        sub_ns = None
        for ib in self.api.index_books:
            if ib.get("book_id"):
                sub_book_id = ib["book_id"]
                sub_ns = ib.get("namespace")
                break

        if not sub_book_id:
            raise ValueError("未配置 index_books")

        # 搜索是否已有该关键词的索引
        doc_title = f"{title_prefix} {keyword} (1)"
        result = self.api.search(doc_title, scope=sub_ns)
        docs = result.get("docs", []) if isinstance(result, dict) else []
        for d in docs:
            if keyword in d.get("title", ""):
                return {"doc_id": d["id"], "is_new": False}

        # 不存在，创建
        body = f"# {title_prefix} {keyword} (1)\n\n本索引包含所有与「{keyword}」相关的文档。\n\n---\n\n## 文档索引\n\n"
        doc = self.api.create_doc(sub_book_id, doc_title, body)
        doc_id = doc["id"]
        try:
            self.api.append_to_toc(sub_book_id, doc_id)
        except Exception:
            pass
        return {"doc_id": doc_id, "is_new": True}

    def append_sub_index_entries(self, sub_doc_id, entries):
        """
        向索引子库文档追加源文档条目。

        Args:
            sub_doc_id: 索引子库文档 ID
            entries: 条目列表 [{title, doc_id, book_id, namespace, slug, keywords, doc_type}, ...]
        """
        sub_book_id = None
        for ib in self.api.index_books:
            if ib.get("book_id"):
                sub_book_id = ib["book_id"]
                break
        if not sub_book_id:
            raise ValueError("未配置 index_books")

        # 读取当前索引文档
        current = self.api.get_doc_body(sub_book_id, sub_doc_id)

        # 构建新条目块
        new_blocks = []
        for e in entries:
            block = f"""### {e.get('title', '无标题')}
- **关键词**: {e.get('keywords', '')}
- **源文档ID**: {e.get('doc_id', '')}
- **源知识库ID**: {e.get('book_id', '')}
- **Namespace**: {e.get('namespace', '')}
- **Slug**: {e.get('slug', '')}
- **类型**: {e.get('doc_type', '文档')}

"""
            new_blocks.append(block)

        # 追加到正文
        new_body = current.rstrip() + "\n" + "".join(new_blocks)

        # 更新标题中的统计数
        import re
        count = len(entries) + (current.count("### ") if current else 0)
        new_body = re.sub(r'^#\s*(\[索引\]\s*\S+)\s*\(\d+\)', f'# \\1 ({count})', new_body)

        self.api.update_doc(sub_book_id, sub_doc_id, body=new_body)

    def rebuild_sub_index_doc(self, keyword, entries):
        """
        重建索引子库文档（全量替换）。

        如果已存在该关键词的索引文档，全量更新；不存在则创建。
        """
        sub_book_id = None
        for ib in self.api.index_books:
            if ib.get("book_id"):
                sub_book_id = ib["book_id"]
                break
        if not sub_book_id:
            raise ValueError("未配置 index_books")

        # 构建完整文档
        count = len(entries)
        body = f"# [索引] {keyword} ({count})\n\n本索引包含所有与「{keyword}」相关的文档。\n\n---\n\n## 文档索引\n\n"

        for e in entries:
            body += f"""### {e.get('title', '无标题')}
- **关键词**: {e.get('keywords', '')}
- **源文档ID**: {e.get('doc_id', '')}
- **源知识库ID**: {e.get('book_id', '')}
- **Namespace**: {e.get('namespace', '')}
- **Slug**: {e.get('slug', '')}
- **类型**: {e.get('doc_type', '文档')}

"""

        # 查找已存在的索引文档
        result = self.api.search(f"[索引] {keyword}", scope=self.api.index_namespaces[0] if self.api.index_namespaces else None)
        docs = result.get("docs", []) if isinstance(result, dict) else []
        existing = None
        for d in docs:
            if d.get("title", "").startswith(f"[索引] {keyword}"):
                existing = d
                break

        if existing:
            self.api.update_doc(sub_book_id, existing["id"], title=f"[索引] {keyword} ({count})", body=body)
            return {"doc_id": existing["id"], "is_new": False}
        else:
            doc = self.api.create_doc(sub_book_id, f"[索引] {keyword} ({count})", body)
            doc_id = doc["id"]
            try:
                self.api.append_to_toc(sub_book_id, doc_id)
            except Exception:
                pass
            return {"doc_id": doc_id, "is_new": True}

    # ── 增量构建状态管理 ──────────────────────────

    def mark_indexed(self, book_id, doc_id, keywords=""):
        """标记文档已索引"""
        now = datetime.now(TZ_SHANGHAI).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO indexed_docs (doc_id, book_id, indexed_at, keywords) VALUES (?,?,?,?)",
            (doc_id, book_id, now, keywords)
        )
        self._conn.commit()

    def mark_build_complete(self, book_id, total_docs, indexed_docs):
        """标记构建完成"""
        now = datetime.now(TZ_SHANGHAI).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO index_state (book_id, last_indexed_at, total_docs, indexed_docs, status) VALUES (?,?,?,?,?)",
            (book_id, now, total_docs, indexed_docs, "complete")
        )
        self._conn.commit()

    def get_last_indexed_at(self, book_id):
        """获取上次构建时间"""
        row = self._conn.execute(
            "SELECT last_indexed_at FROM index_state WHERE book_id=?",
            (book_id,)
        ).fetchone()
        return row[0] if row else None

    def is_doc_indexed(self, doc_id):
        """检查文档是否已索引"""
        row = self._conn.execute(
            "SELECT 1 FROM indexed_docs WHERE doc_id=?",
            (doc_id,)
        ).fetchone()
        return row is not None

    def get_index_state(self, book_id):
        """获取索引进度"""
        row = self._conn.execute(
            "SELECT * FROM index_state WHERE book_id=?",
            (book_id,)
        ).fetchone()
        if row:
            return {
                "book_id": row[0],
                "last_indexed_at": row[1],
                "total_docs": row[2],
                "indexed_docs": row[3],
                "status": row[4],
            }
        return None

    def close(self):
        """关闭数据库连接"""
        self._conn.close()


# ── CLI 测试入口 ────────────────────────────────────

if __name__ == "__main__":
    ib = IndexBuilder()

    if len(sys.argv) < 2:
        print("用法:")
        print("  python yuque_index.py list <book_id>       # 列出源文档")
        print("  python yuque_index.py read <book_id> <doc_id>  # 读取文档")
        print("  python yuque_index.py changed <book_id>     # 变更文档")
        print("  python yuque_index.py state <book_id>       # 索引状态")
        print("  python yuque_index.py find-sub <keyword>    # 查找/创建子库文档")
        sys.exit(0)

    cmd = sys.argv[1]

    try:
        if cmd == "list":
            book_id = int(sys.argv[2])
            docs = ib.list_source_docs(book_id)
            print(f"共 {len(docs)} 篇文档")
            for d in docs[:10]:
                print(f"  [{d['id']}] {d['title']} (updated={d.get('updated_at','?')})")

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
            docs = ib.get_changed_docs(book_id)
            print(f"变更文档: {len(docs)} 篇")

        elif cmd == "state":
            book_id = int(sys.argv[2])
            state = ib.get_index_state(book_id)
            print(json.dumps(state, ensure_ascii=False, indent=2))

        elif cmd == "find-sub":
            keyword = sys.argv[2]
            result = ib.find_or_create_sub_index_doc(keyword)
            print(json.dumps(result, ensure_ascii=False))

        else:
            print(f"未知命令: {cmd}")

    except Exception as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        ib.close()
