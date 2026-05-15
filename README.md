# 语雀 AI Skill

> 语雀全功能 AI Agent 技能 —— 知识库管理、文档 CRUD、小记管理、目录编排、批量导出、两级索引知识库问答（纯 LLM + 语雀 API，零外部依赖），一切通过自然语言驱动。

> 📄 **Skill 规范文档**：[SKILL.md](./SKILL.md) — AI Agent 执行指南，所有功能细节以该文件为准。

**核心理念：用语雀 API 替代手工操作，AI 替你调用。**

[![Release](https://img.shields.io/github/v/release/yehuoshun/yuque-ai-skill?label=release)](https://github.com/yehuoshun/yuque-ai-skill/releases)
[![License](https://img.shields.io/github/license/yehuoshun/yuque-ai-skill)](./LICENSE)
[![SKILL.md](https://img.shields.io/badge/SKILL.md-执行规范-green)](./SKILL.md)

📖 **AI Agent 执行规范 → [SKILL.md](./SKILL.md)**

## 目录

- [功能特性](#功能特性)
- [前置条件](#前置条件)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [使用方式](#使用方式)
- [项目结构](#项目结构)
- [API 参考](#api-参考)
- [License](#license)

## 功能特性

| 功能 | 说明 |
|------|------|
| 📚 **知识库管理** | 列表/创建/更新/删除知识库，slug 格式自动校验 |
| 📝 **文档管理** | CRUD 全支持，创建后自动挂载目录，硬删除需二次确认 |
| 📋 **小记管理** | 创建/列表/详情/更新/软删除/恢复，自动处理嵌套 content 结构 |
| 📂 **目录编排** | TOC API 增删改，sibling/child 双模式挂载 |
| 📤 **文档导出** | 单篇/批量导出 Markdown，TOC 还原目录树，图片本地化，增量导出 |
| 🔍 **搜索** | 全文搜索，namespace 范围限定，结果高亮 |
| 🚦 **双轨并发** | API 轨动态浮动（上限 10），LLM 轨内存公式驱动，429 自动退避 |
| 🔗 **跨库引用** | 自动解析 namespace 与 book_id 互转 |
| 🛡️ **错误处理** | 401/403/404/429/5xx 分级处理，Token 失效自动提示更新 |
| 📊 **任务汇总** | 导出完成后通知（成功/失败/跳过/路径/耗时） |
| 🧠 **知识库问答** | 两级索引（总库路由 → 子库关键词），多路并发搜索，同义词缓存 + 路由缓存，LLM 生成答案 + 引用出处 |

## 前置条件

| 步骤 | 检查项 | 说明 |
|------|--------|------|
| 1 | **语雀 Token** | 需 `doc:read` `doc:write` `repo:read` `repo:write` 权限 |
| 2 | **配置文件** | skill 目录下 `config/yuque-config.json`，首次从 `config/yuque-config.example.json` 复制 |
| 3 | **Python 3.8+** | 纯标准库（`urllib.request`），无需 pip install |

## 快速开始

**方式一：下载 Zip（推荐）**

```bash
wget https://github.com/yehuoshun/yuque-ai-skill/releases/latest/download/yuque-ai-skill.zip
unzip yuque-ai-skill.zip
cd yuque-ai-skill
```

**方式二：Git Clone**

```bash
git clone https://github.com/yehuoshun/yuque-ai-skill.git
cd yuque-ai-skill
```

### 1. 环境

Python 3.8+，无外部依赖。

### 2. 配置

复制示例配置并填写：

```bash
cp config/yuque-config.example.json config/yuque-config.json
```

编辑 `config/yuque-config.json`：

```json
{
  "token": "你的语雀 API Token",
  "group": "你的语雀用户名",
  "default_book": {
    "book_id": 0,
    "namespace": "用户名/book_slug"
  }
}
```

> Token 在[语雀开放平台](https://www.yuque.com/settings/tokens)创建，需 `doc:read` `doc:write` `repo:read` `repo:write` 权限。

### 3. 使用

直接对 AI Agent 说：

- 「列出我的知识库」
- 「在语雀搜索 Python 教程」
- 「创建一篇文档到 XXX」
- 「导出《XXX》知识库」
- 「创建一条小记 今天学了 RAG」

## 配置说明

### 语雀 Token

在 [语雀开放平台](https://www.yuque.com/settings/tokens) 创建 Token，需勾选：

- `doc:read` — 读取文档
- `doc:write` — 创建/修改文档
- `repo:read` — 读取知识库
- `repo:write` — 修改知识库目录

### 配置文件结构

```json
{
  "token": "语雀 API Token",
  "group": "用户名",
  "default_book": {
    "book_id": 0,
    "namespace": ""
  }
}
```

| 字段 | 说明 |
|------|------|
| `token` | 语雀 API Token（必填） |
| `group` | 语雀用户名/login（必填） |
| `default_book.book_id` | 默认知识库 ID，不指定目标时自动使用 |
| `default_book.namespace` | 默认知识库 namespace，如 `yehuoshun/my-book` |

### 速率限制

| 限制项 | 上限 |
|--------|------|
| API QPS | 100/s |
| 每小时请求 | 5000/h |
| 单知识库文档数 | 5000 |

## 使用方式

由 AI Agent 驱动。提到「语雀」「小记」「知识库」即自动触发。

### 典型命令

| 场景 | 示例 |
|------|------|
| 知识库管理 | 「列出我的知识库」「创建一个知识库叫 XXX」 |
| 文档操作 | 「在 XXX 知识库创建一篇文档」「更新《XXX》的内容」 |
| 小记 | 「写一条小记」「查看今天的小记」「恢复那条删除的小记」 |
| 搜索 | 「在语雀搜索 XXX」 |
| 问答 | 「Docker 容器之间怎么通信」「Python 怎么处理异常」 |
| 导出 | 「导出《XXX》知识库」「批量导出所有文档」 |

### AI Agent 自动处理

1. 检查 Token 有效性（`GET /hello`）→ 验证知识库存在性
2. 按操作类型选择 API / LLM 轨并发策略
3. 速率自动控制，429 区分 QPS 突发 vs 小时配额耗尽
4. 操作完成自动校验结果（TOC 挂载成功后验证文档可见）

## 项目结构

```
yuque-ai-skill/
├── SKILL.md              # Skill 规范文档（AI Agent 执行指南）
├── README.md             # 本文件
├── LICENSE
├── config/               # 配置文件目录
│   └── yuque-config.json # 默认配置路径（可自定义）
├── references/
│   └── api_reference.md  # 语雀 OpenAPI 完整参考
└── .github/
    └── workflows/
        └── dingtalk-notify.yml  # CI：钉钉通知
```

## 知识库问答

基于两级索引的纯 LLM + 语雀 API 方案，不依赖嵌入模型、向量数据库或第三方搜索 API。

### 架构

```
用户提问 → 同义词缓存（子串匹配）→ 路由缓存 → 并发搜索索引子库 → 精准标题匹配 → 读索引全文 → LLM 生成答案 + 引用
```

### 缓存体系

| 缓存层 | 存储 | TTL |
|--------|------|-----|
| 同义词 | synonym_cache.json | 永久 |
| 路由 | SQLite | 1天 |
| 搜索结果 | SQLite | 10分钟 |
| 索引文档内容 | 不缓存 | — |

### 搜索降级

```
正常路径 → LLM 放大搜索词 → 语雀全文搜索 → 「未找到相关内容」
```

## API 参考

语雀 OpenAPI 完整接口参考见 [references/api_reference.md](./references/api_reference.md)。

基地址：`https://www.yuque.com/api/v2`

核心端点速查：

| 模块 | 端点 | 说明 |
|------|------|------|
| Hello | `GET /hello` | 验证 Token |
| 知识库 | `GET/POST /users/{login}/repos` | 列表/创建 |
| 知识库 | `GET/PUT/DELETE /repos/{id_or_namespace}` | 详情/更新/删除 |
| 文档 | `GET /repos/{book_id}/docs` | 列表（分页，limit≤100） |
| 文档 | `GET/POST/PUT/DELETE /repos/{book_id}/docs/{doc_id}` | CRUD |
| 目录 | `GET/PUT /repos/{book_id}/toc` | 读取/更新目录 |
| 小记 | `GET/POST /notes` | 列表/创建 |
| 小记 | `GET/PUT /notes/{note_id}` | 详情/更新/删除/恢复 |
| 搜索 | `GET /search?q=&type=doc&scope=&page=` | 全文搜索（支持 namespace 限定） |
| 搜索（问答） | 多路并发搜索索引子库 → 读索引全文解析内容段 | 两级索引知识库问答专用 |

## License

MIT © [yehuoshun](https://github.com/yehuoshun)
