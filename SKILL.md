---
name: yuque-ai
description: 语雀全功能技能。支持 AI 问答、知识库管理、文档管理、文档版本管理、小记管理、目录管理、群组成员管理、统计数据、索引管理、文档导出。当用户提到「语雀」时触发，如「在语雀搜索...」「我的语雀知识库...」「创建语雀文档...」「语雀小记...」。
---

# 语雀全功能技能

## 概述

通过语雀 API 实现全功能操作，包括语义增强搜索和完整的语雀 API 封装。支持：

- **用户信息**：获取当前用户信息，连通性测试
- **知识库管理**：列表、详情、创建、更新、删除
- **文档管理**：列表、详情、创建、更新、删除
- **文档版本管理**：查看文档历史版本和具体版本内容
- **小记管理**：列表、详情、创建、更新、删除、恢复
- **目录管理**：获取、更新目录结构
- **群组成员管理**：列出成员、更新角色、移除成员
- **统计数据**：团队统计、成员统计、知识库统计
- **AI 问答**：基于索引库的语义搜索 + LLM Rerank
- **索引管理**：手动触发构建索引、增量更新、重试失败
- **文档导出**：单篇/批量导出为本地 Markdown

## 核心原理

语雀原生搜索 API 是关键词匹配，语义理解能力有限。本技能通过**同义词双重扩展策略**增强语义搜索：索引阶段扩展（覆盖常见同义词）+ 搜索阶段扩展（补漏）。

详细搜索流程 → [references/search_flow.md](references/search_flow.md)
详细索引构建流程 → [references/index_flow.md](references/index_flow.md)

## 配置初始化

**首次使用时，按以下流程初始化配置**：

1. 询问用户配置文件存放位置（如 `docs/yuque-config.json`）
2. 复制 `config.example.json`（模板）到该位置
3. **Token + 用户验证**（先于知识库配置）：
   - 引导用户填写 `token`
   - 调用 `GET /api/v2/hello` 测试连通性
   - 调用 `GET /api/v2/user` 确认账号正确 → 自动填写 `group` 字段（取 `login`）
   - Token 无效则阻断，引导重试后再继续
4. **知识库准备**（Token 验证通过后）：
   - 调用 `GET /api/v2/users/{login}/repos` 列出已有知识库
   - 逐一检查 `index_master_book`、`index_books[]`、`default_book` 的 `book_id` 对应的知识库是否存在
   - **自动创建缺失的知识库**：不存在的自动调用 `POST /api/v2/users/{login}/repos` 创建，命名规则：
     - 索引总库：`index-master`（slug: `index-master-{ts}`）
     - 索引子库：`index-sub-1`（slug: `index-sub-1-{ts}`）
     - 默认库：`default`（slug: `default-{ts}`）
   - 创建后回写 `book_id` 和 `namespace` 到配置文件
   - **注意**：若用户已有对应知识库想复用，优先使用现有库
5. **配置完整性验证**：
   - 确认 `index_master_book`、`index_books[0]`、`default_book` 的 `book_id` 均非 0
   - 确认 `namespace` 格式正确（`group/book_slug`）
   - 输出配置摘要供用户确认
6. 将配置路径记录到 `MEMORY.md` 的「语雀」章节

**后续使用时**：从 `MEMORY.md` 读取配置路径，加载配置文件。加载后也应执行快速连通性检查（`GET /api/v2/hello`）。

**配置字段**：

| 字段 | 说明 |
|------|------|
| `token` | 语雀 Token（需要读取和写入权限）|
| `group` | 用户名或团队名，用于搜索 scope 参数的前缀 |
| `index_master_book` | 索引总库（存放总文档，JSON 元信息） |
| `index_books` | 索引子库列表（存放子文档，具体索引内容） |
| `active_index_book` | 当前写入的子库索引（`index_books` 数组下标），满了自动创建下一库并更新此字段 |
| `default_book` | 创建文档默认库 |
| `index_state` | 索引状态存储位置：`"local"`（默认，路径 `~/.openclaw/workspace/utils/yuque/index_state.json`，可在初始化时自定义） / 自定义路径 / 语雀文档对象 |
| `synonym_map` | 同义词缓存路径：`"local"`（默认，路径 `~/.openclaw/workspace/utils/yuque/synonym_map.json`）/ 自定义路径 |
| `search_cache` | 搜索缓存路径：`"local"`（默认，路径 `~/.openclaw/workspace/utils/yuque/search_cache.json`）/ 自定义路径 |
| `cache_ttl_minutes` | 搜索缓存有效期，默认 30（分钟） |
| `dead_entries_threshold` | 死条目清理提示阈值，默认 10 |
| `segment_length` | 分段长度，默认 2000 |
| `candidates_limit` | 搜索候选数，默认 20 |
| `top_k` | Rerank 后取几篇全文，默认 5 |

**重要**：
- 语雀搜索 API 的 `scope` 参数只支持 `namespace` 格式（如 `yehuoshun/gi49zs`），不支持 `book_id`，因此配置中必须同时包含 `book_id` 和 `namespace`
- 配置文件路径由用户指定，存放在非 skill 目录内，避免发布或分享 skill 时意外泄露 Token

## 触发条件

当用户消息包含「语雀」关键词时触发，包括但不限于：

- 「在语雀搜索...」「问语雀...」「查我的语雀笔记...」→ AI 问答
- 「我的语雀信息」→ 用户信息
- 「我的语雀知识库」「列出知识库」「创建知识库」「更新知识库」「删除知识库」→ 知识库管理
- 「知识库有哪些文档」「列出文档」「文档详情」「看看那篇文档」「创建语雀文档」「更新语雀文档」「修改文档」「删除语雀文档」→ 文档管理
- 「我的语雀小记」「列出小记」「创建小记」「记一条小记」「更新小记」「删除小记」「恢复小记」→ 小记管理
- 「知识库目录」「目录结构」「移动文档到这个目录」→ 目录管理
- 「团队成员」「群组成员」「移出团队」→ 群组成员管理
- 「团队统计」「成员贡献」「数据统计」→ 统计数据
- 「构建语雀索引」「更新语雀索引」→ 索引管理
- 「重试失败的索引」→ 重试失败文档
- 「导出文档」「导出知识库」「下载语雀文档」→ 文档导出

## 工作流程

> 详细的字段说明和返回结构见 [references/api_reference.md](references/api_reference.md)

### 1. 用户信息

| 操作 | 方法 | 端点 |
|------|------|------|
| 获取当前用户 | `GET` | `/api/v2/user` |
| 连通性测试 | `GET` | `/api/v2/hello` |

### 2. 知识库管理

| 操作 | 方法 | 端点 |
|------|------|------|
| 列出知识库 | `GET` | `/api/v2/users/{login}/repos` |
| 获取详情 | `GET` | `/api/v2/repos/{book_id}` |
| 创建知识库 | `POST` | `/api/v2/users/{login}/repos` |
| 更新知识库 | `PUT` | `/api/v2/repos/{book_id}` |
| 删除知识库 | `DELETE` | `/api/v2/repos/{book_id}` |

**创建参数**：`name`（必填）、`slug`（必填）、`description`、`public`（0=私有/1=公开/2=团队内公开）

⚠️ **slug 必填**：语雀不再自动生成。格式 `[a-z0-9._-]`，生成规则：`{拼音缩写}-{时间戳}`，如 `javamst-1714473600`。

**删除流程**：先展示知识库信息（名称、文档数量）→ 询问确认 → 确认后执行（不可逆）

### 3. 文档管理

| 操作 | 方法 | 端点 |
|------|------|------|
| 列出文档 | `GET` | `/api/v2/repos/{book_id}/docs?offset=0&limit=100` |
| 获取详情 | `GET` | `/api/v2/repos/{book_id}/docs/{doc_id}?raw=1` |
| 创建文档 | `POST` | `/api/v2/repos/{book_id}/docs` |
| 更新文档 | `PUT` | `/api/v2/repos/{book_id}/docs/{doc_id}` |
| 删除文档 | `DELETE` | `/api/v2/repos/{book_id}/docs/{doc_id}` |

**创建参数**：`title`（必填）、`format`（默认 `markdown`）、`body`、`public`（默认 `0`）。`slug` 由语雀自动生成，不要手动指定。

⚠️ **创建文档后必须添加到目录**：语雀 API 创建文档后**不会自动加入目录**，必须调用目录 API 将文档添加到目录，否则文档不会显示在知识库目录中。

```http
PUT /api/v2/repos/{book_id}/toc
Content-Type: application/json

{"action": "appendNode", "action_mode": "sibling", "type": "DOC", "doc_ids": [文档ID]}
```

**完整流程**：创建文档 → 获取 doc_id → 调用目录 API 添加到目录 → 返回结果。

⚠️ **目录 API 失败处理**：若 `PUT /toc` 返回 4xx/5xx，等待 1s 后重试（最多 3 次）。3 次均失败则提示用户：「✅ 文档已创建，但添加到目录失败（{错误原因}），请在语雀中手动将文档拖入目标目录。」

**删除流程**：先展示文档信息（标题、知识库）→ 询问确认 → 确认后执行（不可逆）

### 4. 文档版本管理

| 操作 | 方法 | 端点 |
|------|------|------|
| 版本列表 | `GET` | `/api/v2/doc_versions?doc_id={doc_id}` |
| 版本详情 | `GET` | `/api/v2/doc_versions/{version_id}` |

### 5. 小记管理

| 操作 | 方法 | 端点 |
|------|------|------|
| 列出小记 | `GET` | `/api/v2/notes?page=1&limit=20&status=0` |
| 获取详情 | `GET` | `/api/v2/notes/{note_id}` |
| 创建小记 | `POST` | `/api/v2/notes` |
| 更新小记 | `PUT` | `/api/v2/notes/{note_id}` |
| 删除小记 | `PUT` | `/api/v2/notes/{note_id}`（status=9） |
| 恢复小记 | `PUT` | `/api/v2/notes/{note_id}`（status=0） |

**关键注意事项**：
- 列表返回 `{pin_notes, notes, has_more}` 三数组结构，展示时需合并
- `content` 是**嵌套对象**：取文本用 `note.content.source`，不是 `note.content` 直接当字符串
- 创建小记只返回 `note_url`，不返回 `id`。需获取 id 时通过列表匹配 slug
- 更新小记时 `source`、`html`、`abstract` 三个字段缺一不可
- **删除/恢复小记**：语雀没有 DELETE 端点，通过 PUT 设置 `status: 9`（删除）或 `status: 0`（恢复）实现软删除。必须先 GET 原小记再 PUT

### 6. 目录管理

| 操作 | 方法 | 端点 |
|------|------|------|
| 获取目录 | `GET` | `/api/v2/repos/{book_id}/toc` |
| 更新目录 | `PUT` | `/api/v2/repos/{book_id}/toc` |

**更新参数**：`action`（appendNode/prependNode/editNode/removeNode）、`action_mode`（sibling/child）、`type`（DOC/TITLE/LINK）、`doc_ids`、`target_uuid`

### 7. 群组成员管理

> ⚠️ **未测试**：需要团队/群组环境，API 可能存在问题。

| 操作 | 方法 | 端点 |
|------|------|------|
| 列出成员 | `GET` | `/api/v2/groups/{login}/users` |
| 更新角色 | `PUT` | `/api/v2/groups/{login}/users/{user_id}` |
| 移除成员 | `DELETE` | `/api/v2/groups/{login}/users/{user_id}` |

**role 取值**：0=管理员 / 1=成员 / 2=只读成员。移除成员不可逆，需先确认。

### 8. 统计数据

> ⚠️ **未测试且需额外权限**：需要 `statistic:read` 权限。

| 操作 | 方法 | 端点 |
|------|------|------|
| 团队统计 | `GET` | `/api/v2/groups/{login}/statistics` |
| 成员统计 | `GET` | `/api/v2/groups/{login}/statistics/members` |
| 知识库统计 | `GET` | `/api/v2/groups/{login}/statistics/books` |
| 文档统计 | `GET` | `/api/v2/groups/{login}/statistics/docs` |

### 9. AI 问答（语义增强搜索）

**触发**：「在语雀搜索...」「问语雀...」「查我的语雀笔记」

**流程概要**：
1. LLM 扩展关键词 + 实体识别（`@实体:xxx`）+ 同义词
2. 通过 `/api/v2/search?q={关键词}&type=doc&scope={ns}` 搜索索引库
3. 合并子文档条目 → 按源文档ID去重
4. **实体快速通道**：若条目包含实体/关系数据且能直接回答 → 跳过读原文，LLM 用关系数据生成回答
5. 索引命中为 0 时降级兜底 → 语雀原生搜索
6. 粗排（候选 > 10 时关键词匹配取前 10）
7. LLM Rerank（按语义相关性排序） → 取 Top K
8. 并行获取全文 → 分段 → 注入实体/关系上下文 → LLM 生成回答
9. 命中 404/410 的文档标记为死条目，末尾提示清理

**完整详细流程** → [references/search_flow.md](references/search_flow.md)

### 10. 索引管理

**触发**：「构建语雀索引」「更新语雀索引」

> 包含关键词提取、实体/关系提取、同义词扩展、无意义文档过滤、子库分片。
> 实体/关系数据与关键词一同存入索引条目，搜索时可跳过读原文直接回答。

| 模式 | 触发词 | 每批数量 | 行为 |
|------|--------|----------|------|
| **自动**（默认） | 「构建索引」 | 10 篇/批 | 自动连续跑，正文过短自动降级标题提取，不阻塞 |
| **手动** | 「手动构建索引，每批30篇」 | 用户指定（≤100） | 每批暂停，等「继续」，同样支持降级 |

**保护**：知识库 > 2000 篇文档时，自动模式拒绝，提示用手动模式分批构建。

**完整构建流程、状态机、同义词策略、无意义文档过滤** → [references/index_flow.md](references/index_flow.md)

### 11. 重试失败索引

**触发**：「重试失败的索引」「重试索引失败文档」

读取状态文件中的 `failed_docs`，逐篇走单文档增量索引流程，成功后清除。

**完整流程** → [references/index_flow.md#重试失败的索引](references/index_flow.md)

### 12. 单文档增量索引

**触发**：「更新《XXX》的索引」「重新索引这篇文档」「增量索引文档 205935051」

**核心思路**：复用全量构建中"处理一篇文档"的完整逻辑，上下文限定为单篇。

**流程**：
1. 根据标题或 doc_id 定位源文档
2. 获取文档最新内容
3. 获取旧关键词列表（从现有索引条目读取）
4. LLM 提取新关键词 + 同义词展开（与全量构建相同）
5. 对比新旧关键词 → 过时的清理、保留的原地替换、新增的创建
6. 汇报变更摘要

**完整详细流程** → [references/index_flow.md#单文档增量索引](references/index_flow.md)

### 13. 文档导出

**触发**：「导出这篇文档」「导出知识库」「下载语雀文档到本地」

**单篇导出**：
1. 用户指定文档（标题或 doc_id）和保存路径
2. `GET /api/v2/repos/{book_id}/docs/{doc_id}?raw=1` 获取 markdown 原文
3. 保存为 `{标题}.md`

**批量导出**：
1. 用户指定知识库 → 按 `offset`/`limit` 分页遍历文档列表（每页 100 篇）
2. 逐篇获取正文，保存到指定目录
3. 默认跳过已存在的文件（可强制覆盖）
4. 每导出一页汇报进度：「第 N 页 / 共 M 页，已导出 X 篇」

**限制**：
- ⚠️ 附件和图片不会自动下载，导出的 markdown 中仅保留原始链接
- 只能导出 markdown/lake 格式文档的正文内容

> - **调用方式**：使用 Python 标准库（urllib.request、json、concurrent.futures），简单请求也可用 curl + exec。禁止 pip install。
> - 语雀 slug 检索有 bug，大部分 API 使用 `book_id` 和 `doc_id`
> - 搜索 API 的 `scope` 参数只支持 `namespace` 格式（如 `yehuoshun/gi49zs`），不支持 `book_id`
> - 批量搜索/获取时利用 `ThreadPoolExecutor` 并行请求（并发数 ≤ 5），大幅提升速度
>
> 完整的 API 端点、参数、限制及错误处理见 **[references/api_reference.md](references/api_reference.md)**。

## 最佳实践

1. **索引更新**：源文档变更后手动触发索引更新
2. **分段处理**：长文档分段喂给 LLM，避免超出上下文
3. **来源标注**：回答中始终标注文档来源和链接
4. **scope 使用**：搜索时通过 scope 参数限定知识库范围

## 错误处理规范

| 错误码 | 说明 | 处理方式 |
|--------|------|----------|
| 400 | 请求参数错误 | 检查参数格式 |
| 401 | Token 无效或已过期 | 引导用户到语雀设置重新生成 Token |
| 403 | 权限不足 | 说明缺少的权限 |
| 404 | 资源不存在 | 检查 ID 是否正确 |
| 410 | 资源已删除 | 资源已被删除 |
| 429 | 请求过于频繁 | 检查 `X-RateLimit-Remaining`：`=0` 触及 5000/h → 立即暂停，保存进度，通知用户整点后重新触发；`>0` 触及 100/s → 等待 1s 重试（最多 3 次） |
| 500 | 服务器内部错误 | 稍后重试 |
| 502/503/504 | 网关错误 | 稍后重试 |

## 删除操作确认规范

根据删除的可恢复性，采用不同级别的确认流程：

### 硬删除（不可逆）

**知识库删除**：
```
⚠️ 即将删除知识库《XXX》，包含 N 篇文档。
此操作不可恢复，确认删除吗？
```

**文档删除**：
```
⚠️ 即将删除文档《XXX》。
此操作不可恢复，确认删除吗？
```

### 软删除（可恢复）

**小记删除**：
```
📝 即将把小记移入回收站。
确认删除吗？（可从回收站恢复）
```

删除后提示：
```
✅ 小记已移入回收站。如需恢复，请说「恢复这条小记」。
```

**群组成员移除**：
```
⚠️ 即将将成员移出群组。
此操作不可恢复，确认移除吗？
```

## 故障排查

遇到问题时，参考 **[references/api_reference.md#故障排查](references/api_reference.md)**。

常见问题快速入口：
- **Token 问题**：401/403 错误 → 检查 token 有效性和权限
- **搜索无结果**：检查索引是否构建、scope 是否正确
- **索引构建失败**：检查状态文件、速率限制
- **API 调用异常**：参考错误码处理表
