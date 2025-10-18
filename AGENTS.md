# AGENTS 运行日志

- 2025-10-18 Codex Agent：初始化 Git 仓库并重写 `obr_docs_to_md.py`，实现基于 curl + lxml + pandoc 的 OBR 扩展 API 文档抓取与 Markdown 转换流程，补全目录结构和日志产物。
- 2025-10-18 Codex Agent：优化 robots.txt 加载逻辑，遇到 404 等异常时提示并默认允许抓取，避免脚本直接退出。
- 2025-10-18 Codex Agent：新增域名预热与 sitemap 403 兜底逻辑，在被拒访问时改用索引页解析，提升抓取稳定性。
- 2025-10-18 Codex Agent：调整 sitemap 兜底失败时的退出策略，改为输出空结果并生成 url-map，方便离线排查。
- 2025-10-18 Codex Agent：新增 `.gitignore` 忽略 `out/` 目录，保持版本库整洁便于复现。
- 2025-10-19 Copilot：简化 curl 调用，移除所有 headers/cookies 参数，仅保留 URL。
- 2025-10-19 Copilot：发现 Cloudflare 保护导致 sitemap 返回质询页面，为 curl 添加 User-Agent 绕过简单检测，并新增 Cloudflare 质询页面检测逻辑。
- 2025-10-18 Codex Agent：重构 `obr_docs_to_md.py`，同时抓取 apis/reference 目录，修复 308 重定向导致的空文档问题，新增站内链接本地化、目录分层与 url-map 分类标记等清洗流程优化。
- 2025-10-18 Codex Agent：继续强化 `obr_docs_to_md.py`，新增 Pandoc 后纯文本清洗、站内锚点去装饰、图片资源剔除，以及 `--force-fetch` 参数与缓存复用逻辑，避免重复抓取并保持 Markdown 无 HTML 标签。
