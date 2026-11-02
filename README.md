# daily-tech-radar · 每日技术雷达

每天自动汇总 **GitHub 高星库** 与 **论文日榜**，按你关注的领域过滤，生成 Markdown 日报，可选推送到飞书 / 邮件。零服务器成本，跑在 GitHub Actions 上。

## 追踪的领域（在 `config.json` 里改）

每个领域含一个 `query`（发给 GitHub Search 的主词，只发这一个以控限额）和一组 `keywords`（对仓库标题/描述/topics、论文标题/摘要做本地命中打分）。

| 领域 | 主 query | 本地命中词（节选，已按平台/对象/任务/方法分层） |
| --- | --- | --- |
| 无人机巡检 | drone inspection | drone, UAV, unmanned aerial, quadcopter, power-line/pipeline/infrastructure inspection, photogrammetry, autonomous flight, obstacle avoidance, swarm, 巡检, 无人机 |
| 电力视觉缺陷检测 | power line defect detection | transmission/distribution line, insulator, tower, substation, conductor, defect/anomaly/fault detection, infrared/thermography, YOLOv8/v11, 输电, 绝缘子, 变电站, 缺陷检测 |
| AI 编程 | AI coding assistant | AI coding, coding agent, code assistant, copilot, code generation, SWE-agent, Devin, Cursor, Trae, Claude Code, Codex |
| AI 应用 / Agent / RAG | AI agent framework | RAG, retrieval-augmented generation, multi-agent, autonomous agent, agent framework/orchestration, chatbot, knowledge base, LLMops, 智能体, 知识库 |
| LLM 模型与推理 | LLM inference | LLM, transformer, vLLM, LoRA, fine-tune, quantization, Ollama, LLM serving, KV cache, PagedAttention, Llama, 大模型 |
| Skills / MCP / 提示词生态 | model context protocol | MCP, Model Context Protocol, AI/agent/Claude skill, prompt engineering/template, tool/function calling, agent plugin, 提示词 |
| 本体 / 知识图谱 / 语义网 | knowledge graph ontology | ontology, ontology model, knowledge graph, knowledge representation, RDF/OWL/SKOS/SHACL, description logic, ontology-driven/grounded/guided, knowledge extraction, entity linking, schema alignment, 知识图谱, 本体, 语义网 |

## 数据源（均为公开）

| 数据 | 接口 / 页面 | 排序口径 |
| --- | --- | --- |
| GitHub 新星库 | `GET api.github.com/search/repositories?q=created:>7d&sort=stars` | 近 7 天新建，star 降序，再按领域词二次打分 |
| GitHub 活跃热库 | `q=stars:>500 pushed:<24h&sort=stars` | 24h 内仍有提交的高星库 |
| 论文日榜 | `GET huggingface.co/api/daily_papers?sort=trending` | 社区 upvote 热度 |
| 新论文 | `rss.arxiv.org/rss/cs.CV+cs.RO+cs.AI+cs.CL` | 按分类订阅后关键词过滤 |

> GitHub Search API：未认证 10 次/分钟、认证后 30 次/分钟，每日一次调用远低于限额。
> arXiv RSS 为美东时间午夜更新；HF Daily Papers 仅收录提交到 Daily 的论文，不等于 arXiv 全量。

## 快速开始（Fork 部署）

1. Fork 本仓库到自己账号。
2. 仓库 **Settings → Secrets and variables → Actions**，按需添加 Secrets（不配也能跑，只是不推送/无 AI 摘要）：

| Secret | 必填 | 说明 |
| --- | --- | --- |
| `GH_TOKEN` | 建议 | 个人 PAT（仅 `public_repo` 读权限即可），提高 API 限额到 30 次/分 |
| `FEISHU_WEBHOOK` | 否 | 飞书群自定义机器人 Webhook，配了就推群 |
| `LLM_SUMMARY_ENABLED` | 否 | 填 `1` 开启 AI 一句话摘要 |
| `LLM_BASE_URL` | 否 | OpenAI 兼容接口，默认 DeepSeek `https://api.deepseek.com/v1` |
| `LLM_API_KEY` | 否 | LLM Key |
| `LLM_MODEL` | 否 | 默认 `deepseek-chat` |
| `EMAIL_ENABLED` / `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `MAIL_TO` | 否 | 邮件推送，全部配齐才生效 |

3. **Settings → Actions → General → Workflow permissions** 选 `Read and write`（日报要提交回仓库）。
4. Actions 页手动点一次 `Run workflow` 验证；之后每天北京时间 08:20 自动跑，日报存到 `reports/YYYY-MM-DD.md`。

## 本地测试

```powershell
cd daily-tech-radar
python -m pip install -r requirements.txt
python radar.py
```

打开 `reports/今天日期.md` 查看结果。不带任何 Secret 也能跑：GitHub 走未认证限额、HF/arXiv 公开可读，只是不推送、无 AI 摘要。

## 自定义

- **加减领域 / 关键词**：改 `config.json` 的 `topics`，每个 topic 含 `query`（直接发给 GitHub 搜索的主词，只发这一个以控制 API 限额）和 `keywords`（对仓库标题/描述/topics、论文标题/摘要做本地命中打分）。
- **搜索词太宽收不到结果时**：换 `query`；想提高精准度优先加 `keywords`，不要堆 `query`。
- **关键词写法**：匹配不区分大小写，单词/短语/中文都行；尽量写具体对象词（insulator、transmission line、vLLM），少用 agent/model/skill 这类泛词，泛词在论文里只在标题出现才计分。
- **阈值**：`new_min_stars`（新星最低星数）、`active_min_stars`（活跃榜门槛）、`max_per_topic`（每领域展示条数）。
- **指定语言**：`github.language` 填 `Python`/`C++` 等，留空则不限。
- **论文分类**：改 `papers.arxiv_categories`，多分类用 `+` 连接，如 `cs.CV+cs.RO`。

## 目录结构

```
daily-tech-radar/
├── radar.py              # 主脚本：抓取 → 过滤打分 → 去重 → 渲染 → 推送
├── config.json           # 领域关键词、阈值、渠道配置
├── requirements.txt
├── .github/workflows/daily.yml
└── reports/              # 每日日报 + .seen.json（历史去重）
```

## 说明

- `reports/.seen.json` 记录已推送过的仓库/论文，新出现的条目标 🆕，避免重复刷屏；它随日报一起提交。
- 飞书机器人若开启了「签名校验」，需在脚本 `push_feishu` 里补 timestamp+sign 逻辑（当前为普通 webhook）。
- GitHub 官方 Trending 页面无公开 RSS 且反爬较强，因此本方案用官方 Search API 取新星、用 `pushed` 取活跃库，比硬爬 trending HTML 稳定。
- **API 限额**：未认证 Search 为 10 次/分钟，脚本每领域只发 1 个主 query（共 6 次）+ 1 次活跃榜，留有余量；一旦收到 403 会自动停止后续 GitHub 请求，其余数据源照常出报。配 `GH_TOKEN` 后提升到 30 次/分。
- **活跃榜口径**：`stars:>阈值 pushed:>近24h`，头部常是天天有提交的巨型项目；想找中腰部新贵就调低 `active_min_stars`。
- **arXiv 为空是正常的**：RSS 按美东时间午夜更新，周末无新发布批次；工作日白天跑更可能有内容。
- 论文匹配采用「标题命中加权、泛词（agent/llm/skill 等）仅标题计分」，减少摘要泛词导致的错配；个别噪声可通过增删 `keywords` 收敛。
