# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)（MAJOR.MINOR.PATCH）与 Keep a Changelog 规范。

## [1.0.0] - 2026-11-02

### 新增
- 每日自动汇总 GitHub 新星库 / 活跃热库 + Hugging Face 论文日榜 / arXiv 新提交，按 7 个领域关键词过滤
- 每条配中文一句话简介（免费翻译，无需 LLM Key），输出卡片式 Markdown + 自包含 HTML 美观版
- `reports/.seen.json` 历史去重，新条目标 🆕；可选飞书 / 邮件推送

### 变更
- README 重写美化（徽章、亮点、示例、快速开始、配置表、数据源）

---

### 版本号约定
- **PATCH**（1.0.x）：修 bug、文案、不改变用法的小调整
- **MINOR**（1.x.0）：新增功能、向后兼容（如新增领域/输出格式/推送渠道）
- **MAJOR**（x.0.0）：不兼容的行为变更（如改筛选口径、改输出结构）
