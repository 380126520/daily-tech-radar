# -*- coding: utf-8 -*-
"""
daily-tech-radar：每日自动追踪 GitHub 高星库 + 论文日榜。

数据源：
  1. GitHub Search API（新星榜：近 N 天新建按 star；活跃榜：24h 内有推送的高星库）
  2. Hugging Face Daily Papers API（社区 upvote 日榜）
  3. arXiv RSS（按分类订阅新提交，关键词过滤）

输出：reports/YYYY-MM-DD.md；可选推送飞书群机器人 / 邮件。
所有外部渠道未配置时自动跳过，脚本始终能产出 Markdown 日报。
"""
import json
import os
import re
import sys
import time
import datetime as dt
from pathlib import Path

import requests
import feedparser

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
REPORTS_DIR = BASE_DIR / "reports"
SEEN_PATH = REPORTS_DIR / ".seen.json"
HTTP_TIMEOUT = 20
UA = "daily-tech-radar/1.0 (+https://github.com; personal research digest)"


# ---------- 基础工具 ----------
def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def http_get(url: str, params: dict | None = None, headers: dict | None = None) -> requests.Response:
    h = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if headers:
        h.update(headers)
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token and "api.github.com" in url:
        h["Authorization"] = f"Bearer {token}"
    # 不在此统一 raise_for_status：调用方需要自行区分 403 限流与其他错误
    return requests.get(url, params=params, headers=h, timeout=HTTP_TIMEOUT)


def now_cst() -> dt.datetime:
    # GitHub Actions 容器为 UTC，这里统一换算到北京时间做日报文件名
    return dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def load_seen() -> dict:
    if SEEN_PATH.exists():
        try:
            return json.loads(SEEN_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_seen(seen: dict) -> None:
    REPORTS_DIR.mkdir(exist_ok=True)
    SEEN_PATH.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")


def hit_score(text: str, keywords: list[str]) -> int:
    text_l = text.lower()
    return sum(1 for k in keywords if k.lower() in text_l)


# ---------- GitHub ----------
def search_github(query: str, sort: str, per_page: int):
    """返回 items 列表；遇到限流(403)返回 None，供上层停止后续请求。"""
    params = {"q": query, "sort": sort, "order": "desc", "per_page": per_page}
    try:
        resp = http_get("https://api.github.com/search/repositories", params=params)
        if resp.status_code == 403:
            print("[warn] GitHub Search 触发限流(403)，停止后续 GitHub 请求", file=sys.stderr)
            return None
        resp.raise_for_status()
        return resp.json().get("items", [])
    except Exception as e:
        print(f"[warn] GitHub 查询失败 ({query}): {e}", file=sys.stderr)
        return []


def repo_text(item: dict) -> str:
    topics = " ".join(item.get("topics") or [])
    return " ".join([
        item.get("name") or "", item.get("description") or "", topics
    ])


def fetch_github_new(cfg: dict) -> dict[str, list[dict]]:
    """近 new_lookback_days 天新建、按 star 排序，再按领域关键词二次打分。
    为控制未认证 10 次/分限额，每个领域只发 config 里的主 query，
    细分匹配交给本地 keywords 打分。遇到限流立即停止。"""
    g = cfg["github"]
    since = (utc_now() - dt.timedelta(days=g["new_lookback_days"])).strftime("%Y-%m-%d")
    out: dict[str, list[dict]] = {}
    for topic in cfg["topics"]:
        out[topic["name"]] = []
        main_q = topic["query"]
        lang = f" language:{g['language']}" if g.get("language") else ""
        items = search_github(f"{main_q} created:>{since}{lang}", "stars", g["per_query"])
        if items is None:  # 限流：停止后续所有 GitHub 请求
            break
        ranked = []
        for item in items:
            stars = item.get("stargazers_count", 0)
            if stars < g["new_min_stars"]:
                continue
            score = hit_score(repo_text(item), topic["keywords"])
            if score == 0:
                continue
            ranked.append({"item": item, "score": score, "stars": stars})
        ranked.sort(key=lambda x: (x["score"], x["stars"]), reverse=True)
        out[topic["name"]] = [r["item"] for r in ranked[: g["max_per_topic"]]]
    return out


def fetch_github_active(cfg: dict) -> list[dict]:
    """24h 内仍有推送、star 超过阈值的活跃热库。
    pushed 用日期格式（GitHub 不认带微秒的 ISO）；仅具体领域词命中才收录，排除泛词误配。"""
    g = cfg["github"]
    since = (utc_now() - dt.timedelta(hours=g["active_pushed_hours"])).strftime("%Y-%m-%d")
    specific = [k for t in cfg["topics"] for k in t["keywords"] if k.lower() not in GENERIC_WORDS]
    query = f"stars:>{g['active_min_stars']} pushed:>{since}"
    items = search_github(query, "stars", g["per_query"])
    if items is None:
        return []
    ranked = []
    for item in items:
        score = hit_score(repo_text(item), specific)
        if score == 0:
            continue
        ranked.append({"item": item, "score": score})
    ranked.sort(key=lambda x: x["item"].get("stargazers_count", 0), reverse=True)
    return [r["item"] for r in ranked[:8]]


# 高频泛词：仅在标题命中时计分，避免摘要里遍地 agent/llm 导致错配
GENERIC_WORDS = {"agent", "llm", "skill", "gpt", "transformer", "model", "ai", "application", "framework", "system"}


def paper_score(title: str, summary: str, keywords: list[str]) -> int:
    t, s = title.lower(), summary.lower()
    score = 0
    for k in keywords:
        kl = k.lower()
        if kl in t:
            score += 2 if kl in GENERIC_WORDS else 3
        elif kl not in GENERIC_WORDS and kl in s:
            score += 1
    return score


# ---------- Hugging Face Daily Papers ----------
def fetch_hf_papers(cfg: dict) -> dict[str, list[dict]]:
    p = cfg["papers"]
    out: dict[str, list[dict]] = {t["name"]: [] for t in cfg["topics"]}
    try:
        resp = http_get(
            "https://huggingface.co/api/daily_papers",
            params={"sort": "trending", "limit": p["hf_daily_limit"]},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[warn] HF Daily Papers 获取失败: {e}", file=sys.stderr)
        return out

    papers = []
    for row in data:
        paper = row.get("paper") or row
        pid = paper.get("id") or paper.get("arxivId")
        papers.append({
            "id": pid,
            "title": paper.get("title") or "",
            "summary": paper.get("summary") or "",
            "upvotes": paper.get("upvotes") or row.get("upvotes") or 0,
            "url": f"https://huggingface.co/papers/{pid}",
        })

    # 每条论文选得分最高的领域归属，避免被泛词错配
    bucket: dict[str, list[tuple[int, dict]]] = {t["name"]: [] for t in cfg["topics"]}
    for paper in papers:
        best_name, best_score = None, 0
        for topic in cfg["topics"]:
            sc = paper_score(paper["title"], paper["summary"], topic["keywords"])
            if sc > best_score:
                best_name, best_score = topic["name"], sc
        if best_name:
            bucket[best_name].append((best_score, paper))

    for name, rows in bucket.items():
        rows.sort(key=lambda x: x[1]["upvotes"], reverse=True)
        out[name] = [p for _, p in rows[: p["max_per_topic"]]]
    return out


# ---------- arXiv RSS ----------
def fetch_arxiv(cfg: dict) -> list[dict]:
    cats = cfg["papers"]["arxiv_categories"]
    url = f"https://rss.arxiv.org/rss/{cats}"
    all_keywords = [k for t in cfg["topics"] for k in t["keywords"]]
    entries = []
    try:
        feed = feedparser.parse(url)
    except Exception as e:
        print(f"[warn] arXiv RSS 获取失败: {e}", file=sys.stderr)
        return []
    for e in feed.entries:  # 遍历 RSS 返回的全部条目，不提前截断
        title = getattr(e, "title", "") or ""
        summary = getattr(e, "summary", "") or ""
        link = getattr(e, "link", "") or ""
        arxiv_id = (getattr(e, "id", "") or link).strip()
        # 标题命中或摘要命中非泛词才收录，过滤噪声
        if paper_score(title, summary, all_keywords) == 0:
            continue
        entries.append({
            "id": arxiv_id,
            "title": re.sub(r"\s+", " ", title).strip(),
            "summary": re.sub(r"<[^>]+>", "", summary)[:300],
            "url": link,
        })
    return entries[: cfg["papers"]["arxiv_max_total"]]


# ---------- 可选 LLM 一句话摘要 ----------
def llm_one_liner(text: str, cfg: dict) -> str | None:
    llm = cfg["llm_summary"]
    if os.environ.get(llm["enabled_env"], "").lower() not in ("1", "true", "yes"):
        return None
    api_key = os.environ.get(llm["api_key_env"])
    if not api_key:
        return None
    base_url = os.environ.get(llm["base_url_env"], llm["default_base_url"]).rstrip("/")
    model = os.environ.get(llm["model_env"], llm["default_model"])
    prompt = "用一句不超过 40 字的中文，说明这个项目/论文是做什么的、适合谁用，不要堆砌术语：\n" + text
    try:
        resp = requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 120,
            },
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[warn] LLM 摘要失败: {e}", file=sys.stderr)
        return None


# ---------- 渲染 ----------
def render_markdown(date_str: str, gh_new: dict, gh_active: list, hf: dict, arxiv: list,
                    seen: dict, cfg: dict) -> str:
    lines = [f"# 每日技术雷达 · {date_str}", ""]
    topic_names = " / ".join(t["name"] for t in cfg["topics"])
    lines.append(f"> 自动汇总 GitHub 高星库与论文日榜，按「{topic_names}」过滤。")
    lines.append("")

    # GitHub 新星
    lines.append("## 一、GitHub 新星库（近 7 天新建 · 按领域）")
    any_new = False
    for topic, items in gh_new.items():
        if not items:
            continue
        any_new = True
        lines.append(f"### {topic}")
        for it in items:
            key = f"gh:{it['full_name']}"
            badge = "🆕" if key not in seen else ""
            desc = (it.get("description") or "（无描述）").strip()
            summary = llm_one_liner(f"{it['full_name']} {desc}", cfg)
            extra = f" — {summary}" if summary else ""
            lines.append(f"- {badge} **[{it['full_name']}]({it['html_url']})** ⭐{it.get('stargazers_count', 0)}｜{desc}{extra}")
            seen[key] = date_str
        lines.append("")
    if not any_new:
        lines.append("_今日无匹配新星库。_")
        lines.append("")

    # GitHub 活跃热库
    lines.append("## 二、GitHub 活跃热库（24h 内有更新 · 全局匹配）")
    if gh_active:
        for it in gh_active:
            key = f"gh:{it['full_name']}"
            badge = "🆕" if key not in seen else ""
            desc = (it.get("description") or "（无描述）").strip()
            lines.append(f"- {badge} **[{it['full_name']}]({it['html_url']})** ⭐{it.get('stargazers_count', 0)}｜{desc}")
            seen[key] = date_str
    else:
        lines.append("_今日无匹配活跃热库。_")
    lines.append("")

    # HF 论文
    lines.append("## 三、Hugging Face 论文日榜（社区 upvote）")
    any_hf = False
    for topic, papers in hf.items():
        if not papers:
            continue
        any_hf = True
        lines.append(f"### {topic}")
        for p in papers:
            key = f"hf:{p['id']}"
            badge = "🆕" if key not in seen else ""
            lines.append(f"- {badge} **[{p['title']}]({p['url']})** 👍{p['upvotes']}")
            seen[key] = date_str
        lines.append("")
    if not any_hf:
        lines.append("_今日无匹配论文。_")
        lines.append("")

    # arXiv
    lines.append("## 四、arXiv 新提交（关键词匹配）")
    if arxiv:
        for p in arxiv:
            key = f"arxiv:{p['id']}"
            badge = "🆕" if key not in seen else ""
            lines.append(f"- {badge} **[{p['title']}]({p['url']})**")
            lines.append(f"  - {p['summary']}")
            seen[key] = date_str
    else:
        lines.append("_今日无匹配新提交。_")
    lines.append("")
    return "\n".join(lines)


# ---------- 推送 ----------
def push_feishu(markdown: str, cfg: dict) -> None:
    webhook = os.environ.get(cfg["push"]["feishu_webhook_env"])
    if not webhook:
        return
    # 飞书文本消息限制较长，这里截取前 1500 字保证可达
    text = markdown[:1500]
    try:
        requests.post(webhook, json={"msg_type": "text", "content": {"text": text}}, timeout=HTTP_TIMEOUT)
    except Exception as e:
        print(f"[warn] 飞书推送失败: {e}", file=sys.stderr)


def push_email(markdown: str, date_str: str, cfg: dict) -> None:
    ec = cfg["push"]["email"]
    if os.environ.get(ec["enabled_env"], "").lower() not in ("1", "true", "yes"):
        return
    import smtplib
    from email.mime.text import MIMEText
    from email.header import Header
    host = os.environ.get(ec["smtp_host_env"])
    user = os.environ.get(ec["smtp_user_env"])
    pwd = os.environ.get(ec["smtp_pass_env"])
    to_addr = os.environ.get(ec["to_env"])
    port = int(os.environ.get(ec["smtp_port_env"], "465"))
    if not all([host, user, pwd, to_addr]):
        print("[warn] 邮件配置不完整，跳过。", file=sys.stderr)
        return
    msg = MIMEText(markdown, "plain", "utf-8")
    msg["Subject"] = Header(f"每日技术雷达 {date_str}", "utf-8")
    msg["From"] = user
    msg["To"] = to_addr
    try:
        with smtplib.SMTP_SSL(host, port) as s:
            s.login(user, pwd)
            s.sendmail(user, [to_addr], msg.as_string())
    except Exception as e:
        print(f"[warn] 邮件推送失败: {e}", file=sys.stderr)


# ---------- 主流程 ----------
def main() -> None:
    cfg = load_config()
    today = now_cst().strftime("%Y-%m-%d")
    REPORTS_DIR.mkdir(exist_ok=True)
    seen = load_seen()

    print("→ 抓取 GitHub 新星库 ...")
    gh_new = fetch_github_new(cfg)
    print("→ 抓取 GitHub 活跃热库 ...")
    gh_active = fetch_github_active(cfg)
    print("→ 抓取 HF 论文日榜 ...")
    hf = fetch_hf_papers(cfg)
    print("→ 抓取 arXiv RSS ...")
    arxiv = fetch_arxiv(cfg)

    markdown = render_markdown(today, gh_new, gh_active, hf, arxiv, seen, cfg)
    out_path = REPORTS_DIR / f"{today}.md"
    out_path.write_text(markdown, encoding="utf-8")
    save_seen(seen)
    print(f"✓ 日报已生成: {out_path}")

    push_feishu(markdown, cfg)
    push_email(markdown, today, cfg)


if __name__ == "__main__":
    main()
