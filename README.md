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
- [知识库问答](#知识库问答)
- [项目结构](#项目结构)
- [API 参考](#api-参考)

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

### 1. 配置

```bash
cp config/yuque-config.example.json config/yuque-config.json
# 编辑填入 token、group、default_book，详见下方「配置说明」
```

### 2. 使用

见下方「使用方式」，直接对 AI Agent 说即可。

## 配置说明

### 语雀 Token

在 [语雀开放平台](https://www.yuque.com/settings/tokens) 创建 Token，需勾选：

- `doc:read` — 读取文档
- `doc:write` — 创建/修改文档
- `repo:read` — 读取知识库
- `repo:write` — 修改知识库目录

### 完整字段

```json
{
  "token": "语雀 API Token",
  "group": "用户名",
  "default_book": { "book_id": 0, "namespace": "" },
  "index_master_book": { "book_id": 0, "namespace": "" },
  "index_books": [{ "book_id": 0, "namespace": "" }]
}
```

| 字段 | 说明 |
|------|------|
| `token` | 语雀 API Token（必填） |
| `group` | 语雀用户名/login（必填） |
| `default_book.book_id` | 默认知识库 ID，不指定目标时自动使用 |
| `default_book.namespace` | 默认知识库 namespace，如 `yehuoshun/my-book` |
| `index_master_book.book_id` |（可选）索引总库 ID，问答功能使用 |
| `index_master_book.namespace` |（可选）索引总库 namespace |
| `index_books[].book_id` |（可选）索引子库 ID 列表 |
| `index_books[].namespace` |（可选）索引子库 namespace 列表 |

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

详情见 [SKILL.md](./SKILL.md)「调用约定」。Agent 自动处理 Token 校验、并发调度、速率退避、结果校验。

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

两级索引（总库路由 → 子库关键词）+ 多路并发搜索 + 同义词/路由缓存 + LLM 生成答案。纯 LLM + 语雀 API，零外部依赖。

完整搜索管线、缓存策略、索引构建、搜索降级 → **[SKILL.md#一知识库问答系统](./SKILL.md#一知识库问答系统)**。

```
同义词缓存（子串匹配）→ 路由缓存 → 并发搜索索引子库 → 读索引全文 → LLM 生成答案 + 引用
```

## API 参考

详见 **[SKILL.md#二api-速查管理操作](./SKILL.md#二api-速查管理操作)**。完整端点/参数/错误码 → [references/api_reference.md](./references/api_reference.md)。

基地址：`https://www.yuque.com/api/v2`

## License

MIT © [yehuoshun](https://github.com/yehuoshun)
