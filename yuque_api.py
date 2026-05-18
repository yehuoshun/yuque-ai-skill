#!/usr/bin/env python3
"""
语雀 API 核心封装 —— 纯标准库，零外部依赖。

用法：
    from yuque_api import YuqueAPI
    api = YuqueAPI()                          # 自动读 config/yuque-config.json
    api = YuqueAPI("/path/to/config.json")    # 自定义路径

    # 知识库
    repos = api.list_repos()
    api.create_repo("测试库", "test-repo")
    api.delete_repo("yehuoshun/test-repo")    # ⚠️ 硬删除

    # 文档
    docs = api.list_docs(78276514)
    doc = api.get_doc(78276514, 123456, raw=True)
    new_id = api.create_doc(78276514, "标题", "正文 **Markdown**")
    api.update_doc(78276514, 123456, body="新内容")
    api.delete_doc(78276514, 123456)

    # 目录
    toc = api.get_toc(78276514)
    api.append_to_toc(78276514, 123456)

    # 小记
    notes = api.list_notes()
    api.create_note("今天学了 Python")

    # 搜索
    results = api.search("Docker 容器", scope="yehuoshun/index-sub-1")

    # 并发批量
    docs = api.batch_get_docs(78276514, [1, 2, 3, 4, 5])
"""

import json
import os
import time
import urllib.request
import urllib.error
import urllib.parse
import ssl
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── 配置 ──────────────────────────────────────────────

def _find_config():
    """按优先级查找配置文件"""
    candidates = [
        os.environ.get("YUQUE_CONFIG", ""),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "yuque-config.json"),
        os.path.expanduser("~/.config/yuque/config.json"),
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    raise FileNotFoundError(
        "找不到语雀配置文件。请设置 YUQUE_CONFIG 环境变量，"
        "或将 config/yuque-config.json 放在 skill 目录下。"
    )


def load_config(path=None):
    """加载配置 JSON"""
    path = path or _find_config()
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ── 错误定义 ──────────────────────────────────────────

class YuqueError(Exception):
    """语雀 API 通用错误"""
    def __init__(self, code, message, status=None, body=None):
        self.code = code
        self.message = message
        self.status = status
        self.body = body
        super().__init__(f"[{code}] {message}")


class YuqueAuthError(YuqueError):
    """401 认证错误"""


class YuqueNotFoundError(YuqueError):
    """404 资源不存在"""


class YuqueRateLimitError(YuqueError):
    """429 限流"""


def _classify_error(status, body):
    """根据 HTTP 状态码和响应体构造对应的异常"""
    if status == 401:
        return YuqueAuthError(status, "Token 无效或过期，请重新生成", status=status, body=body)
    if status == 403:
        return YuqueError(status, "权限不足", status=status, body=body)
    if status == 404:
        return YuqueNotFoundError(status, "资源不存在或已删除", status=status, body=body)
    if status == 429:
        return YuqueRateLimitError(status, "请求过频", status=status, body=body)
    if 500 <= status < 600:
        return YuqueError(status, f"语雀服务端错误 ({status})，稍后重试", status=status, body=body)
    return YuqueError(status, f"HTTP {status}", status=status, body=body)


# ── API 客户端 ────────────────────────────────────────

class YuqueAPI:
    """
    语雀 API 客户端。

    特性：
    - 纯标准库（urllib），无需 pip install
    - 自动超时（30s）
    - 429 自动退避（检测 X-RateLimit-Remaining）
    - 并发安全（ThreadPoolExecutor）
    """

    BASE = "https://www.yuque.com/api/v2"

    def __init__(self, config_path=None):
        cfg = load_config(config_path)
        self.token = cfg["token"]
        self.group = cfg["group"]
        self.default_book = cfg.get("default_book", {})
        # index_books[0] = 索引总库，index_books[1:] = 索引子库
        self.index_books = cfg.get("index_books", [])

        self._remaining = None   # 最近一次 X-RateLimit-Remaining
        self._ssl_ctx = ssl.create_default_context()

    # ── HTTP 核心 ───────────────────────────────────

    def _request(self, method, path, data=None, params=None, timeout=30, raw=False):
        """
        发送 HTTP 请求，返回解析后的 JSON 数据。

        Args:
            method: GET / POST / PUT / DELETE
            path: API 路径（如 /users/yehuoshun/repos），不含 base URL
            data: 请求体（dict），自动序列化为 JSON
            params: URL 查询参数（dict）
            timeout: 超时秒数
            raw: True 返回完整响应（含 meta），默认只返回 data 字段

        Returns:
            dict/list: 解析后的响应（raw=False 返回 data 字段，raw=True 返回完整响应）

        Raises:
            YuqueError 及其子类
        """
        self._check_rate_limit()

        url = self.BASE + path
        if params:
            qs = urllib.parse.urlencode(params, doseq=True)
            url += "?" + qs

        headers = {
            "X-Auth-Token": self.token,
            "Content-Type": "application/json",
            "User-Agent": "yuque-ai-skill/1.0",
        }

        body_bytes = None
        if data is not None:
            body_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
            headers["Content-Length"] = str(len(body_bytes))

        for attempt in range(4):  # 1 次正常 + 3 次重试
            req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
            try:
                resp = urllib.request.urlopen(req, timeout=timeout, context=self._ssl_ctx)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 3:
                    remaining = e.headers.get("X-RateLimit-Remaining") if hasattr(e, 'headers') else None
                    if remaining is not None:
                        try:
                            self._remaining = int(remaining)
                        except (ValueError, TypeError):
                            pass
                    if self._remaining is not None and self._remaining > 0:
                        time.sleep(1)  # QPS 突发，等 1s 重试
                        continue
                    elif self._remaining is not None and self._remaining == 0:
                        self._check_rate_limit()  # 小时配额耗尽，等整点
                        continue
                    else:
                        time.sleep(1)  # 无法判断，保守等 1s
                        continue
                return self._handle_http_error(e)
            except urllib.error.URLError as e:
                raise YuqueError(-1, f"网络错误: {e.reason}") from e
            except Exception as e:
                raise YuqueError(-1, f"请求异常: {e}") from e
        else:
            # 4 次全部失败（429 耗尽重试次数）
            raise YuqueRateLimitError(429, "请求过频，重试次数已耗尽")

        # 记录速率信息
        remaining = resp.getheader("X-RateLimit-Remaining")
        if remaining is not None:
            self._remaining = int(remaining)

        raw_body = resp.read().decode("utf-8")
        if not raw_body.strip():
            return None

        result = json.loads(raw_body)
        if raw:
            return result
        # 语雀 API 返回格式：{"data": {...}, "meta": {...}}  或 {"data": [...]}
        # 小记 API 返回格式：{"data": {"data": {...}}}
        data_field = result.get("data")
        if data_field is not None:
            return data_field
        return result

    def _handle_http_error(self, e):
        """处理 HTTP 错误响应"""
        raw_body = e.read().decode("utf-8", errors="replace") if e.fp else ""

        # 尝试解析 JSON 错误信息
        try:
            body = json.loads(raw_body) if raw_body else {}
        except json.JSONDecodeError:
            body = {"message": raw_body[:200]}

        msg = body.get("message") or body.get("error") or f"HTTP {e.code}"
        raise _classify_error(e.code, body).__class__(e.code, msg, status=e.code, body=body)

    def _check_rate_limit(self):
        """检查剩余配额，耗尽则等待"""
        if self._remaining is not None and self._remaining == 0:
            # 小时配额耗尽，等到下一个整点
            now = time.time()
            wait = 3600 - (now % 3600) + 5
            print(f"⚠️ 小时配额耗尽，等待 {wait:.0f}s 到整点...")
            time.sleep(wait)
            self._remaining = None

    # ── 连通性 ─────────────────────────────────────

    def hello(self):
        """测试 Token 有效性"""
        return self._request("GET", "/hello")

    def get_user(self):
        """获取当前用户信息"""
        return self._request("GET", "/user")

    # ── 知识库 ─────────────────────────────────────

    def list_repos(self, login=None):
        """获取用户知识库列表"""
        login = login or self.group
        path = f"/users/{login}/repos"
        return self._request("GET", path)

    def get_repo(self, id_or_namespace):
        """获取知识库详情（支持 book_id 或 namespace）"""
        return self._request("GET", f"/repos/{id_or_namespace}")

    def create_repo(self, name, slug, description="", public=0, _type="Book"):
        """
        创建知识库。

        Args:
            name: 知识库名称
            slug: URL slug（[a-z0-9._-]，大写自动转小写，禁空格）
            description: 描述
            public: 0=私有 1=公开 2=团队内公开
            _type: Book / Design / Column
        """
        data = {
            "name": name,
            "slug": slug,
            "description": description,
            "public": public,
            "type": _type,
        }
        return self._request("POST", f"/users/{self.group}/repos", data=data)

    def update_repo(self, id_or_namespace, **kwargs):
        """更新知识库。支持 name/slug/description/public/toc 等"""
        return self._request("PUT", f"/repos/{id_or_namespace}", data=kwargs)

    def delete_repo(self, id_or_namespace):
        """⚠️ 硬删除知识库，不可恢复"""
        return self._request("DELETE", f"/repos/{id_or_namespace}")

    # ── 文档 ───────────────────────────────────────

    def list_docs(self, book_id, offset=0, limit=100):
        """列出知识库文档（limit 最大 100）"""
        limit = min(limit, 100)
        return self._request("GET", f"/repos/{book_id}/docs", params={"offset": offset, "limit": limit})

    def get_doc(self, book_id, doc_id, raw=False):
        """
        获取文档详情。

        Args:
            book_id: 知识库 ID
            doc_id: 文档 ID (slug 也可以)
            raw: True 时返回 Markdown 原文

        Returns:
            raw=False: 文档元信息 dict
            raw=True: 文档 Markdown 正文（字符串，在 data.body 或 data.body_draft）
        """
        params = {"raw": 1} if raw else None
        return self._request("GET", f"/repos/{book_id}/docs/{doc_id}", params=params)

    def get_doc_body(self, book_id, doc_id):
        """获取文档 Markdown 正文（便捷方法）"""
        result = self.get_doc(book_id, doc_id, raw=True)
        if isinstance(result, dict):
            return result.get("body") or result.get("body_draft") or ""
        return str(result) if result else ""

    def create_doc(self, book_id, title, body="", slug="", _format="markdown", public=None):
        """
        创建文档。

        Args:
            book_id: 知识库 ID
            title: 标题
            body: Markdown 正文
            slug: 自定义 slug（可选）
            _format: markdown / lake / demo
            public: 0=私有 1=公开

        Returns:
            dict: 含 id, slug 等字段

        ⚠️ 创建后文档默认不显示，需调用 append_to_toc()
        """
        data = {"title": title, "body": body, "format": _format}
        if slug:
            data["slug"] = slug
        if public is not None:
            data["public"] = public
        return self._request("POST", f"/repos/{book_id}/docs", data=data)

    def update_doc(self, book_id, doc_id, title=None, body=None, slug=None, public=None):
        """更新文档。只传需要修改的字段"""
        data = {}
        if title is not None:
            data["title"] = title
        if body is not None:
            data["body"] = body
        if slug is not None:
            data["slug"] = slug
        if public is not None:
            data["public"] = public
        if not data:
            raise ValueError("至少指定一个要更新的字段")
        return self._request("PUT", f"/repos/{book_id}/docs/{doc_id}", data=data)

    def delete_doc(self, book_id, doc_id):
        """⚠️ 硬删除文档，不可恢复"""
        return self._request("DELETE", f"/repos/{book_id}/docs/{doc_id}")

    def create_doc_with_toc(self, book_id, title, body="", slug="", _format="markdown", parent_uuid=None, prepend=False):
        """
        创建文档并自动挂载到目录。

        Args:
            prepend: True 则插入目录第一位，否则追加到最后

        Returns:
            dict: 创建的文档信息
        """
        doc = self.create_doc(book_id, title, body, slug, _format)
        doc_id = doc["id"]
        self.append_to_toc(book_id, doc_id, parent_uuid=parent_uuid, prepend=prepend)
        return doc

    # ── 目录 ───────────────────────────────────────

    def get_toc(self, book_id):
        """获取知识库目录"""
        return self._request("GET", f"/repos/{book_id}/toc")

    def update_toc(self, book_id, toc_data):
        """全量替换目录"""
        return self._request("PUT", f"/repos/{book_id}/toc", data=toc_data)

    def append_to_toc(self, book_id, doc_id, parent_uuid=None, prepend=False, retries=3):
        """
        将文档挂载到目录。

        Args:
            book_id: 知识库 ID
            doc_id: 文档 ID
            parent_uuid: 父节点 UUID（None=根目录）
            prepend: True 插到首位，False 追加到末尾
            retries: 失败重试次数
        """
        action = "prependNode" if prepend else "appendNode"
        data = {
            "action": action,
            "action_mode": "sibling",
            "type": "DOC",
            "doc_ids": [int(doc_id)],
        }
        if parent_uuid:
            data["parent_uuid"] = parent_uuid

        last_err = None
        for i in range(retries):
            try:
                return self._request("PUT", f"/repos/{book_id}/toc", data=data)
            except YuqueError as e:
                last_err = e
                if i < retries - 1:
                    time.sleep(1)
        raise YuqueError(-1, f"TOC 挂载失败（重试 {retries} 次）: {last_err}") from last_err

    # ── 搜索 ───────────────────────────────────────

    def search(self, query, scope=None, _type="doc", page=1):
        """
        搜索文档。

        Args:
            query: 搜索关键词（空格分隔多词）
            scope: 搜索范围 namespace（如 yehuoshun/index-sub-1），None=全局
            _type: doc / design / table
            page: 页码（每页固定 20 条）

        Returns:
            dict: {"total": int, "docs": [...], "page": int, "page_size": 20}
        """
        params = {"q": query, "type": _type, "page": page}
        if scope:
            params["scope"] = scope
        result = self._request("GET", "/search", params=params, raw=True)
        meta = result.get("meta", {}) if isinstance(result, dict) else {}
        docs = result.get("data", []) if isinstance(result, dict) else result
        return {
            "total": meta.get("total", len(docs) if isinstance(docs, list) else 0),
            "page": meta.get("pageNo", page),
            "page_size": meta.get("pageSize", 20),
            "docs": docs if isinstance(docs, list) else [],
        }

    def search_all_pages(self, query, scope=None, _type="doc", max_pages=100):
        """
        搜索所有分页结果，返回完整文档列表。

        Args:
            max_pages: 最多翻页数（语雀限制 100 页）
        """
        all_docs = []
        for page in range(1, max_pages + 1):
            result = self.search(query, scope=scope, _type=_type, page=page)
            docs = result.get("docs", []) if isinstance(result, dict) else []
            if not docs:
                break
            all_docs.extend(docs)
            if len(docs) < 20:
                break
        return all_docs

    # ── 小记 ───────────────────────────────────────

    def list_notes(self, page=1, limit=20, status=0):
        """
        获取小记列表。

        Args:
            status: 0=正常 9=已删除
        """
        params = {"page": page, "limit": limit, "status": status}
        result = self._request("GET", "/notes", params=params)
        # 语雀返回结构有时是嵌套的
        return result

    def get_note(self, note_id):
        """获取小记详情"""
        return self._request("GET", f"/notes/{note_id}")

    def create_note(self, body, html="", abstract=""):
        """
        创建小记。

        ⚠️ API 只返回 note_url，不返回 note_id。
        如需获取 ID，创建后再调 list_notes 通过 slug 匹配。
        """
        data = {"body": body}
        if html:
            data["html"] = html
        if abstract:
            data["abstract"] = abstract
        return self._request("POST", "/notes", data=data)

    def update_note(self, note_id, body=None, html=None, abstract=None):
        """
        更新小记。

        ⚠️ 必须先 GET 获取原内容，source/html/abstract 三个字段缺一不可。
        返回结构为 {data: {data: {...}}}，取结果用 result.get("data")。
        """
        original = self.get_note(note_id)
        # 小记返回结构：{content: {source: "...", html: "...", abstract: "..."}, ...}
        # 也可能是 data.data 嵌套
        if isinstance(original, dict):
            content = original.get("content") or original.get("data", {}).get("content") or original
        else:
            content = {}

        # 提取 source/html/abstract
        source_val = body if body is not None else (content.get("source") or content.get("body") or "")
        html_val = html if html is not None else (content.get("html") or "")
        abstract_val = abstract if abstract is not None else (content.get("abstract") or "")

        data = {
            "source": source_val,
            "html": html_val,
            "abstract": abstract_val,
        }
        result = self._request("PUT", f"/notes/{note_id}", data=data)
        # 小记更新返回结构为 {data: {data: {...}}}，提取内层 data
        if isinstance(result, dict) and "data" in result:
            return result["data"]
        return result

    def delete_note(self, note_id):
        """
        软删除小记（status=9）。

        ⚠️ 必须先 GET 获取原内容，再 PUT 设 status=9。
        """
        original = self.get_note(note_id)
        if isinstance(original, dict):
            content = original.get("content") or original.get("data", {}).get("content") or {}
        else:
            content = {}
        source = content.get("source") or content.get("body") or ""
        html_val = content.get("html") or ""
        abstract_val = content.get("abstract") or ""

        data = {
            "source": source,
            "html": html_val,
            "abstract": abstract_val,
            "status": 9,
        }
        return self._request("PUT", f"/notes/{note_id}", data=data)

    def recover_note(self, note_id):
        """恢复小记（status=0）"""
        original = self.get_note(note_id)
        if isinstance(original, dict):
            content = original.get("content") or original.get("data", {}).get("content") or {}
        else:
            content = {}
        source = content.get("source") or content.get("body") or ""
        html_val = content.get("html") or ""
        abstract_val = content.get("abstract") or ""

        data = {
            "source": source,
            "html": html_val,
            "abstract": abstract_val,
            "status": 0,
        }
        return self._request("PUT", f"/notes/{note_id}", data=data)

    # ── 搜索笔记 ───────────────────────────────────

    def search_notes(self, query, page=1):
        """搜索小记"""
        params = {"q": query, "page": page}
        return self._request("GET", "/search/notes", params=params)

    # ── 批量操作 & 并发 ────────────────────────────

    def batch_get_docs(self, book_id, doc_ids, max_workers=5):
        """
        并发获取多篇文档。

        Args:
            book_id: 知识库 ID
            doc_ids: 文档 ID 列表
            max_workers: 最大并发数

        Returns:
            dict: {doc_id: doc_data, ...}
        """
        results = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(self.get_doc, book_id, doc_id, True): doc_id
                for doc_id in doc_ids
            }
            for future in as_completed(futures):
                doc_id = futures[future]
                try:
                    results[doc_id] = future.result()
                except YuqueError as e:
                    results[doc_id] = {"error": str(e)}
        return results

    def batch_search(self, queries, scope=None, max_workers=5):
        """
        多组关键词并发搜索。

        Args:
            queries: ["关键词A B C", "关键词D E F", ...]

        Returns:
            dict: {query: search_result, ...}
        """
        results = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(self.search, q, scope=scope): q
                for q in queries
            }
            for future in as_completed(futures):
                q = futures[future]
                try:
                    results[q] = future.result()
                except YuqueError as e:
                    results[q] = {"error": str(e)}
        return results

    def list_all_docs(self, book_id, max_workers=3):
        """
        并发分页获取知识库全部文档（超过 100 篇时分页）。

        Returns:
            list: 全部文档列表
        """
        first = self.list_docs(book_id, offset=0, limit=100)
        all_docs = list(first) if isinstance(first, list) else []
        if len(all_docs) < 100:
            return all_docs

        # 顺序拉后续分页，直到某页不足 100
        offset = 100
        while True:
            page_result = self.list_docs(book_id, offset=offset, limit=100)
            page_docs = list(page_result) if isinstance(page_result, list) else []
            all_docs.extend(page_docs)
            if len(page_docs) < 100:
                break
            offset += 100

        return all_docs

    # ── 导出 ───────────────────────────────────────

    def export_doc_markdown(self, book_id, doc_id, output_dir=None):
        """
        导出单篇文档为 Markdown 文件。

        Args:
            book_id: 知识库 ID
            doc_id: 文档 ID
            output_dir: 输出目录（默认当前目录）

        Returns:
            str: 输出文件路径
        """
        result = self.get_doc(book_id, doc_id, raw=True)
        if isinstance(result, dict):
            title = result.get("title", f"doc_{doc_id}")
            body = result.get("body") or result.get("body_draft") or ""
        else:
            title = f"doc_{doc_id}"
            body = str(result) if result else ""

        # 安全文件名
        safe_title = "".join(c for c in title if c.isalnum() or c in "._- ()（）")
        output_dir = output_dir or "."
        os.makedirs(output_dir, exist_ok=True)
        filepath = os.path.join(output_dir, f"{safe_title}.md")

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(body)

        return filepath

    def export_repo(self, book_id, output_dir=None, max_workers=3):
        """
        批量导出知识库所有文档。

        Returns:
            dict: {"total": int, "success": int, "failed": int, "files": [...]}
        """
        all_docs = self.list_all_docs(book_id)
        repo = self.get_repo(book_id)
        repo_name = repo.get("name", f"repo_{book_id}") if repo else f"repo_{book_id}"
        safe_name = "".join(c for c in repo_name if c.isalnum() or c in "._- ()（）")
        output_dir = output_dir or safe_name
        os.makedirs(output_dir, exist_ok=True)

        success = 0
        failed = 0
        files = []

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(self.export_doc_markdown, book_id, d["id"], output_dir): d
                for d in all_docs
            }
            for future in as_completed(futures):
                doc = futures[future]
                try:
                    path = future.result()
                    files.append(path)
                    success += 1
                except Exception:
                    failed += 1

        return {
            "repo": repo_name,
            "total": len(all_docs),
            "success": success,
            "failed": failed,
            "output_dir": os.path.abspath(output_dir),
            "files": files,
        }

    # ── 工具方法 ───────────────────────────────────

    def resolve_namespace(self, book_id):
        """book_id → namespace"""
        repo = self.get_repo(book_id)
        if repo:
            login = repo.get("user", {}).get("login") or repo.get("creator", {}).get("login") or self.group
            slug = repo.get("slug", "")
            return f"{login}/{slug}"
        return None

    def resolve_book_id(self, namespace):
        """namespace → book_id"""
        repo = self.get_repo(namespace)
        return repo.get("id") if repo else None

    def validate_token(self):
        """验证 Token 有效性，返回 user 信息或抛出异常"""
        return self.hello()

    @property
    def default_book_id(self):
        return self.default_book.get("book_id") if self.default_book else None

    @property
    def default_namespace(self):
        return self.default_book.get("namespace") if self.default_book else None

    @property
    def index_master_book_id(self):
        """索引总库 ID（index_books 第一个元素）"""
        return self.index_books[0].get("book_id") if self.index_books else None

    @property
    def index_master_namespace(self):
        """索引总库 namespace（index_books 第一个元素）"""
        return self.index_books[0].get("namespace") if self.index_books else None

    @property
    def index_book_ids(self):
        """所有索引库的 book_id 列表（含总库）"""
        return [b.get("book_id") for b in self.index_books if b.get("book_id")]

    @property
    def index_namespaces(self):
        """所有索引库的 namespace 列表（含总库）"""
        return [b.get("namespace") for b in self.index_books if b.get("namespace")]


# ── CLI 入口（调试用） ──────────────────────────────

if __name__ == "__main__":
    import sys

    api = YuqueAPI()

    if len(sys.argv) < 2:
        print("用法: python yuque_api.py <命令> [参数...]")
        print("命令: hello | list-repos | list-docs <book_id> | get-doc <book_id> <doc_id>")
        print("      search <query> [scope] | list-notes | export <book_id> [output_dir]")
        sys.exit(0)

    cmd = sys.argv[1]

    try:
        if cmd == "hello":
            result = api.hello()
            print(json.dumps(result, ensure_ascii=False, indent=2))

        elif cmd == "list-repos":
            repos = api.list_repos()
            for r in repos:
                print(f"  [{r['id']}] {r['name']} ({r.get('user',{}).get('login','')}/{r['slug']}) - {r.get('items_count',0)} 篇")

        elif cmd == "list-docs":
            book_id = int(sys.argv[2])
            docs = api.list_docs(book_id)
            for d in docs:
                print(f"  [{d['id']}] {d['title']}")

        elif cmd == "get-doc":
            book_id = int(sys.argv[2])
            doc_id = sys.argv[3]
            body = api.get_doc_body(book_id, doc_id)
            print(body[:2000])

        elif cmd == "search":
            query = sys.argv[2]
            scope = sys.argv[3] if len(sys.argv) > 3 else api.default_namespace
            result = api.search(query, scope=scope)
            for d in (result.get("docs") or []):
                print(f"  [{d['id']}] {d['title']} — {d.get('summary','')[:80]}")

        elif cmd == "list-notes":
            notes = api.list_notes()
            print(json.dumps(notes, ensure_ascii=False, indent=2))

        elif cmd == "export":
            book_id = int(sys.argv[2])
            output_dir = sys.argv[3] if len(sys.argv) > 3 else None
            result = api.export_repo(book_id, output_dir)
            print(f"导出完成: {result['success']}/{result['total']} → {result['output_dir']}")

        else:
            print(f"未知命令: {cmd}")

    except YuqueError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
