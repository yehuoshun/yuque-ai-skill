---
name: yuque-ai
description: 语雀全功能技能。支持 AI 问答、知识库管理、文档管理、小记管理、目录管理、索引管理、文档导出。版本管理、群组、统计等按需查 API 参考。当用户提到「语雀」时触发，如「在语雀搜索...」「我的语雀知识库...」「创建语雀文档...」「语雀小记...」。
---

# 语雀 AI 技能

> API 端点/参数/错误码/限制 → **[references/api_reference.md](references/api_reference.md)**

## 触发

消息含「语雀」「小记」「知识库」「索引」即触发。不确定时主动触发。

## 配置

默认从 skill 目录下 `config/yuque-config.json` 读取，支持自定义路径。

```json
{
  "token": "语雀 API Token",
  "group": "用户名",
  "default_book": { "book_id": 0, "namespace": "" }
}
```

首次使用依次检查：Token 有效性（`/hello`）→ 知识库存在性 → 缺失则提示创建。

## 调用约定

- **基地址**：`https://www.yuque.com/api/v2`
- **方式**：Python `urllib.request`（禁止 pip install），简单请求可用 curl
- **超时**：所有请求 `timeout=30`
- **并发**：批量场景 `ThreadPoolExecutor`，并发 ≤ 5
- **速率**：每批次请求后检查 `X-RateLimit-Remaining`，<200 暂停等整点；429 区分 5000/h（暂停）和 100/s（等1s重试×3）
- **scope**：搜索 API 用 namespace 格式（`group/book_slug`），不支持 book_id

## API 速查

### 知识库

| 操作 | 端点 | 注意 |
|------|------|------|
| 列表 | `GET /users/{login}/repos` | 一次返回全部 |
| 详情 | `GET /repos/{id_or_namespace}` | id 或 namespace 均可 |
| 创建 | `POST /users/{login}/repos` | **slug 必填**，格式 `{缩写}-{时间戳}` |
| 更新 | `PUT /repos/{id_or_namespace}` | 支持 `toc` 全量替换目录 |
| 删除 | `DELETE /repos/{id_or_namespace}` | 硬删除不可逆，**必须先确认** |

### 文档

| 操作 | 端点 | 注意 |
|------|------|------|
| 列表 | `GET /repos/{book_id}/docs?offset=0&limit=100` | limit 最大 100 |
| 详情 | `GET /repos/{book_id}/docs/{doc_id}?raw=1` | raw=1 返回 markdown |
| 创建 | `POST /repos/{book_id}/docs` | title+body 必填；**创建后必须 `PUT /toc` 挂目录** |
| 更新 | `PUT /repos/{book_id}/docs/{doc_id}` | |
| 删除 | `DELETE /repos/{book_id}/docs/{doc_id}` | 硬删除不可逆，**必须先确认** |

> ⚠️ **TOC 挂载**：`POST /docs` 后文档默认不显示。调 `PUT /toc`（action=appendNode, action_mode=sibling, type=DOC, doc_ids=[id], target_uuid=首个TITLE的uuid）。失败等1s重试×3，仍失败则提示手动拖入。

### 小记

| 操作 | 端点 | 注意 |
|------|------|------|
| 列表 | `GET /notes?page=1&limit=20&status=0` | 返回 `{pin_notes, notes, has_more}` |
| 详情 | `GET /notes/{note_id}` | content 是嵌套对象：`note.content.source` |
| 创建 | `POST /notes` | body 必填，只返回 `note_url` 不返回 id |
| 更新 | `PUT /notes/{note_id}` | source/html/abstract 三个字段缺一不可 |
| 删除 | `PUT /notes/{note_id}`（status=9） | 软删除，可恢复 |
| 恢复 | `PUT /notes/{note_id}`（status=0） | |

### 搜索

```
GET /search?q={query}&type=doc&scope={namespace}&page=1
```

- PageSize 固定 20，最多 100 页
- scope 只支持 namespace 格式，不支持 book_id
- 返回 summary 含 `<em>` 高亮

### 目录 / 群组 / 统计 / 版本

不常用，按需查 [api_reference.md](references/api_reference.md)。

### 文档导出

**单篇**：`GET /docs?raw=1` → 保存为 `{标题}.md`
**批量**：分页遍历 → 逐篇获取 → 保存到指定目录，每页汇报进度
附件/图片不自动下载，仅保留原始链接。

## 创建文档完整流程（强制）

```
POST /repos/{book_id}/docs  →  获取 doc_id
  ↓
GET /repos/{book_id}/toc    →  取首个节点 uuid
  ↓
PUT /repos/{book_id}/toc    →  appendNode + sibling + target_uuid
  ↓
验证文档出现在 TOC 返回中
```

若 `PUT /toc` 返回 404：检查 target_uuid 是否存在、book_id 是否正确、文档是否真的创建成功。排除后重试 curl 方式（非 Python）。

## 索引管理

AI 问答的前置依赖，必须先有索引才能走 Layer 1 / Layer 2 检索。

**构建**：「构建语雀索引」
- 自动模式：每批 10 篇连续跑，>2000 篇拒绝改手动
- 手动模式：「手动构建索引，每批 N 篇」，每批暂停等「继续」
- 每篇生成关键词 + 同义词 + questions + direct_answer

**增量**：「更新《XXX》的索引」→ 单篇重新索引

**补洞**：「补洞」「回灌漏提问」→ 处理 AI 问答 Layer 3 记录的 leak_queries

索引状态文件：skill 目录下 `state/index_state.json`

## AI 问答（三层检索）

**触发**：「在语雀搜索...」「问语雀...」

三层架构，按优先级递减：

1. **Layer 1**：索引中 questions 精确/模糊匹配 → 直接返回 direct_answer
2. **Layer 2**：关键词命中但 questions 未命中 → 读 Top 5 chunk → LLM 生成
3. **Layer 3**：索引未覆盖 → 原生搜索兜底 → 读 Top 3 全文 → LLM 生成 → 记录漏提问

回答始终标注来源文档链接。

## 删除确认规范

| 操作 | 类型 | 确认模板 |
|------|------|---------|
| 删知识库 | 硬删除 | `⚠️ 即将删除《XXX》，含 N 篇文档。不可恢复，确认？` |
| 删文档 | 硬删除 | `⚠️ 即将删除《XXX》。不可恢复，确认？` |
| 删小记 | 软删除 | `📝 移入回收站，可恢复。确认？` |

---

> 详细 API 参数/返回结构/错误码/故障排查 → **[references/api_reference.md](references/api_reference.md)**
