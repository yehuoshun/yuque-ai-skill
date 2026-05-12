# 索引构建详细流程

> 本文档包含索引构建的完整流程、状态机、同义词策略、无意义文档过滤和子文档分片逻辑。
> 概述和触发条件见 SKILL.md → 第 10 节。
> API 调用使用 [api_helper.py](api_helper.py) 中的通用封装（`yuque_get`、`yuque_put`、`fetch_all_docs` 等）。

## 构建模式

| 模式 | 触发词 | 每批数量 | 行为 |
|------|--------|----------|------|
| **自动**（默认） | 「构建索引」 | 10 篇/批 | 自动连续跑，批间稍作停顿 |
| **手动** | 「手动构建索引，每批30篇」 | 用户指定（≤100） | 每批结束后暂停，等用户说「继续」 |

## 知识库规模限制

- 知识库文档总数 ≤ 2000：手动/自动均可
- 知识库文档总数 > 2000：自动模式**拒绝**，提示用户改用手动模式分批构建

```
⚠️ 该知识库共有 2,356 篇文档，超过自动构建上限（2000 篇）。

建议使用手动模式分批构建，例如：
- 「手动构建索引，每批10篇」
```

## 状态文件

位置由配置文件的 `index_state` 字段决定：

- `"local"`（默认）：`~/.openclaw/workspace/utils/yuque/index_state.json`
- `"/path/to/state.json"`：自定义本地路径
- `{"type":"yuque","book_id":xxx,"doc_id":yyy}`：语雀文档（需用户手动创建）

```json
{
  "source_book_id": null,
  "source_namespace": "",
  "mode": "auto",
  "batch_size": 10,
  "current_batch": 1,
  "total_docs": 0,
  "indexed_docs": 0,
  "skipped_docs": [],
  "failed_docs": [],
  "dead_entries": [],
  "last_indexed_doc_id": null,
  "last_updated": null,
  "status": "idle",
  "rate_limit": {
    "limit": 5000,
    "remaining": null,
    "last_checked": null
  }
}
```

| 字段 | 说明 |
|------|------|
| `mode` | `"auto"` / `"manual"` |
| `batch_size` | 每批处理文档数（自动=10，手动=用户指定，≤100） |
| `current_batch` | 当前批次号 |
| `total_docs` | 源知识库文档总数 |
| `indexed_docs` | 已索引文档数（仅进度展示，不记录每篇详情） |
| `skipped_docs` | 待确认/已跳过的文档列表（无意义/异常文档） |
| `title_fallback_docs` | 标题降级索引的文档列表 |
| `failed_docs` | 失败文档列表（API 错误、超时等） |
| `dead_entries` | 死条目列表（源文档已删除，待清理的索引条目） |
| `last_indexed_doc_id` | 最后索引的文档 ID（断点续传锚点） |
| `status` | `idle` / `in_progress` / `awaiting_confirmation` / `done` / `failed` |
| `rate_limit.limit` | 速率限制总量（默认 5000） |
| `rate_limit.remaining` | 剩余可用次数（从响应头 `X-RateLimit-Remaining` 更新） |
| `rate_limit.last_checked` | 上次检查时间 |

**dead_entries 结构**：

```json
{
  "doc_id": 205935051,
  "title": "Python 安装指南",
  "url": "https://www.yuque.com/yehuoshun/wwqac0/abc123",
  "keywords": ["Python", "py", "python3", "py3", "安装", "install", "环境配置", "环境搭建", "pip"],
  "first_seen": "2026-04-30T16:00:00+08:00"
}
```

**积累阈值**：`dead_entries` 条数超过配置文件 `dead_entries_threshold`（默认 10）时，下次搜索在回答末尾提示：「⚠️ 已积累 N 条死索引条目，回复「清理死索引」一键移除。」

**failed_docs 结构**：

```json
{
  "doc_id": 123456,
  "title": "文档标题",
  "url": "https://www.yuque.com/xxx/xxx",
  "error": "API 429: Rate limited",
  "retries": 2,
  "timestamp": "2026-04-26T15:10:00+08:00"
}
```

## AI 协调的完整流程

1. 确认源知识库（用户指定或配置），确定模式（auto/manual）和批大小
2. 获取源知识库文档总数，若自动模式且 > 2000 则拒绝并提示用手动模式
3. 读取状态文件，根据 `status` 决定行为：
   - `idle` / `done` / `failed` → 开始新构建
   - `in_progress` → 从 `last_indexed_doc_id` 后续文档继续（断点续传）
   - `awaiting_confirmation` → 跳过构建，直接展示 `skipped_docs` 等用户确认
4. 分页获取源知识库全部文档列表（`offset`/`limit`，每次 100 篇，循环直到返回数 < 100），按 API 返回原序排列，更新 `total_docs`
5. 设置 `status=in_progress`，写入状态文件
6. 按批处理：
   - 取当前批次文档（从断点位置开始）
   - 遍历每篇：
     - 获取文档内容
     - **无意义文档检测**（详见下方「无意义文档过滤」）
     - 若有意义，LLM 分析提取关键词及其同义词（详见下方「同义词扩展策略」）
     - 对每个关键词（含同义词）：
       - 在 `index_master_book` 中搜索 `[索引] {关键词}` 总文档
       - 若不存在，创建总文档和第一个子文档 `[索引] {关键词} (1)`（子文档存放在 `index_books[active_index_book]` 指向的库），**创建后自动调用 `PUT /toc` 将文档加入目录**（`appendNode/sibling`）
       - 若存在，读取总文档的 `current_doc_id` 和 `current_size`
       - 若 `current_size` 接近 200kb（如超过 180kb），新建子文档并更新总文档
       - 否则追加到当前子文档并更新 `current_size`
     - **每索完一篇文档，立即更新状态文件**（`indexed_docs`、`last_indexed_doc_id`、`last_updated`）和**同义词缓存**（见下方「同义词缓存」小节）
   - 一批完成后：
     - **检查索引子库容量**：获取 `index_books[active_index_book]` 的 `items_count`，若 ≥ 4000 则**自动创建新子库**：`POST /api/v2/users/{login}/repos` → `index-sub-{N+1}-{ts}` → 追加到 `index_books` → `active_index_book` 指向新库；若 `items_count < 4000` 跳过
     - 若为**自动模式**且还有未处理文档，自动继续下一批
     - 若为**手动模式**，暂停并汇报：「第 N 批完成（M/Total），回复「继续」开始下一批」
   - **触及 5000/h 配额限制**（`RuntimeError` 包含「小时配额耗尽」）：立即暂停，更新状态文件（`status=in_progress`，`last_indexed_doc_id` 已记录），汇报用户：「⏳ 小时配额耗尽，已保存进度。整点后回复「继续构建索引」即可从断点续传。」
7. 全部遍历完成后，检查 `skipped_docs` 中是否有 `user_decision=null` 的项
   - 如果有，设置 `status=awaiting_confirmation`，汇报给用户确认（见下方「待确认汇报格式」）
   - 用户确认后，对选择「索引」的文档补建索引，更新状态文件
8. 全部完成，设置 `status=done`
9. **TOC 目录完整性校验**（构建完成后强制执行）：
   - 对 `index_master_book` 和所有 `index_books` 逐库执行 `verify_toc_integrity()`（auto_fix=True）
   - 汇报：「🔍 TOC 校验：总库 X/X，子库 Y/Y」
   - 有缺失自动修复，修复后再次验证
   - 仍有缺失则提示用户：「⚠️ {N} 篇文档未能加入目录，请手动检查」
   - **目的**：防止 POST 创建文档后 TOC 调用静默失败导致索引条目不可见

## 子库选择策略

- 配置文件 `active_index_book` 指向当前写入的子库（`index_books` 数组下标）
- 新建子文档时始终使用 `index_books[active_index_book]`
- 每批构建完成后检查该库的 `items_count`：≥ 4000 时自动创建新子库并更新 `active_index_book`
- 无需遍历所有子库查容量，配置即状态
- 总文档记录子文档所在库的 `book_id` 和 `namespace`

## 内容过短降级策略

当文档正文过短（< 20 字，常见于视频课件占位符），**不跳过**，改为降级处理：仅从标题提取关键词。

### 触发条件
- `is_meaningless()` 返回 `reason="内容过短"`
- 标题非空

### 降级流程
1. 跳过正文，仅用标题调用 LLM 提取关键词（content 字段留空）
2. 后续流程不变：同义词展开 → 创建/更新索引条目
3. 在状态文件中记录为 `title_fallback_docs`（区别于正文索引）
4. 若标题也提取不到关键词，标记为 `skipped_docs`（reason="标题无关键词"）

### 状态文件新增字段

| `title_fallback_docs` | 标题降级索引的文档列表 |

```json
{
  "title_fallback_docs": [
    {"id": 228542066, "title": "Python超详细安装教程.mp4"}
  ]
}
```

## 关键词过滤策略

**第一层：硬性规则过滤（自动跳过）**

| 类型 | 规则 | 示例 | 例外 |
|------|------|------|------|
| 纯数字 | `^\d+$` | 123、999 | 无 |
| 版本号/章节号 | `^v?\d+(\.\d+)+$` | 1.2、v1.0、1.2.3、99.8 | 无 |
| 问候语 | 精确匹配 | 早上好、晚上好、早安、晚安、你好、您好、谢谢、好的 | 无 |
| 时间词 | 精确匹配 | 今天、明天、昨天、今年、去年、上周、下周 | 无 |
| 通用词 | 精确匹配 | test、测试、临时、草稿、新建文档、无标题 | 无 |

**@实体: 关键词豁免**：以 `@实体:` 开头的关键词直接通过所有过滤规则，不参与数量控制（独立计数）。

**第二层：语义判断兜底**

对通过第一层的关键词，问自己：
> 这个关键词能帮助用户找到特定知识点吗？

- 能 → 保留
- 不能或模糊 → 过滤

**第三层：数量控制**

- 每篇文档 3-8 个关键词
- 宁缺毋滥：质量优先于数量
- 如果过滤后为空，检查文档是否有索引价值

**白名单（技术术语保留）**

即使匹配数字规则，以下类型保留：
- 语言/框架版本：JDK 8、ES6、Python 3、Vue 3
- 技术术语：HTTP/2、IPv6、5G、4K、MP3、JSON
- 有语义编号：Windows 11、iPhone 15

## 关键词 + 实体/关系提取（索引阶段）

LLM 对每篇文档**一次调用**同时提取关键词、实体和关系：

```
源文档「亿级流量系统架构设计」（节选）
> 张三是亿级流量系统的架构师。系统采用 Redis 做缓存层，QPS 峰值 50 万。张三是 Redis 专家，负责核心缓存模块。

LLM 提取结果：
{
  "keywords": [
    {"keyword": "亿级流量", "synonyms": ["高并发"]},
    {"keyword": "Redis", "synonyms": ["缓存"]},
    {"keyword": "QPS", "synonyms": ["吞吐量"]},
    {"keyword": "@实体:张三", "synonyms": []},
    {"keyword": "@实体:Redis", "synonyms": []},
    {"keyword": "@实体:亿级流量系统", "synonyms": []}
  ],
  "entities": [
    {"name": "张三", "type": "人物"},
    {"name": "Redis", "type": "技术/中间件"},
    {"name": "亿级流量系统", "type": "项目"}
  ],
  "relations": [
    "张三→负责→亿级流量系统",
    "Redis→用于→亿级流量系统",
    "Redis→达到→50万QPS"
  ]
}

展开后的索引词列表：
  亿级流量, 高并发, Redis, 缓存, QPS, 吞吐量, @实体:张三, @实体:Redis, @实体:亿级流量系统
```

**实体/关系提取规则**：
- 实体类型：人物、技术/中间件、项目/产品、公司/组织、概念/术语
- 关系格式：`主体→谓语→客体`，从文档正文中提取明确陈述的关系，不臆造
- `@实体:xxx` 作为特殊关键词存入索引，前缀用于搜索端识别实体查询
- 实体的同义词（如 Redis → 缓存）仍走普通关键词路径
- 无明确实体或关系的文档，`entities`/`relations` 为空数组，不影响索引构建

### 同义词类型

- 缩写/简写：Python → py, JavaScript → js, Kubernetes → k8s
- 中英文互译：安装 → install, 配置 → config, 线程 → thread
- 近义词：安装 → 部署 → 环境搭建, 数据库 → DB → 存储
- 大小写变体：Python → python, JVM → jvm
- 专业术语变体：线程池 → ThreadPool, 并发 → concurrency

## 同义词缓存 (synonym_map)

索引构建时 LLM 提取的同义词关系写入本地缓存文件，搜索时直接查表，避免每次调 LLM。

**文件位置**：配置文件 `synonym_map` 指定（默认 `~/.openclaw/workspace/utils/yuque/synonym_map.json`）

**结构**：键 = 索引词，值 = 同义词列表（去重）

```json
{
  "py": ["Python", "python3", "py3"],
  "安装": ["install", "环境配置", "环境搭建"],
  "Python": ["py", "python3", "py3"]
}
```

**写入时机**：索引构建每完成一篇文档后，将 LLM 提取的同义词关系追加到缓存文件（按 key 合并去重）。

**搜索使用**：搜索时先读缓存查表 → 命中直接取、未命中的生僻词调 LLM 补充（详见 search_flow.md）。

## 无意义文档过滤

在索引构建过程中，对每篇文档进行无意义判定。**判定后不阻塞**：继续遍历后续文档，全部遍历结束后汇总汇报给用户确认。

### 判定维度

| 维度 | 无意义特征 |
|------|-----------|
| 标题 | 「无标题」「新建文档」「草稿」「test」「临时」「废稿」等 |
| 长度 | 正文 < 20 字 | **降级处理**：从标题提取关键词，不跳过 |
| 内容质量 | 纯碎碎念、无结构、无信息量 |
| 内容性质 | 明显是个人日记/随笔/备忘，不含可检索的知识点 |

满足任一维度即标记为待确认。记录到状态文件的 `skipped_docs`：

```json
{
  "doc_id": 123456,
  "title": "无标题",
  "url": "https://www.yuque.com/yehuoshun/wwqac0/abc123",
  "reason": "标题为「无标题」+ 正文过短",
  "user_decision": null
}
```

**user_decision 取值**：`null`=待确认 / `"skip"`=确认跳过 / `"index"`=确认索引

### 待确认汇报格式

```markdown
🏷️ 索引构建中，发现以下文档可能无意义，请确认是否跳过：

1. 📄 **无标题** — 正文仅 12 字
   链接：https://www.yuque.com/yehuoshun/wwqac0/abc123

2. 📄 **随手记** — 个人随笔，无可检索知识点
   链接：https://www.yuque.com/yehuoshun/wwqac0/def456

3. 📄 **test123** — 标题含 test + 正文仅 8 字
   链接：https://www.yuque.com/yehuoshun/wwqac0/ghi789

——
共 3 篇待确认。回复「全部跳过」/「全部索引」/「跳过1和3，索引2」
```

## 总文档格式（存放在 `index_master_book`）

标题：`[索引] {关键词}`

正文使用代码块包裹 JSON（避免格式问题）：

```
{
  "keyword": "线程池",
  "sub_docs": [
    {
      "title": "[索引] 线程池 (1)",
      "doc_id": 111111,
      "book_id": 456,
      "namespace": "yehuoshun/index-1"
    },
    {
      "title": "[索引] 线程池 (2)",
      "doc_id": 222222,
      "book_id": 456,
      "namespace": "yehuoshun/index-1"
    }
  ],
  "current_doc_id": 222222,
  "current_doc_title": "[索引] 线程池 (2)",
  "current_book_id": 456,
  "current_namespace": "yehuoshun/index-1",
  "current_size": 460000,
  "total_count": 630
}
```

## 子文档格式（存放在 `index_books`）

```markdown
# [索引] 线程池 (1)

本索引包含所有与「线程池」相关的文档。

---

## 文档索引

### Java JUC 线程池详解
- **摘要**: 深入分析 Java 线程池的实现原理...
- **关键词**: Java, JUC, 线程池, ThreadPoolExecutor
- **实体**: @实体:Java(语言), @实体:ThreadPoolExecutor(技术)
- **关系**: Java→包含→ThreadPoolExecutor
- **源文档ID**: 205935051
- **源知识库ID**: 60455024
- **Namespace**: yehuoshun/gi49zs
- **Slug**: abc123
- **链接**: https://www.yuque.com/yehuoshun/gi49zs/abc123

### Python 线程池实践
- **摘要**: Python concurrent.futures 线程池使用...
- **关键词**: Python, 线程池, concurrent.futures
- **实体**: @实体:Python(语言)
- **关系**: --
- **源文档ID**: 205935052
- **源知识库ID**: 60455024
- **Namespace**: yehuoshun/gi49zs
- **Slug**: def456
- **链接**: https://www.yuque.com/yehuoshun/gi49zs/def456

...（更多含「线程池」关键词的文档）
```

## 200kb 限制处理

- 子文档上限 200kb（约 50000 字，实测单次 POST 耗时 <3s）
- 每个索引条目约 500-1000 字符
- 单个子文档最多 100-200 个条目
- 接近 200kb（如超过 180kb）时，新建子文档并更新总文档

### 示例

```
源文档: Python安装指南
关键词提取结果:
  - Python (同义词: py, python3, py3)
  - 安装 (同义词: install, 环境配置, 环境搭建)
  - pip (无同义词)

→ 展开索引词: Python, py, python3, py3, 安装, install, 环境配置, 环境搭建, pip

→ 创建/更新索引文档:
  [索引] Python ← 追加条目
  [索引] py ← 追加条目
  [索引] python3 ← 追加条目
  [索引] py3 ← 追加条目
  [索引] 安装 ← 追加条目
  [索引] install ← 追加条目
  [索引] 环境配置 ← 追加条目
  [索引] 环境搭建 ← 追加条目
  [索引] pip ← 追加条目
```

## 单文档增量索引

> 复用全量构建中"处理一篇文档"的完整逻辑，上下文限定为单篇。不引入新状态、不建反向索引。

### 触发方式

| 触发词 | 行为 |
|--------|------|
| 「更新《XXX》的索引」 | 按标题匹配源文档 → 增量更新 |
| 「重新索引这篇文档」 | 同上 |
| 「增量索引文档 205935051」 | 按 doc_id 直接定位 |

### 完整流程

```
1. 定位源文档
   → 按标题搜索源知识库，或直接用 doc_id 获取

2. 获取文档最新内容
   → GET /api/v2/repos/{book_id}/docs/{doc_id}?raw=1

3. 获取旧版关键词：
   → LLM 从文档内容提取 3-5 个试探性关键词
   → 用试探词搜索 `index_master_book`，找到包含该 `源文档ID` 的索引条目
   → 从条目的「关键词」字段读取完整旧关键词列表
   → 若找不到任何条目（从未索引过），旧关键词为空集

4. LLM 提取新关键词 + 同义词展开
   → 与全量构建完全相同的逻辑（参考上方「同义词扩展策略」）

5. 对比新旧关键词：
   新增 = 新关键词 - 旧关键词
   保留 = 新关键词 ∩ 旧关键词
   过时 = 旧关键词 - 新关键词

6. 对过时关键词：
   → 搜索 index_master_book → 找 [索引] {过时词} 总文档
   → 读对应子文档 → 删除匹配该 源文档ID 的条目
   → PUT 更新子文档

7. 对新增 + 保留关键词（含同义词和 @实体: 词）：
   a. 搜索 index_master_book → 找 [索引] {词} 总文档
   b. 若存在：
      → 读当前子文档
      → 找到 源文档ID 匹配的条目 → 替换（更新摘要、关键词、实体、关系等）
      → 更新 current_size
   c. 若不存在：
      → 创建总文档 + 第一个子文档（与全量构建相同）

8. 汇报结果：
   ✅ 已更新《XXX》的索引
   新增关键词：A, B
   新增实体：X(类型), Y(类型)
   新增关系：X→谓语→Y
   保留关键词：C, D
   清理过时关键词：E, F（已从对应子文档删除）
```

### 边界情况

| 场景 | 处理 |
|------|------|
| 文档从未索引过 | 子文档中找不到匹配的 `源文档ID` → 按新建处理，创建条目 |
| 旧关键词不再出现 | 增量更新时主动删除：对比新旧关键词，过时关键词对应的索引条目立即清理 |
| 子文档空间不足 | 与全量构建相同：超 180kb 时自动分片，新建子文档并更新总文档 |
| 文档已被删除 | 走「死条目清理」逻辑，从所有子文档中移除该 `源文档ID` 的条目 |

### 与全量构建的对比

| | 全量构建 | 增量索引 |
|------|----------|-----------|
| 处理范围 | 知识库所有文档 | 单篇文档 |
| 状态文件 | 更新 `index_state.json` | 不更新状态文件 |
| 无意义检测 | 有 | 无（用户指定了要索引的文档） |
| 核心逻辑 | **完全相同** | 复用的就是它 |

### 设计原则

不引入 `doc_index_map` 或任何反向索引——子文档里已经有 `源文档ID`，直接匹配即可。增量索引 = 全量构建的单文档特化版，不需要额外的数据结构。

## 重试失败的索引

**触发**：「重试失败的索引」「重试索引失败文档」

**流程**：

1. 读取状态文件 → 获取 `failed_docs` 列表
2. 若 `failed_docs` 为空 → 汇报「✅ 没有失败的索引文档」
3. 若有失败记录：
   - 展示失败列表（doc_id、标题、失败原因）
   - 逐篇执行单文档增量索引（复用上方「单文档增量索引」完整流程）
   - **每成功一篇立即从 `failed_docs` 移除并更新状态文件**（与主构建一致，防止中断丢失进度）
   - 失败则保留，`retries +1` 并记录新原因；`retries ≥ 3` 时标记为永久失败，不再参与后续重试，汇报时单独列出建议手动处理
4. 全部处理完毕 → 汇报结果：
   ```
   ✅ 重试完成：成功 N 篇，仍有 M 篇失败
   失败文档：
   - 《XXX》(doc: 123456)：429 速率限制（已重试 2 次）
   ```
