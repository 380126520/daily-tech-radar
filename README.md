<div align="center">

# 📡 daily-tech-radar

### 每天自动汇总 GitHub 高星库与论文日榜，按你关注的领域过滤，生成中文日报

![Python](https://img.shields.io/badge/Python-3.9+-3776AB)
![Schedule](https://img.shields.io/badge/cron-每天%2008:20%20(CST)-4f6ef7)
![Cost](https://img.shields.io/badge/成本-零服务器%20·%20GitHub%20Actions-0a8f5b)
![Output](https://img.shields.io/badge/输出-Markdown%20+%20HTML-7a5cf0)

</div>

---

每天自动抓取 **GitHub 新星库 / 活跃热库** 与 **Hugging Face 论文日榜 / arXiv 新提交**，按你配置的领域关键词本地二次打分过滤，每条配**中文一句话简介**，同时产出卡片式 Markdown 与美观 HTML 两个版本。零服务器成本，跑在 GitHub Actions 上，可选推送到飞书 / 邮件。

## ✨ 亮点

- 🆕 **GitHub 新星库**：近 7 天新建、按 star 降序，再按领域词二次打分
- 🔥 **GitHub 活跃热库**：24h 内仍有提交的高星库，捕捉仍在活跃演进的项目
- 📄 **论文日榜**：Hugging Face 社区 upvote 热度 + arXiv 关键词新提交
- 📝 **中文简介**：每条把英文描述 / 摘要自动译为中文一句话，一眼看懂
- 🎨 **双版式**：`reports/YYYY-MM-DD.md`（归档）+ `YYYY-MM-DD.html`（浏览器卡片版）
- 🔁 **去重不刷屏**：`reports/.seen.json` 记录已推送条目，新出现的标 🆕
- 🔑 **免 Key 可跑**：不配任何 Secret 也能出报；LLM / 飞书 / 邮件均为可选增强

## 🎯 追踪领域（在 `config.json` 里改）

每个领域含一个 `query`（发给 GitHub Search 的主词，只发这一个以控限额）和一组 `keywords`（对仓库标题/描述/topics、论文标题/摘要做本地命中打分）。

| 领域 | 主 query | 本地命中词（节选） |
| --- | --- | --- |
| 无人机巡检 | drone inspection | drone, UAV, quadcopter, power-line/pipeline inspection, photogrammetry, autonomous flight, 巡检, 无人机 |
| 电力视觉缺陷检测 | power line defect detection | transmission/distribution line, insulator, tower, substation, defect/anomaly detection, infrared, YOLOv8/v11, 绝缘子, 缺陷检测 |
| AI 编程 | AI coding assistant | AI coding, coding agent, copilot, code generation, SWE-agent, Cursor, Trae, Claude Code, Codex |
| AI 应用 / Agent / RAG | AI agent framework | RAG, multi-agent, autonomous agent, agent framework, knowledge base, 智能体, 知识库 |
| LLM 模型与推理 | LLM inference | vLLM, LoRA, fine-tune, quantization, Ollama, KV cache, PagedAttention, 大模型 |
| Skills / MCP / 提示词生态 | model context protocol | MCP, Model Context Protocol, agent skill, prompt engineering, function calling, 提示词 |
| 本体 / 知识图谱 / 语义网 | knowledge graph ontology | ontology, knowledge graph, RDF/OWL/SKOS/SHACL, description logic, 知识图谱, 本体, 语义网 |

## 📊 输出示例（Markdown 卡片版）

```markdown
#### 1. [owner/repo](https://github.com/owner/repo)
⭐ 12.3k · 🍴 1.2k · Python · 🟢近期活跃

> 📝 开源自主编码代理，可作为 SDK、IDE 扩展或 CLI 助手。

🔗 [查看仓库](https://github.com/owner/repo)
```

HTML 版在浏览器打开即为渐变标题栏 + 卡片网格，含 star/fork/语言徽章、中文简介、一键访问。

## 🗂 目录结构

```
daily-tech-radar/
├── radar.py              # 主脚本：抓取 → 过滤打分 → 去重 → 翻译简介 → md/HTML 渲染 → 推送
├── config.json           # 领域关键词、阈值、渠道配置
├── requirements.txt
├── .github/workflows/daily.yml
└── reports/              # 每日日报 .md/.html + .seen.json（历史去重）
```

## 🚀 快速开始（Fork 部署）

1. Fork 本仓库到自己账号。
2. 仓库 **Settings → Secrets and variables → Actions**，按需添加（不配也能跑，只是不推送 / 无 AI 摘要）：

| Secret | 必填 | 说明 |
| --- | --- | --- |
| `GH_TOKEN` | 建议 | 个人 PAT（仅 `public_repo` 读权限），搜索限额提到 30 次/分 |
| `FEISHU_WEBHOOK` | 否 | 飞书群自定义机器人 Webhook，配了就推群 |
| `LLM_SUMMARY_ENABLED` | 否 | 填 `1` 开启 LLM 精炼中文一句话（替代免费翻译） |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | 否 | OpenAI 兼容接口，默认 DeepSeek |
| `EMAIL_ENABLED` / `SMTP_*` / `MAIL_TO` | 否 | 邮件推送，全部配齐才生效 |

3. **Settings → Actions → General → Workflow permissions** 选 `Read and write`（日报要提交回仓库）。
4. Actions 页手动 `Run workflow` 验证；之后每天北京时间 **08:20** 自动跑，日报存到 `reports/`。

```bash
# 本地运行（不带 Secret 也能跑）
pip install -r requirements.txt
python radar.py
# 产出 reports/YYYY-MM-DD.md 与 .html
```

## ⚙️ 自定义（config.json）

- **加减领域 / 关键词**：改 `topics`，每项含 `query`（主词）和 `keywords`（本地打分词）。
- **搜索词太宽收不到结果**：换 `query`；想更精准优先加 `keywords`，不要堆 `query`。
- **阈值**：`new_min_stars`（新星最低星数）、`active_min_stars`（活跃榜门槛）、`max_per_topic`（每领域条数）。
- **指定语言**：`github.language` 填 `Python`/`C++` 等，留空不限。
- **论文分类**：`papers.arxiv_categories`，多分类用 `+` 连接，如 `cs.CV+cs.RO`。

## 📌 数据源（均为公开）

| 数据 | 接口 / 页面 | 排序口径 |
| --- | --- | --- |
| GitHub 新星库 | `search/repositories?q=created:>7d&sort=stars` | 近 7 天新建，star 降序，再按领域词打分 |
| GitHub 活跃热库 | `q=stars:>500 pushed:<24h&sort=stars` | 24h 内仍有提交的高星库 |
| 论文日榜 | `huggingface.co/api/daily_papers?sort=trending` | 社区 upvote 热度 |
| 新论文 | `rss.arxiv.org/rss/cs.CV+cs.RO+cs.AI+cs.CL` | 按分类订阅后关键词过滤 |

> GitHub Search 未认证 10 次/分、认证后 30 次/分；arXiv RSS 按美东时间午夜更新，周末可能为空属正常。

## 📝 说明

- 中文简介由免费翻译接口生成，个别术语机翻略生硬；配了 LLM Key 可替换为更精炼的人工式摘要。
- 飞书机器人若开启「签名校验」，需在 `push_feishu` 补 timestamp+sign 逻辑（当前为普通 webhook）。
- 官方 Trending 无公开 RSS 且反爬强，故用官方 Search API 取新星、用 `pushed` 取活跃库，比硬爬 HTML 稳定。
