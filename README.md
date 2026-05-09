# 语雀全功能技能

基于语雀 API 的全功能操作，包括语义增强搜索和完整的语雀管理能力。

## 与官方 yuque-mcp-server 的关系

语雀官方提供了 [yuque-mcp-server](https://github.com/yuque/yuque-mcp-server)，是一个 MCP 协议的语雀 API 封装。

| 对比项 | 本 Skill | 官方 yuque-mcp-server |
|--------|----------|----------------------|
| 协议 | OpenClaw Skill | MCP 标准 |
| 客户端 | 仅 OpenClaw | Claude Desktop/Code、Cursor、VS Code、Windsurf 等 |
| 语义搜索 | ✅ 有（同义词扩展 + LLM Rerank） | ❌ 无（直接用语雀原生搜索） |
| 文档版本管理 | ✅ 有 | ❌ 无 |
| 群组成员管理 | ✅ 有（未测试） | ✅ 有 |
| 统计数据 | ✅ 有（未测试，需额外权限） | ✅ 有 |
| 维护方 | 个人 | 语雀官方 |
| 类型安全 | ❌ 无（纯 Prompt） | ✅ 有（TypeScript + Zod） |

**建议**：
- 如果只用 OpenClaw，用本 Skill 即可，功能更全且有语义搜索增强
- 如果用多个 MCP 客户端，可以同时安装官方 yuque-mcp-server

### 官方 yuque-mcp-server 安装方式

```bash
# 一键安装
npx yuque-mcp install --token=YOUR_TOKEN --client=cursor

# 支持的客户端
# claude-desktop, vscode, cursor, windsurf, cline, trae, qoder, opencode
```

支持的客户端：Claude Desktop、Claude Code、VS Code (GitHub Copilot)、Cursor、Windsurf、Cline、Trae 等

## 能做什么

### 用户信息
- 获取当前用户信息
- 测试 API 连通性

### 知识库管理
- 列出所有知识库
- 查看知识库详情
- 创建新知识库
- 更新知识库信息
- 删除知识库

### 文档管理
- 列出知识库内的文档
- 查看文档详情（支持 Lake 格式）
- 创建新文档（自动添加到目录，默认私有）
- 更新已有文档
- 删除文档

### 文档版本管理
- 查看文档历史版本
- 查看指定版本详情

### 小记管理
- 列出所有小记（包括置顶和回收站）
- 查看小记详情
- 创建新小记
- 更新已有小记
- 删除小记（移入回收站）
- 恢复小记

### 目录管理
- 查看知识库目录结构
- 移动文档到指定目录
- 调整目录结构

### 群组成员管理 ⚠️ 未测试
- 列出群组成员
- 更新成员角色
- 移除群组成员

### 统计数据 ⚠️ 未测试，需 `statistic:read` 权限
- 团队整体统计
- 成员贡献统计
- 知识库统计
- 文档统计

### AI 问答（语义增强）
- 同义词扩展搜索
- LLM Rerank 智能排序
- 基于笔记生成回答

## 快速开始

### 1. 配置 Token

首次使用时，我会询问你配置文件存放位置，然后引导你完成配置：填写 Token → 自动验证 → 自动创建索引所需的知识库 → 记录路径到 MEMORY.md。

全程只需提供 Token，知识库自动创建。

获取 Token：语雀 → 设置 → Token → 新建 Token（需要读取和写入权限）

### 2. 开始使用

#### 查看知识库
```
我的语雀知识库
列出知识库
```

#### 创建文档
```
在语雀创建文档《测试指南》
在语雀的 xxx 知识库创建一篇关于 Git 使用说明的文档
```

创建文档后会自动添加到目录末尾。如需指定位置，先创建再移动。

#### 创建小记
```
创建语雀小记：今天要完成 xxx
帮我记一条小记
```

#### AI 问答（需先构建索引）
```
在语雀搜索 Python 环境配置
问语雀怎么实现线程池
查我的语雀笔记关于并发
```

#### 构建索引
```
构建语雀索引
更新语雀索引，知识库是 yehuoshun/my-notes
```

## 触发条件

当消息包含「语雀」关键词时触发：

| 说法 | 功能 |
|------|------|
| 我的语雀信息 | 获取用户信息 |
| 我的语雀知识库 / 列出知识库 | 知识库列表 |
| 创建语雀知识库 | 创建知识库 |
| 更新知识库 | 更新知识库 |
| 删除知识库 | 删除知识库 |
| 知识库有哪些文档 / 列出文档 | 文档列表 |
| 文档详情 / 看看那篇文档 | 文档详情 |
| 在语雀创建文档 | 创建文档 |
| 更新语雀文档 / 修改文档 | 更新文档 |
| 删除语雀文档 | 删除文档 |
| 我的语雀小记 / 列出小记 | 小记列表 |
| 创建语雀小记 / 记一条小记 | 创建小记 |
| 更新小记 | 更新小记 |
| 删除小记 | 删除小记 |
| 恢复小记 | 恢复小记 |
| 知识库目录 / 目录结构 | 获取目录 |
| 移动文档到这个目录 | 更新目录 |
| 团队成员 / 群组成员 / 移出团队 | 群组成员管理 |
| 团队统计 / 成员贡献 / 数据统计 | 统计数据 |
| 在语雀搜索 / 问语雀 | AI 问答 |
| 构建语雀索引 | 构建索引 |
| 重试失败的索引 | 重试失败文档 |
| 导出文档 / 导出知识库 / 下载语雀文档 | 文档导出 |

## 配置

**首次使用时**，将 `config.example.json` 复制到你指定的位置（如 `docs/yuque-config.json`），填写以下字段后告诉我路径，我会记录到 MEMORY.md。

获取 Token：语雀 → 设置 → Token → 新建 Token（需要读取和写入权限）

**配置示例**：

```json
{
  "token": "你的语雀Token",
  "group": "你的用户名",
  "index_master_book": {"book_id": 123, "namespace": "xxx/index-master"},
  "index_books": [{"book_id": 456, "namespace": "xxx/index-1"}],
  "default_book": {"book_id": 789, "namespace": "xxx/default"},
  "index_state": "local",
  "segment_length": 2000,
  "candidates_limit": 20,
  "top_k": 5
}
```

**字段说明**：

| 字段 | 说明 |
|------|------|
| `token` | 语雀 Token，需要读取和写入权限 |
| `group` | 用户名或团队名，用于搜索 scope 参数前缀 |
| `index_master_book` | 索引总库，存放总文档（JSON 元信息），每个关键词对应一个总文档 |
| `index_books` | 索引子库列表，存放子文档（具体索引内容）。接近 4500 篇时建议添加更多（语雀上限 5000 篇） |
| `default_book` | 创建文档时的默认知识库 |
| `index_state` | 状态文件存储位置。`"local"` = 本地 `~/.openclaw/workspace/utils/yuque/index_state.json`；自定义路径如 `"/path/to/state.json"`；或语雀文档对象 `{"type":"yuque","book_id":0,"doc_id":0}`（需用户手动创建后填入 doc_id） |
| `segment_length` | 分段长度（字），长文档分段喂给 LLM 时用，默认 2000 |
| `candidates_limit` | 搜索候选数，LLM Rerank 前的候选文档数，默认 20 |
| `top_k` | Rerank 后获取全文的文档数，默认 5 |

索引子文档上限为 **200KB**（约 50000 字），接近 180KB 时自动新建子文档，避免单次 API POST 超时。

## AI 问答原理

### 为什么要构建索引？

语雀原生搜索是关键词匹配，搜「py」找不到「Python」。构建索引后：
- 自动扩展同义词：Python ≈ py ≈ python3
- 智能关联：搜「安装」能找到「环境配置」

### 关键词过滤

索引构建时自动过滤无意义关键词：「1.2」等版本号、「早上好」等问候语、「今天」等时间词。

技术术语保留：JDK 8、HTTP/2、IPv6 等。

### 搜索流程

```
你的问题：「py 怎么装」
    ↓
扩展关键词：py, Python, python3, 安装, install...
    ↓
搜索索引文档，找到相关条目
    ↓
LLM Rerank 智能排序
    ↓
逐一获取全文（命中 404 则跳过 → 末尾提示清理死索引）
    ↓
阅读全文，生成回答
```

### 同义词类型

- 缩写/简写：Python → py, JavaScript → js, Kubernetes → k8s
- 中英文互译：安装 → install, 配置 → config
- 近义词：安装 → 部署 → 环境搭建
- 大小写变体：Python → python, JVM → jvm
- 专业术语变体：线程池 → ThreadPool

## API 参考

完整的 API 端点、参数、限制及错误处理见 **[references/api_reference.md](references/api_reference.md)**，由语雀官方 OpenAPI 文档整理而来，作为唯一权威来源。

**删除操作注意**：删除文档、知识库不可恢复，小记删除为软删除（可回收站恢复），执行前均会询问确认。

## 常见问题

### 索引要多久？

取决于文档数量。建议首次使用时先小范围测试。

### 索引会过期吗？

源文档更新后，建议重新构建索引。

### 搜索不到怎么办？

1. 确认已构建索引
2. 尝试用不同关键词搜索
3. 检查配置的知识库是否正确

## 文件结构

```
SKILL.md                          ← 核心触发 + 工作流程速查表（230 行）
CHANGELOG.md                      ← 更新日志
references/
  api_reference.md                ← 语雀 API + 错误处理 + 故障排查
  api_helper.py                   ← 通用请求封装（超时/重试/并行/速率检查）
  index_flow.md                   ← 索引构建详细流程
  search_flow.md                  ← AI 问答详细流程
```

**设计原则**：SKILL.md 只保留触发条件、API 端点速查和关键注意事项，详细内容按需读取 reference 文件，减少上下文消耗。

## 更新日志

见 [CHANGELOG.md](CHANGELOG.md)