import sys
import asyncio
from bilibili_api import sync, user

# 跨平台兼容补丁
if sys.platform == 'win32':
    # 强制 Windows 终端采用 UTF-8 编码规避中文视频标题打印乱码
    import os
    os.system('chcp 65001')
    # 修复 Windows 异步事件循环策略冲突
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

print("[SUCCESS] Mirror 蒸馏系统底层依赖环境配置完成！")