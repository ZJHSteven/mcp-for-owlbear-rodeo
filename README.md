# Owlbear Rodeo 扩展文档工具集

> 现包含两个核心模块：
> 1. `mcp-docs-server`：基于 uv 发布的 MCP Python 服务端，可直接通过 `uvx mcp-docs-server` 暴露 Owlbear Rodeo 文档资源（搜索 + 打开全文）。
> 2. `obr_docs_to_md.py`：抓取官网扩展文档、生成 Markdown 的离线脚本，仍用于保持 `docs/markdown` 最新。

## MCP 文档服务器快速上手

1. **安装依赖**
   ```bash
   uv sync
   ```
2. **查看帮助**
   ```bash
   uv run mcp-docs-server --help
   ```
3. **最小可运行示例**
   ```bash
   uvx mcp-for-owlbear-rodeo mcp-docs-server --transport stdio
   ```
   - 默认从 `docs/markdown` 自动加载所有 Markdown 文件，每个文件即一个 MCP 资源 (`doc://owlbear/<category>/<slug>`)。
   - 自动注册两个工具：
     - `search_docs(query, top_k=5)`：返回资源链接列表。
     - `open_doc(name)`：返回完整 Markdown 内容（与资源 URI 对齐）。
   - 资源描述 (`description`) 直接取自文档首段正文，无人工编写，满足“不要瞎编”约束。

4. **接入自定义文档目录**
   ```bash
   uv run mcp-docs-server --docs-path D:/cache/markdown
   ```
   或通过环境变量：
   ```bash
   set MCP_DOCS_ROOT=D:/cache/markdown
   uvx mcp-docs-server
   ```

5. **在其他 MCP 客户端中测试**
   - 以 Claude MCP Tool 为例，配置 `command`: `"uvx"`, `args`: `["mcp-for-owlbear-rodeo", "mcp-docs-server"]`。
   - 客户端会自动发现全部 `doc://owlbear/...` 资源，并可调用 `search_docs`/`open_doc`。

> ⚠️默认打包会随 wheel 附带 `docs/markdown`，无需联网即可开箱即用；若要更新内容，请运行下方的抓取脚本刷新 Markdown 再重新构建。

## Owlbear Rodeo 扩展文档抓取工具使用说明

> 脚本入口：`obr_docs_to_md.py`  
> 目标：批量抓取 https://docs.owlbear.rodeo/extensions/ 下的 API 及 Reference 文档，转换为纯文本 Markdown，方便后续切分与注入 MCP。

## 环境准备

1. **Python**：推荐 Python 3.9 及以上版本。  
2. **命令行依赖**  
   - `curl`：用于抓取 HTML。  
   - `pandoc`：将清洗后的 HTML 转换为 GitHub Flavored Markdown。  
3. **Python 包**  
   - `lxml`（标准库以外）  
   - `cssselect`（本仓库近期新增，务必安装）  
   安装示例：
   ```bash
   python -m pip install lxml cssselect
   ```

> 小贴士：在 Windows 上使用本脚本时，建议通过 Git Bash 或 PowerShell 运行；确保 `curl` 与 `pandoc` 已加入 `PATH`。

## 输出结构总览

默认输出位于 `./out`，脚本会自动创建并复用目录：

```
out/
  raw_html/       # 原始抓取的 HTML（按 apis/reference 分类）
  cleaned_html/   # 清洗后的 HTML，供 Pandoc 转换
  md/             # 最终 Markdown，纯文本无 HTML 标签
  assets/         # Pandoc 提取出的媒体文件（当前已全部剔除，不再使用）
  logs/
    run.log       # 逐条处理日志（北京时间，含缓存/抓取标记）
    failures.txt  # 失败记录（包含最终错误信息，便于重试）
  url-map.json    # 成功/缺失页面概览 + 元数据
```

`url-map.json` 结构示例：

```json
{
  "generated_at": "2025-10-19T02:24:42+08:00",
  "timezone": "UTC+08:00",
  "output_root": ".../out",
  "expected_items": [
    {"url": "...", "category": "apis", "slug": "action"}
  ],
  "items": [
    {
      "url": "...",
      "category": "apis",
      "title": "Action",
      "slug": "action",
      "raw_html": "raw_html/apis/action.html",
      "cleaned_html": "cleaned_html/apis/action.html",
      "markdown": "md/apis/action.md"
    }
  ],
  "missing_items": []
}
```

> `missing_items` 非空时，请查看 `logs/failures.txt` 并考虑使用 `--force-fetch` 重试。

## 常用命令

### 1. 全量抓取（推荐初次执行）

```bash
python obr_docs_to_md.py
```

- 自动解析 `sitemap.xml`（优先）/ 各分类索引页，收集 `/extensions/apis/` 与 `/extensions/reference/` 全部页面。  
- 默认输出位置：`./out`。可通过 `--out` 自定义目录。

执行结束后，终端会输出如下概览（示例）：

```
成功转换 42/45 个页面（apis:30, reference:12），Markdown 位于 D:\...\out\md\apis
仍有以下页面未成功生成 Markdown：
- [reference] https://docs.owlbear.rodeo/extensions/reference/foo
- [reference] https://docs.owlbear.rodeo/extensions/reference/bar
```

### 2. 单页调试

```bash
python obr_docs_to_md.py --single https://docs.owlbear.rodeo/extensions/reference/manifest
```

- 仅处理指定 URL，适用于调试清洗规则。  
- 同样会更新 `url-map.json`，记录当前运行期望/缺失项。

### 3. 复用缓存 / 强制刷新

- **默认行为**：若 `out/raw_html/<category>/<slug>.html` 存在且非空，脚本直接复用，避免重复请求。  
- **强制刷新**：添加 `--force-fetch` 即可忽略缓存从远端重新抓取。  
  ```bash
  python obr_docs_to_md.py --force-fetch
  ```

### 4. 其他常用参数

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `--out PATH` | 指定输出根目录 | `out` |
| `--sleep-min` | 连续请求的最小间隔（秒） | `0.5` |
| `--sleep-max` | 连续请求的最大间隔（秒） | `1.5` |
| `--urls-file FILE` | 从自定义列表读取 URL（每行一个） | 无 |

> 建议保持合理的间隔，避免触发远端限流。即使启用缓存，解析失败也会在下一次尝试自动重新抓取。

## 运行后如何自检

1. **命令行输出**：优先关注终端概览，确认成功/缺失数量。  
2. **`logs/failures.txt`**：若有失败条目，逐条定位原因（网络异常、Cloudflare 质询、Pandoc 转换失败等）。  
3. **`out/url-map.json`**：快速查询生成的 Markdown 路径及未覆盖页面，可供后续脚本读取。  
4. **Markdown 纯文本校验**：脚本已移除所有 `<img>/<a>` 等 HTML 标签，仅保留 Markdown 语法。可配合 `rg "<"` 检查是否仍有漏网标签。

## 常见问题

- **Cloudflare 验证导致 403**：脚本已内置通用 UA 与重试机制，但若仍无法通过，可适当增加 `--sleep-min/--sleep-max` 间隔或手动复制 HTML 至 `raw_html` 后重跑清洗。  
- **本地没有安装 Pandoc**：请从 [https://pandoc.org/installing.html](https://pandoc.org/installing.html) 下载对应平台版本，并添加到 `PATH`。  
- **输出时间看不懂**：所有日志、`url-map.json` 都使用北京时间（UTC+08:00），便于与本地排查时区一致。

## 后续扩展建议

- 在 CI 中配置定时任务，结合 `--force-fetch` 每日刷新文档。  
- 依据 `url-map.json` 中的 `expected_items` / `missing_items` 生成告警报告，确保 MCP 数据源随站点更新。  
- 如需合并 Markdown，可编写额外脚本，根据 `category` 字段聚合生成章节化文档。

> 如果后续需要新增其它目录（例如 `/extensions/tutorials/`），可以参照 `CATEGORY_CONFIGS` 增加配置项并复用现有流程。



• 整体思路
                                                                      
  - 仓库当前只有两个核心模块：MCP 文档服务器 mcp-docs-server 与离线抓 
    取脚本 obr_docs_to_md.py，可以也应该分别测试，全部流程都能在本地  
    完成，不需要先发到 PyPI。pyproject.toml 里已声明入口脚本 mcp-docs-    server = "docs_server:main"，因此 uv run mcp-docs-server ... 就   
    能直接启动最新源码版的服务端，等到发布到 PyPI 后再用 uvx mcp-for- 
    owlbear-rodeo ... 跑线上版即可（README.md:7-41, pyproject.toml:1- 
    33）。                                                            
                                                                      
  MCP 文档服务器本地验证路径                                          
                                                                      
  - 安装依赖：首次或更新后运行 uv sync，uv 会按 pyproject.toml        
    把 mcp[cli]>=1.21.0 与其余运行时（FastMCP 在依赖里）装好          
    （README.md:9-16, pyproject.toml:8-10）。                         
  - 直接跑 CLI：uv run mcp-docs-server --transport stdio --log-level  
    DEBUG，默认会自动从仓库内的 docs/markdown 扫描文档并注册成资源；  
    命令行里可观察 “完成资源注册: X 篇文档” 的日志判断加载是否成功    
    （README.md:17-25, src/docs_server/server.py:1-123）。            
  - 用 MCP 官方 Inspector 做冒烟测试（不用任何 AI 客户端）：          
                                                                      
    uv run mcp dev src/docs_server/server.py:main -- --transport stdio
  --log-level DEBUG                                                   
      - 前半段让 mcp dev 启动 Inspector，-- 后的参数原样传进 main()。 
      - Inspector 打开后：                                            
          1. Resources → Refresh：确认列表里出现 doc://owlbear/       
             <category>/<slug>，数量要与 docs/markdown 内文件数一致； 
             若缺少，回溯日志看哪个 Markdown 无法解析。               
          2. Tools → search_docs：输入 viewport 等关键词，检查返回的  
             resource_link 是否能点开。                               
          3. Tools → open_doc：输入 apis/viewport 等名称，确认能收到整             篇 Markdown。                                            
  - 与 Codex/Claude/VS Code Copilot 等客户端对接：凡是支持自定        
    义 MCP 命令的客户端，都只需把命令设置成 uv run mcp-docs-server    
    --transport stdio 或（若已发版）uvx mcp-for-owlbear-rodeo mcp-    
    docs-server --transport stdio，客户端就能自动发现 search_docs /   
    open_doc 两个工具（README.md:17-40）。Codex 这类基于标准 MCP 的代 
    理只需在其配置里填写同样命令即可，无需额外桥接。                  
  - 自动化/脚本化自检：可用下述最小脚本确认服务端即将暴露的文档数量、 
    示例条目是否正确；脚本只读 DocumentStore，不需启动 MCP：          
                                                                      
    uv run python - <<'PY'                                            
    """快速列出可用文档, 方便比对数量"""                              
    from docs_server.doc_store import DocumentStore                   
                                                                      
    store = DocumentStore()                                           
    docs = store.documents                                            
    print(f"共加载 {len(docs)} 篇文档, 根目录={store.doc_root}")      
    for record in docs[:5]:                                           
    # 逐条示例, 确认类别/URI/简介是否符合预期                         
        print(f"- {record.name} -> {record.uri}")                     
    PY                                                                
    如果脚本抛出 DocumentIndexEmptyError / DocumentNotFoundError（定义    在 src/docs_server/exceptions.py），就说明文档目录不完整或名称输入    错误，可据此定位（src/docs_server/doc_store.py:1-188）。          
                                                                      
  VS Code Copilot / 其他编辑器联调建议                                
                                                                      
  - 目前 GitHub Copilot MCP 集成仍在逐步放出，若你所在版本尚未开放，可    以先用 mcp dev Inspector 或 Claude Desktop（支持自定义 MCP）验证；    待 Copilot 支持后，沿用同一命令即可，无需改造。调试时可把 --log-  
    level DEBUG 打开，通过终端日志追踪客户端何时发起 tool call，便于  
    排查。                                                            
                                                                      
  文档抓取脚本测试（确保资源不缺失）                                  

  1. 基础运行：python obr_docs_to_md.py 会抓取官网并在 out/ 生        
     成 raw_html / cleaned_html / md / logs / url-map.json。若只想    
     更新仓库随包发出的 Markdown，可跑完后把 out/md/* 覆盖到 docs/    
     markdown（README.md:43-135）。                                   
  2. 强制刷新 & 指定 URL：                                            
      - 网络状态良好但怀疑缓存过期：python obr_docs_to_md.py --force- 
        fetch。                                                       
      - 单页调试：python obr_docs_to_md.py --single                   
        <URL>（README.md:109-143）。                                  
  3. 验证是否“漏文档”：                                               
      - 查看 out/url-map.json，missing_items 非空就代表有文档没转成   
        功，需结合 logs/failures.txt 排查（README.md:64-161）。       
      - 可配合 rg "<" out/md 确认 Markdown 是否仍混入 HTML，若命中则表        示清洗规则需调整（README.md:155-161）。                       
  4. 回填到 MCP：当 url-map.json 中 missing_items 为空后，把最新      
     Markdown 覆盖到 docs/markdown，再按上节步骤重启 mcp-docs-server  
     验证资源数是否同步增加；DocumentStore 会在日志里打印“使用文档    
     目录: ...”，可双重确认指向的是你刚更新的目录（src/docs_server/   
     doc_store.py:70-142）。                                          
                                                                      
  “初步手动测试”Checklist（新手友好）                                 
                                                                      
  1. uv sync → uv run mcp-docs-server --transport stdio --log-level   
     DEBUG（服务器能启动，日志里有“完成资源注册”）。                  
  2. 另开终端跑 uv run mcp dev src/docs_server/server.py:main --      
     --transport stdio，在 Inspector 里：                             
      - Resources 显示的数量 = docs/markdown 实际 Markdown 数（可用 rg        --files docs/markdown | wc -l 对比）。                        
      - search_docs 输入冷门/热门关键词各一次，确保有有/无命中两种情况        都能得到合理文本提示。