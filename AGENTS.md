# AGENTS 运行日志

- 2025-10-18 Codex Agent：初始化 Git 仓库并重写 `obr_docs_to_md.py`，实现基于 curl + lxml + pandoc 的 OBR 扩展 API 文档抓取与 Markdown 转换流程，补全目录结构和日志产物。
- 2025-10-18 Codex Agent：优化 robots.txt 加载逻辑，遇到 404 等异常时提示并默认允许抓取，避免脚本直接退出。
- 2025-10-18 Codex Agent：新增域名预热与 sitemap 403 兜底逻辑，在被拒访问时改用索引页解析，提升抓取稳定性。
- 2025-10-18 Codex Agent：调整 sitemap 兜底失败时的退出策略，改为输出空结果并生成 url-map，方便离线排查。
- 2025-10-18 Codex Agent：新增 `.gitignore` 忽略 `out/` 目录，保持版本库整洁便于复现。
