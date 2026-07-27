import os, sys
from enum import Enum
import json
import logging
from utils.logger import setup_logger

logger = setup_logger(level=logging.DEBUG)

"""
是否启用调试模式
更详细的日志打印，浏览器操作可视化等
"""
DEBUG = True
config = None
userData = None


class Environment(Enum):
    GITHUBACTION = "GITHUB_ACTION"  # GitHub Action 运行
    LOCAL = "LOCAL"  # 本地代码运行
    PACKED = "PACKED"  # PyInstaller 打包运行

    def __str__(self):
        return self.value


def get_environment():
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Environment.PACKED
    elif os.getenv("GITHUB_ACTIONS") == "true":
        return Environment.GITHUBACTION
    else:
        return Environment.LOCAL


def get_config():
    """
    获取配置信息
    :return: 配置字典
    """
    global config

    if config:
        return config

    config = {
        "proxyAddress": os.getenv("PROXY_ADDRESS", ""),
        "messageTemplate": os.getenv("MESSAGE_TEMPLATE", "[盖瑞]今日火花[加一]\\n—— [右边] 每日一言 [左边] ——\\n[API]"),
        "hitokotoTypes": json.loads(
            os.getenv("HITOKOTO_TYPES", '["文学","影视","诗词","哲学"]')
        ),
        "matchMode": os.getenv("MATCH_MODE", "nickname"),  # 是否使用短 ID 进行好友匹配
        "browserTimeout": int(os.getenv("BROWSER_TIMEOUT", "120000")),  # 浏览器操作超时时间，单位毫秒
        "friendListTimeout": int(os.getenv("FRIEND_LIST_WAIT_TIME", "2000")),  # 好友列表加载超时时间，单位毫秒
        "taskRetryTimes": int(os.getenv("TASK_RETRY_TIMES", "3")),  # 任务重试次数
        "logLevel": os.getenv("LOG_LEVEL", "DEBUG"),  # 日志级别
    }

    return config

def sanitize_cookies(cookies):
    """
    清洗和扩展 Cookie：
    1) 移除 Playwright 1.40+ 不支持的 sameSite（非标准枚举值）和 expires（应该用 expires 时间戳 Unix timestamp，如果是字符串格式则移除）
    2) 如果传入的 domain 是 .douyin.com 这种顶级通配，同时再注入一份更具体域名的变体（.creator.douyin.com, creator.douyin.com, .passport.douyin.com, www.douyin.com），
       因为抖音不同子系统在不同子域名上会校验 Cookie，防止单点 domain 不全导致登录失效。
    3) 按 (name, domain, path) 去重
    """
    import time as _time
    cleaned = []
    seen = set()
    if not isinstance(cookies, list):
        return cleaned
    now_unix = int(_time.time())
    for c in cookies:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name", "")).strip()
        if not name:
            continue
        value = str(c.get("value", "")) if c.get("value") is not None else ""
        domain = c.get("domain", ".douyin.com")
        path = c.get("path", "/") or "/"
        # 移除不支持或错误的字段
        for bad_key in ("sameSite", "same_site", "priority", "sameparty", "sourceScheme", "sourcePort", "partitionKey"):
            c.pop(bad_key, None)
        # expires 处理：非整数（比如是 RFC 日期字符串）就移除；过期时间戳已经过了也移除
        if "expires" in c:
            exp = c["expires"]
            if isinstance(exp, (int, float)):
                ts = int(exp)
                if 0 < ts < now_unix:  # 已经过期
                    continue
                c["expires"] = ts
            else:
                c.pop("expires", None)
        # 保证存在基础字段
        base = {"name": name, "value": value, "domain": domain, "path": path}
        for k, v in base.items():
            c[k] = v
        key = (name, domain, path)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(c)

    # 2. Domain 扩展：把 .douyin.com 的 cookie 克隆一份到常见子域名
    extra = []
    EXTRA_DOMAINS = (".creator.douyin.com", "creator.douyin.com",
                     ".passport.douyin.com", "passport.douyin.com",
                     ".www.douyin.com", "www.douyin.com",
                     ".douyin.com")  # 兜底
    for c in list(cleaned):
        dom = (c.get("domain") or "").lower()
        # 只对抖音相关主域 cookie 扩展
        if not (dom == ".douyin.com" or dom == "douyin.com" or dom.endswith(".douyin.com")):
            continue
        for ed in EXTRA_DOMAINS:
            if ed == dom:
                continue
            nc = dict(c)
            nc["domain"] = ed
            k = (nc["name"], nc["domain"], nc.get("path", "/"))
            if k in seen:
                continue
            seen.add(k)
            extra.append(nc)
    cleaned.extend(extra)
    return cleaned


def get_userData():
    """
    获取用户数据目录
    :return: 用户数据目录路径
    """
    global userData

    if userData:
        return userData

    tasks = json.loads(os.getenv("TASKS", "[]"))

    userData = []

    for task in tasks:
        username = task.get("username", "未知用户")
        unique_id = task.get("unique_id")
        if not unique_id:
            logger.warning(f"{username} 的任务  缺少 unique_id 字段，已跳过")
            continue
        cookies_key = f"cookies_{unique_id}".upper()
        raw_cookies_str = os.getenv(cookies_key, "")
        if not raw_cookies_str:
            logger.warning(
                f"{username} 的任务 缺少 {cookies_key} 环境变量，已跳过"
            )
            continue
        # [修复] 不要用 unicode_escape 转换，否则 cookie value 中 \u、%u、反斜杠等会被错误解码
        # 仅兜底：如果字符串首尾都带 " 或 ' ，做一次 strip，支持粘贴时多包了一层
        cookies_str = raw_cookies_str.strip()
        if (len(cookies_str) >= 2 and ((cookies_str[0] == '"' and cookies_str[-1] == '"')
                                        or (cookies_str[0] == "'" and cookies_str[-1] == "'"))):
            cookies_str = cookies_str[1:-1]
        try:
            cookies = json.loads(cookies_str)
        except json.JSONDecodeError:
            # 兼容：GitHub Actions 把变量里的 " 自动转义成 \"，这里尝试反解一次
            try:
                cookies = json.loads(cookies_str.encode("utf-8").decode("unicode_escape"))
                logger.warning(f"{username} 的任务 {cookies_key} 通过 unicode_escape 兼容解析成功（建议原始 Cookie JSON 不要多包一层引号）")
            except Exception:
                logger.warning(f"{username} 的任务 {cookies_key} 格式不正确，已跳过")
                continue

        userData.append(
            {
                "unique_id": unique_id,
                "username": username,
                "cookies": sanitize_cookies(cookies),
                "targets": task.get("targets", []),
            }
        )

    return userData
