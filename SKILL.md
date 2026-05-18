---
name: yuque-ai
description: 语雀全功能技能。支持知识库管理、文档管理、小记管理、目录管理、文档导出 + 两级索引知识库问答（纯 LLM + 语雀 API，零外部依赖）。当用户提到「语雀」时触发，如「在语雀搜索...」「我的语雀知识库...」「创建语雀文档...」「语雀小记...」。
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
  "default_book": { "book_id": 0, "namespace": "" },
  "index_master_book": { "book_id": 0, "namespace": "" },
  "index_books": [{ "book_id": 0, "namespace": "" }],
  "search_cache_enabled": false,
  "incremental_index_enabled": false,
  "search_report_enabled": false
}
```

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `token` | — | 语雀 API Token（必填） |
| `group` | — | 语雀用户名/login（必填） |
| `default_book` | — | 默认知识库（内容库），不指定目标时自动使用 |
| `index_master_book` | — | 索引总库，问答路由使用 |
| `index_books` | — | 索引子库列表 |
| `search_cache_enabled` | false | 搜索结果缓存（SQLite，10分钟TTL） |
| `incremental_index_enabled` | false | 增量索引（仅索引变更文档） |
| `search_report_enabled` | false | 搜索报告开关，调试时开启 |

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

---

# 一、知识库问答系统

> **铁律**：不用嵌入模型、不用向量数据库、不用额外模型服务、不用第三方搜索 API。仅 LLM API + 语雀 API + 本地 SQLite 缓存。

## 1. 架构：两级索引 + 多路并发

### 1.1 索引总库（路由）

标题 = `[索引] 关键词`，正文 = JSON（`keyword` + `sub_docs` 数组）。
搜索时标题匹配 → 解析 sub_docs → 拿到子库索引文档的 doc_id/book_id/namespace。

**JSON 格式**：
```json
{
  "keyword": "Docker",
  "sub_docs": [
    {
      "title": "[索引] Docker (1)",
      "doc_id": 123456,
      "book_id": 51689762,
      "namespace": "yehuoshun/rqgc16"
    }
  ]
}
```

> `sub_docs` 是数组，支持一个关键词路由到多个子库。

### 1.2 索引子库（关键词→来源）

标题 = `[索引] 关键词 (N)`，正文 = JSON（`keyword` + `source_entries` 数组）。
每个 source_entry 指向源知识库中的一篇文档。

**JSON 格式**：
```json
{
  "keyword": "Docker-部署",
  "source_entries": [
    {
      "doc_id": 263733036,
      "book_id": 37800749,
      "title": "Docker Compose 多服务编排",
      "namespace": "yehuoshun/bhcllx",
      "slug": "doc263733036",
      "keywords": "docker,compose,多服务,编排",
      "content_segment": "docker-compose.yml 通过 services 字段...",
      "doc_type": "文档"
    }
  ]
}
```

> **构建原则**：单个索引文档来源 5-15 个。关键词过宽时拆细粒度（如 Docker→Docker-部署、Docker-网络）。
>
> **Lake 卡片**：正文不可读时 `content_segment` 填标题，搜索时标注「仅标题匹配」。
>
> 兼容旧 Markdown 格式（`### 标题\n- **源文档ID**: xxx`），自动识别。

## 2. 搜索流程

```
用户提问: "Java 面试怎么准备"
         │
         ├─[0] 前置：用户指定了文档名？
         │      → 是：直接全库搜索 → 读原文 → LLM 总结（短路）
         │      → 否：继续
         │
         ├─[1] LLM 生成 3-4 组关键词 → batch_search 索引总库
         │      → 命中路由文档（标题=[索引] xx）→ parse_master_body → 提取 sub_docs
         │      → 拿到子库索引文档的 doc_id + book_id + namespace
         │
         ├─[2] batch_search 索引子库（多组关键词 × 子库 namespace）
         │      → 命中索引文档 → read 全文 → parse_sub_index_body → 提取 source_entries
         │
         ├─[3] 合并去重（按 source doc_id）
         │
         ├─[4] 提取 content_segment
         │      有内容段 → 直接送入 LLM
         │      无内容段（Lake卡片）→ 标注"仅标题匹配" → 读取原文尝试
         │
         ├─[5] LLM 判断 content_segment 是否足以回答
         │      不足 → read_source_docs_across_books（跨知识库并发读取原文）
         │
         └─[6] LLM 生成答案 + 引用出处
```

### 2.1 降级模式

索引管线命中不足或未配置索引时，降级为**语雀全库搜索**（不传 scope，搜用户全部知识库）：

```
LLM 生成搜索词 → batch_search（无 scope）→ 语雀原生全库搜索
→ 返回标题 + 摘要 → LLM 筛选 → 读原文 → LLM 生成答案
```

降级触发：
- 未配置 index_books
- 索引总库无命中
- 索引子库无命中
- content_segment 全空且原文读取失败

## 3. 缓存策略

> 默认关闭。通过 `search_cache_enabled: true` 启用，启用后创建本地 SQLite。

| 缓存层 | 存储 | TTL | 说明 |
|--------|------|-----|------|
| 搜索结果 | SQLite | 10分钟 | 同 query 复用，省重复 API 调用 |
| 索引文档内容 | **不缓存** | — | 每次实时读取，保证一致性 |

## 4. 搜索 Prompt 模板

### 4.1 搜索词生成

```
把用户问题改写成 3-4 组搜索关键词，用于关键词匹配搜索。
每组空格分隔，覆盖不同表述方式。

用户问题：{question}

搜索词（每行一组）：
```

### 4.2 标题筛选（两阶段搜索）

```
以下是通过关键词搜索命中的索引文档标题列表。
请选出与用户问题最相关的 3-5 个，其余跳过。

用户问题：{question}

索引文档标题：
{titles}

输出 JSON：{"selected": ["标题A", "标题B"]}
```

### 4.3 答案生成

```
基于以下内容段回答用户问题。每个内容段标注了来源。

内容段：
{content_segments}

用户问题：{question}

要求：
1. 优先使用内容段中的信息
2. 内容段不足时标注需要补充搜索
3. 回答末尾列出引用的来源 doc_name + doc_link
```

## 5. 搜索降级

```
正常路径（索引管线）
  ↓ 索引命中不足 / 未配置索引子库
降级模式（跳过索引层，直接搜内容库）
  ↓ 仍 0 命中
返回「未找到相关内容，请尝试换个问法」
```

## 6. 搜索报告（调试用，默认关闭）

每次搜索结束后生成结构化报告用于调试，**不给用户看**。通过配置 `search_report_enabled: true` 开启。

```json
{
  "timestamp": "2026-05-15T16:00:00+08:00",
  "query": "Docker 容器之间怎么通信",
  "pipeline": {
    "search_word_gen": { "model": "deepseek-v4-pro", "keywords": [["Docker","容器","通信"],["container","network"],["Docker","网络","互联"]] },
    "route": { "source": "cache", "hit": true, "sub_books": ["yehuoshun/idx-docker"] },
    "search": { "total_api_calls": 3, "hits_per_variant": {"docker 容器 通信": 5} },
    "filter": { "method": "exact_title_match", "selected_docs": ["[索引] Docker-网络"] },
    "read": { "index_docs_read": 3, "source_entries_extracted": 8 },
    "generate": { "model": "deepseek-v4-pro", "input_tokens": 1840, "output_tokens": 320 }
  },
  "latency_ms": { "search_word_gen": 280, "route": 2, "search": 420, "generate": 1800, "total": 2702 },
  "sources": [{ "doc_name": "...", "doc_id": 263733042, "doc_link": "..." }]
}
```

| 关键字段 | 用途 |
|----------|------|
| `pipeline.*.source` | cache 还是 API/LLM |
| `pipeline.search.hits_per_variant` | 各组搜索词命中数（比对各组效果） |
| `latency_ms.*` | 各阶段耗时（定位瓶颈） |
| `sources` | 最终引用来源列表 |

## 7. 索引构建（离线）

### 7.1 索引子库文档格式

```markdown
[索引] {关键词}

##关键词
{关键词密集排列区：空格分隔，每行 5-15 个}

##来源
- doc_name: {文档标题}
  doc_link: {语雀链接}
  doc_id: {数字ID}
  slug: {slug}
  namespace: {group/slug}
  keywords: {逗号分隔关键词}
  内容段: {原文关键段落 50-200 字}
```

### 7.2 索引构建 Prompt

```
你是一个搜索索引构建器。阅读以下文档，提取所有可用于搜索的关键词和短语。

要求：
1. 穷举文档中的核心概念、术语、操作名
2. 为每个核心概念穷举别名：全称、中文缩写、英文缩写、英文全称、口语俗称、旧称/历史名称
   例如 Kubernetes → k8s, kube, k8s集群, 容器编排平台, 容器调度引擎
   例如 Redis → redis缓存, 内存数据库, 键值存储
   **所有别名都必须写进 ##关键词 段**，一行一种表述，确保搜索时无论用户用什么说法都能命中
3. 提取 1-3 个内容段（每段 50-200 字），覆盖文档的不同主题方面
4. 关键词用空格分隔，每行 5-15 个
5. 不虚构文档没有的事实

文档标题：{title}
文档正文：{body}

输出格式：
##关键词
[行1: 关键词 关键词 ...]
[行2: 关键词 关键词 ...]
##来源
- doc_name: {title}
  doc_link: {url}
  doc_id: {id}
  slug: {slug}
  namespace: {namespace}
  keywords: {提取的关键词逗号分隔}
  内容段: {提取的原文关键段落}
```

### 7.3 构建流程

**全量构建**（默认）：

1. 创建索引总库 + 按领域建索引子库
2. LLM 遍历内容库文档 → 提取关键词 + 内容段
3. 按关键词归类合并同一关键词的多个来源
4. 来源过多的关键词拆细粒度（如 Docker→Docker-部署/Docker-网络）
5. 写入索引子库：`POST /repos/{index_book_id}/docs` 创建索引文档 → `PUT /repos/{index_book_id}/toc`（action=appendNode）挂载到目录
6. 写入索引总库：`POST /repos/{master_book_id}/docs` 创建路由文档（标题=关键词，正文=子库 namespace）→ `PUT /repos/{master_book_id}/toc`（action=appendNode）挂载到目录

**增量构建**（`incremental_index_enabled: true`）：

1. 拉内容库 docs list → 筛选 `updated_at > last_indexed_at` 的文档
2. 仅对变更文档跑索引构建（同上 2-4 步）
3. 更新索引子库中对应来源条目
4. 更新 `last_indexed_at = 构建开始时间`（存 SQLite `index_state` 表）

## 8. 并发策略

| 阶段 | 并发数 | 说明 |
|------|--------|------|
| 搜索词搜索（总库路由） | 3-4 | 多组关键词并发 |
| 搜索词搜索（子库） | 3-4 | namespace 限定并发 |
| 读关键词文档全文 | 按命中数 | `GET /docs/{doc_id}?raw=1` |
| 读原文（按需） | 2-3 | 仅内容段不足时 |

## 9. 风险与对策

| 风险 | 对策 |
|------|------|
| 语雀搜索分词质量未知 | 关键词采用词级空格分隔，降低对分词依赖 |
| LLM 提取关键词有遗漏 | 多路并发搜补位 |
| 索引子库容量超 5000 | 索引文档按关键词归类，数量可控 |
| 关键词过宽→单文档来源爆炸 | 拆细粒度 + 两阶段标题筛选 |
| 别名遗漏→搜索命中不足 | 索引构建强制穷举别名 + 多路并发搜补位 |
| 索引文档内容过时 | 每次实时读取，不缓存 |
| API 限流 | 缓存 + 指数退避 |

## 10. 技术依赖

| 组件 | 依赖 |
|------|------|
| LLM | 任意 OpenAI 兼容 API |
| 存储 | 语雀知识库（索引总库 + 索引子库 + 内容库） |
| 搜索 | 语雀搜索 API（namespace 限定） |
| 缓存（可选） | SQLite（仅 `search_cache_enabled` 或 `incremental_index_enabled` 为 true 时启用） |
| **额外依赖** | **零** |

---

# 二、API 速查（管理操作）

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

> ⚠️ **TOC 挂载**：`POST /repos/{book_id}/docs` 后文档默认不显示。调 `PUT /toc`（action=appendNode, action_mode=sibling, type=DOC, doc_ids=[id]）。如需放在目录第一位，用 `prependNode` 替换 `appendNode`。失败等1s重试×3，仍失败则提示手动拖入。

### 小记

| 操作 | 端点 | 注意 |
|------|------|------|
| 列表 | `GET /notes?page=1&limit=20&status=0` | 返回 `{pin_notes, notes, has_more}` |
| 详情 | `GET /notes/{note_id}` | content 是嵌套对象：`note.content.source` |
| 创建 | `POST /notes` | body 必填，只返回 `note_url`。需查列表通过 slug 匹配获取 id |
| 更新 | `PUT /notes/{note_id}` | 先 GET 获取原内容，再 PUT。source/html/abstract 三个字段缺一不可。⚠️ 返回结构为 `{data: {data: {...}}}`，取结果用 `.data.data` |
| 删除 | `PUT /notes/{note_id}`（status=9） | 软删除。**先 GET 获取原内容**，再 PUT 设 status=9 |
| 恢复 | `PUT /notes/{note_id}`（status=0） | **先 GET 获取原内容**，再 PUT 设 status=0 |

### 搜索 / 连通测试 / 目录 / 导出

不常用的端点（群组、统计、版本管理等）按需查 **[references/api_reference.md](references/api_reference.md)**。

| 操作 | 端点 | 注意 |
|------|------|------|
| 连通测试 | `GET /hello` | 验证 Token 有效性 |
| 搜索 | `GET /search?q={query}&type=doc&scope={namespace}&page=1` | PageSize 固定 20，最多 100 页 |
| 单篇导出 | `GET /repos/{book_id}/docs/{doc_id}?raw=1` → 保存 `{标题}.md` | |
| 批量导出 | 完整流程见 api_reference | 增量导出、图片下载、交叉引用替换 |

## 创建文档完整流程（强制）

```
POST /repos/{book_id}/docs  →  获取 doc_id
  ↓
PUT /repos/{book_id}/toc    →  action=appendNode, action_mode=sibling, type=DOC, doc_ids=[id]
  ↓
验证文档出现在 TOC 返回中
```

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

---

> 详细 API 参数/返回结构/错误码/故障排查 → **[references/api_reference.md](references/api_reference.md)**
