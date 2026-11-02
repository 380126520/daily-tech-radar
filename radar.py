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


# ---------- 免费中文翻译（无 LLM key 时的简介回退） ----------
import html as _html

_T_CACHE: dict[str, str] = {}


def translate_zh(text: str) -> str:
    """用免费 MyMemory 把英文一句话译为中文；失败回退原文。带缓存。"""
    text = (text or "").strip()
    if not text:
        return ""
    if text in _T_CACHE:
        return _T_CACHE[text]
    src = text[:220]
    try:
        r = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": src, "langpair": "en|zh-CN"}, timeout=HTTP_TIMEOUT,
        )
        j = r.json()
        out = j.get("responseData", {}).get("translatedText", "")
        if out and j.get("responseStatus") == 200:
            import re as _re
            out = _re.sub(r"\s+", " ", out).strip()
            _T_CACHE[text] = out
            return out
    except Exception as e:
        print(f"[warn] 翻译失败，回退原文: {e}", file=sys.stderr)
    _T_CACHE[text] = text
    return text


def intro_for(text: str, cfg: dict) -> str:
    """简介优先用 LLM（配了 key），否则免费翻译为中文。"""
    s = llm_one_liner(text, cfg)
    return s if s else translate_zh(text)


def esc(s: str) -> str:
    return _html.escape(str(s), quote=True)


def fmt_stars(n: int) -> str:
    return f"{n/1000:.1f}k" if n >= 1000 else str(n)


# ---------- 渲染 ----------
def _clean_summary(s: str) -> str:
    """剥掉 arXiv RSS 的 arXiv id/公告类型/摘要前缀，压缩空白。"""
    import re as _re
    s = s or ""
    s = _re.sub(r"arXiv:\S+", " ", s)
    s = _re.sub(r"公告类型[：:].*?(摘要[：:]|$)", " ", s)
    s = _re.sub(r"摘要[：:]", " ", s)
    return _re.sub(r"\s+", " ", s).strip()


def _gh_md(it: dict, badge: str, cfg: dict) -> str:
    desc = (it.get("description") or "").strip() or it["full_name"]
    intro = intro_for(desc, cfg)
    lang = it.get("language") or "—"
    return (
        f"#### {badge} [{it['full_name']}]({it['html_url']})\n"
        f"⭐ {fmt_stars(it.get('stargazers_count', 0))} · 🍴 {fmt_stars(it.get('forks_count', 0))} · {lang}\n\n"
        f"> 📝 {intro}\n\n"
        f"🔗 [查看仓库]({it['html_url']})\n"
    )


def _paper_md(p: dict, badge: str, cfg: dict) -> str:
    intro = intro_for(_clean_summary(p.get("summary", ""))[:220] or p["title"], cfg)
    extra = f" 👍{p.get('upvotes', 0)}" if "upvotes" in p else ""
    return (
        f"#### {badge} [{p['title']}]({p['url']}){extra}\n\n"
        f"> 📝 {intro}\n\n"
        f"🔗 [查看论文]({p['url']})\n"
    )


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
            lines.append(_gh_md(it, badge, cfg))
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
            lines.append(_gh_md(it, badge, cfg))
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
            lines.append(_paper_md(p, badge, cfg))
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
            lines.append(_paper_md(p, badge, cfg))
            seen[key] = date_str
    else:
        lines.append("_今日无匹配新提交。_")
    lines.append("")
    return "\n".join(lines)


# ---------- HTML 卡片版 ----------
def _gh_html(it: dict, badge: str, cfg: dict) -> str:
    desc = (it.get("description") or "").strip() or it["full_name"]
    intro = intro_for(desc, cfg)
    lang = it.get("language") or "—"
    return f"""
    <article class="card">
      <div class="card-head"><a class="name" href="{esc(it['html_url'])}">{esc(it['full_name'])}</a>{badge}</div>
      <div class="badges">
        <span class="badge">⭐ {fmt_stars(it.get('stargazers_count',0))}</span>
        <span class="badge">🍴 {fmt_stars(it.get('forks_count',0))}</span>
        <span class="badge lang">{esc(lang)}</span>
      </div>
      <p class="intro">📝 {esc(intro)}</p>
      <div class="card-foot"><span></span><a class="cta" href="{esc(it['html_url'])}">访问仓库 →</a></div>
    </article>"""


def _paper_html(p: dict, badge: str, cfg: dict) -> str:
    intro = intro_for(_clean_summary(p.get("summary", ""))[:220] or p["title"], cfg)
    up = f"👍{p.get('upvotes',0)}" if "upvotes" in p else ""
    return f"""
    <article class="card">
      <div class="card-head"><a class="name" href="{esc(p['url'])}">{esc(p['title'])}</a>{badge}</div>
      <div class="badges"><span class="badge">{esc(up)}</span></div>
      <p class="intro">📝 {esc(intro)}</p>
      <div class="card-foot"><span></span><a class="cta" href="{esc(p['url'])}">查看论文 →</a></div>
    </article>"""


def _section(title: str, cards: list[str]) -> str:
    if not cards:
        return ""
    return f"""
    <section><h2>{esc(title)}</h2><div class="grid">{''.join(cards)}
    </div></section>"""


def render_html(date_str: str, gh_new: dict, gh_active: list, hf: dict, arxiv: list,
                seen: dict, cfg: dict) -> str:
    blocks = []
    # 新星（按领域拼卡片）
    new_cards = []
    for topic, items in gh_new.items():
        for it in items:
            key = f"gh:{it['full_name']}"
            badge = '<span class="new">🆕</span>' if key not in seen else ""
            new_cards.append(_gh_html(it, badge, cfg))
            seen[key] = date_str
    blocks.append(_section("一、GitHub 新星库（近 7 天新建）", new_cards))

    act_cards = []
    for it in gh_active:
        key = f"gh:{it['full_name']}"
        badge = '<span class="new">🆕</span>' if key not in seen else ""
        act_cards.append(_gh_html(it, badge, cfg))
        seen[key] = date_str
    blocks.append(_section("二、GitHub 活跃热库（24h 内更新）", act_cards))

    hf_cards = []
    for topic, papers in hf.items():
        for p in papers:
            key = f"hf:{p['id']}"
            badge = '<span class="new">🆕</span>' if key not in seen else ""
            hf_cards.append(_paper_html(p, badge, cfg))
            seen[key] = date_str
    blocks.append(_section("三、Hugging Face 论文日榜", hf_cards))

    ax_cards = []
    for p in arxiv:
        key = f"arxiv:{p['id']}"
        badge = '<span class="new">🆕</span>' if key not in seen else ""
        ax_cards.append(_paper_html(p, badge, cfg))
        seen[key] = date_str
    blocks.append(_section("四、arXiv 新提交", ax_cards))

    topic_names = " / ".join(t["name"] for t in cfg["topics"])
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>每日技术雷达 · {date_str}</title>
<style>
  :root {{ --bg:#f4f6fb; --card:#fff; --ink:#1f2430; --sub:#6b7280; --line:#e7eaf2;
           --accent:#4f6ef7; --accent-soft:#eef1ff; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
         font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif; line-height:1.6; }}
  .wrap {{ max-width:1080px; margin:0 auto; padding:28px 18px 60px; }}
  header {{ background:linear-gradient(135deg,#4f6ef7,#7a5cf0); color:#fff;
            border-radius:18px; padding:26px 28px; margin-bottom:22px; }}
  header h1 {{ margin:0 0 6px; font-size:22px; }}
  header p {{ margin:0; opacity:.9; font-size:14px; }}
  h2 {{ font-size:17px; margin:26px 0 12px; padding-left:10px; border-left:4px solid var(--accent); }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(310px,1fr)); gap:14px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:14px;
           padding:16px; display:flex; flex-direction:column; gap:10px; }}
  .card:hover {{ box-shadow:0 6px 20px rgba(79,110,247,.12); }}
  .card-head {{ display:flex; justify-content:space-between; gap:8px; align-items:flex-start; }}
  .name {{ font-weight:700; font-size:15px; color:var(--ink); text-decoration:none; word-break:break-all; }}
  .name:hover {{ color:var(--accent); }}
  .new {{ font-size:13px; }}
  .badges {{ display:flex; flex-wrap:wrap; gap:6px; }}
  .badge {{ font-size:12px; color:var(--sub); background:var(--bg); border-radius:999px; padding:3px 10px; }}
  .badge.lang {{ color:var(--accent); background:var(--accent-soft); }}
  .intro {{ margin:0; font-size:14px; color:#333845; flex:1; }}
  .card-foot {{ display:flex; justify-content:space-between; align-items:center; }}
  .cta {{ font-size:13px; color:var(--accent); text-decoration:none; font-weight:600; }}
</style>
</head>
<body><div class="wrap">
  <header><h1>📡 每日技术雷达</h1>
  <p>{date_str} · {esc(topic_names)} · 每日自动更新</p></header>
  {''.join(blocks)}
</div></body></html>"""


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

    import copy
    markdown = render_markdown(today, gh_new, gh_active, hf, arxiv, seen, cfg)
    html = render_html(today, gh_new, gh_active, hf, arxiv, dict(seen), cfg)
    (REPORTS_DIR / f"{today}.md").write_text(markdown, encoding="utf-8")
    (REPORTS_DIR / f"{today}.html").write_text(html, encoding="utf-8")
    save_seen(seen)
    print(f"✓ 日报已生成: reports/{today}.md / .html")

    push_feishu(markdown, cfg)
    push_email(markdown, today, cfg)


if __name__ == "__main__":
    main()
