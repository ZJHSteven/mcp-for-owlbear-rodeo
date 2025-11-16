# dev_mcp_server.py
# 本地调试用，不会打包发出去

from docs_server.server import create_server

# 调你真正的工厂，保持源码不动
server = create_server()

# 关键一步：把要自动安装的依赖清掉，避免 mcp dev 去 PyPI 找不到
server.dependencies = []
