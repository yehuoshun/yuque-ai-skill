---
name: yuque-ai
description: 语雀全功能技能。支持知识库管理、文档管理、小记管理、目录管理、文档导出。版本管理、群组、统计等按需查 API 参考。当用户提到「语雀」时触发，如「在语雀搜索...」「我的语雀知识库...」「创建语雀文档...」「语雀小记...」。
---

# 语雀 AI 技能

> API 端点/参数/错误码/限制 → **[references/api_reference.md](references/api_reference.md)**

## 触发

消息含「语雀」「小记」「知识库」即触发。不确定时主动触发。

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
- **并发**：按操作类型分级
  - **API 轨**（列表/搜索/文档 CRUD/目录/导出）：初始并发 5，上限 10。每批后读 `X-RateLimit-Remaining` 动态调节
  - **LLM 轨**（索引/问答/内容生成等）：`LLM并发 = clamp(1, floor(可用内存MB / 1024), 3)`，即 ≥3GB→3, ≥2GB→2, <2GB→1。耗时兜底：连续 3 次 >10s 降 1 级，>30s 暂停
  - 混合场景自动切换：拉文档走 API 轨、过 LLM 走 LLM 轨，两轨不互阻
- **速率**：每批次请求后检查 `X-RateLimit-Remaining`。429 响应：检查 `X-RateLimit-Remaining`，≠0 则等 1s 重试（QPS 突发），=0 则暂停等整点（小时配额耗尽）
- **scope**：搜索 API 用 namespace 格式（`group/book_slug`），不支持 book_id

## API 速查

### 知识库

| 操作 | 端点 | 注意 |
|------|------|------|
| 列表 | `GET /users/{login}/repos` | 一次返回全部 |
| 详情 | `GET /repos/{id_or_namespace}` | id 或 namespace 均可 |
| 创建 | `POST /users/{login}/repos` | name+slug 必填。slug 约束：`[a-z0-9._-]`，大写自动转小写，禁空格 |
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

> ⚠️ **TOC 挂载**：`POST /repos/{book_id}/docs` 后文档默认不显示。调 `PUT /toc`（action=appendNode, action_mode=sibling, type=DOC, doc_ids=[id]）。失败等1s重试×3，仍失败则提示手动拖入。

### 小记

| 操作 | 端点 | 注意 |
|------|------|------|
| 列表 | `GET /notes?page=1&limit=20&status=0` | 返回 `{pin_notes, notes, has_more}` |
| 详情 | `GET /notes/{note_id}` | content 是嵌套对象：`note.content.source` |
| 创建 | `POST /notes` | body 必填，只返回 `note_url`。需查列表通过 slug 匹配获取 id |
| 更新 | `PUT /notes/{note_id}` | 先 GET 获取原内容，再 PUT。source/html/abstract 三个字段缺一不可 |
| 删除 | `PUT /notes/{note_id}`（status=9） | 软删除。**先 GET 获取原内容**，再 PUT 设 status=9 |
| 恢复 | `PUT /notes/{note_id}`（status=0） | **先 GET 获取原内容**，再 PUT 设 status=0 |

### Hello

| 操作 | 端点 | 注意 |
|------|------|------|
| 连通测试 | `GET /hello` | 验证 Token 有效性 |

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

**单篇**：`GET /repos/{book_id}/docs/{doc_id}?raw=1` → 保存为 `{标题}.md`

**批量导出（完整流程）**：
1. `GET /toc` 获取知识库目录树，生成本地文件夹结构
2. 分页遍历文档列表 → 逐篇 `GET /docs/{doc_id}?raw=1` 获取 Markdown 正文
3. 下载图片附件，替换文档内图片链接为本地相对路径
4. 处理文档间交叉引用，替换为本地相对链接
5. 支持增量导出：对比本地已有文件更新时间，仅处理变更文档
6. 每 10 篇汇报一次进度

**任务结束**：汇总通知（成功/失败/跳过篇数、保存路径、耗时、总大小）

> ⚠️ **局限性**：Lake 文档（新版编辑器）和表格 API 可获取内容（`body_lake` JSON / 结构化数据），但无法完美转为 Markdown，会丢失复杂排版和嵌入组件。画板/白板完全无法导出。

## 创建文档完整流程（强制）

```
POST /repos/{book_id}/docs  →  获取 doc_id
  ↓
PUT /repos/{book_id}/toc    →  action=appendNode, action_mode=sibling, type=DOC, doc_ids=[id]
  ↓
验证文档出现在 TOC 返回中
```

若 `PUT /toc` 返回 404：检查 book_id 是否正确、文档是否真的创建成功。排除后重试 curl 方式（非 Python）。

> 💡 挂载为子节点时需 `action_mode=child` + `target_uuid`，需先 GET `/toc` 取目标节点 uuid。

## 错误处理

| 错误码 | 说明 | 处理 |
|--------|------|------|
| 401 | Token 无效/过期 | 引导用户重新生成 Token 并更新配置 |
| 403 | 权限不足 | 检查 Token 权限范围 |
| 404 | 资源不存在 | 检查 ID 是否正确或已删除 |
| 429 | 请求过频 | 见[调用约定](#调用约定)速率部分 |
| 500/502/503/504 | 服务端错误 | 稍后重试 |

## 删除确认规范

| 操作 | 类型 | 确认模板 |
|------|------|---------|
| 删知识库 | 硬删除 | `⚠️ 即将删除《XXX》，含 N 篇文档。不可恢复，确认？` |
| 删文档 | 硬删除 | `⚠️ 即将删除《XXX》。不可恢复，确认？` |
| 删小记 | 软删除 | `📝 移入回收站，可恢复。确认？` |
| 移群成员 | 硬删除 | `⚠️ 即将将成员 @XXX 移出群组。不可恢复，确认？` |

---

> 详细 API 参数/返回结构/错误码/故障排查 → **[references/api_reference.md](references/api_reference.md)**
