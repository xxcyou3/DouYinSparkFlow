import traceback
from utils.logger import setup_logger
from utils.config import get_config, get_userData
from core.msg_builder import build_message, build_message_with_openai
from core.browser import get_browser
from playwright.sync_api import Response
import time
import json
import urllib.parse


complates = {}

config = get_config()
userData = get_userData()
logger = setup_logger(level=config.get("logLevel", "Info"))
matchMode = config.get("matchMode", "nickname")
userIDDict = {}

def handle_response(response: Response):
    """
    只监听你要的那个接口响应
    """
    global userIDDict
    # 精准匹配目标接口 URL
    if "aweme/v1/creator/im/user_detail/" in response.url:
        # print(f"URL: {response.url}")
        # print(f"状态码: {response.status}")
        try:
            # 获取接口返回的 JSON 数据（就是你在 Network 里看到的内容）
            json_data = response.json()
            # print("\n📦 响应 JSON 数据：")
            # print(json.dumps(json_data, indent=4, ensure_ascii=False))
            for item in json_data.get("user_list", []):
                short_id = item.get("user", {}).get("ShortId")
                nickname = item.get("user", {}).get("nickname")
                user_id = item.get("user_id", "")
                userIDDict[str(short_id)] = {"nickname": nickname, "user_id": user_id}
        except Exception as e:
            tb = traceback.extract_tb(e.__traceback__)
            last = tb[-1]
            print(f"解析响应失败: {e}")
            print(f"文件: {last.filename}, 行号: {last.lineno}, 函数: {last.name}")


def retry_operation(name, operation, retries=3, delay=2, *args, **kwargs):
    """
    通用的重试逻辑
    :param name: 操作名称（用于日志记录）
    :param operation: 要执行的异步操作
    :param retries: 最大重试次数
    :param delay: 每次重试之间的延迟（秒）
    :param args: 传递给操作的参数
    :param kwargs: 传递给操作的关键字参数
    """
    for attempt in range(retries):
        try:
            return operation(*args, **kwargs)
        except Exception as e:
            if attempt < retries - 1:
                logger.warning(f"{name} 失败，正在重试第 {attempt + 1} 次，错误：{e}")
                time.sleep(delay)
            else:
                logger.error(f"{name} 失败，已达到最大重试次数，错误：{e}")
                raise


def scroll_and_select_user(page, username, targets):
    """尝试滚动并查找用户名"""
    # 定义目标元素和滚动容器的选择器
    friends_tab_selector = 'xpath=//*[@id="sub-app"]/div/div/div[1]/div[2]'
    target_selector = 'xpath=//*[@id="sub-app"]/div/div[1]/div[2]/div[2]//div[contains(@class, "semi-list-item-body semi-list-item-body-flex-start")]'
    scrollable_friends_selector = 'xpath=//*[@id="sub-app"]/div/div[1]/div[2]/div[2]/div/div/div[3]/div/div/div/ul/div'
    
    # [修复] 使用模糊匹配 no-more-tip- 前缀，不再依赖精确哈希后缀
    # 同时增加文本匹配作为兜底
    no_more_selector = 'xpath=//div[contains(@class, "no-more-tip-")]'
    loading_selector = 'xpath=//div[contains(@class, "semi-spin")]'

    logger.debug(f"账号 {username} 开始查找目标好友列表")
    logger.debug(f"账号 {username} 目标好友列表: {targets}")

    logger.debug(f"账号 {username} 点击进入好友标签页")
    # 点击好友标签页
    page.wait_for_selector(friends_tab_selector)
    page.locator(friends_tab_selector).click()

    logger.debug(f"账号 {username} 进入好友列表页面")

    # 确保第一个好友元素加载完成
    first_friend_selector = 'xpath=//*[@id="sub-app"]/div/div/div[2]/div[2]/div/div/div[1]/div/div/div/ul/div/div/div[1]/li/div'
    page.wait_for_selector(first_friend_selector)
    page.locator(first_friend_selector).click()  # 点击第一个好友，确保列表激活

    logger.debug(f"账号 {username} 已激活好友列表，开始滚动查找目标好友")

    time.sleep(config["friendListTimeout"] / 1000)  # 等待好友列表加载

    found_targets = set()
    # [修改] 复制一份目标列表用于追踪进度
    remaining_targets = set(targets)

    # [修复] 新增：连续空滚动计数器（滚动后没有发现新好友的次数）
    empty_scroll_count = 0
    MAX_EMPTY_SCROLLS = 10  # 连续10次滚动没有新好友，认为到底了

    while True:
        # 查找所有目标元素
        target_elements = page.locator(target_selector).all()

        # [修复] 记录本轮循环前已发现的好友数，用于判断是否有新发现
        prev_found_count = len(found_targets)

        for element in target_elements:
            try:
                # 查找子元素 span，模糊匹配 class
                span = element.locator(
                    """xpath=.//span[contains(@class, "item-header-name-")]"""
                )
                targetName = span.inner_text()

                if targetName in found_targets:
                    continue  # 已处理过，跳过
                found_targets.add(targetName)

                logger.debug(f"账号 {username} 找到好友 {targetName}")
                # 检查是否是目标用户名
                if matchMode == "short_id":
                    targetSymbol = next((sid for sid, info in userIDDict.items() if info.get("nickname") == targetName), None)
                else:
                    targetSymbol = targetName

                if targetSymbol in targets:
                    element.click()
                    if matchMode == "short_id":
                        logger.debug(
                            f"账号 {username} 选中目标好友 {targetName} 准备开始交互"
                        )
                    else:
                        logger.debug(
                            f"账号 {username} 选中目标好友 {targetName} (ShortId: {targetSymbol}) 准备开始交互"
                        )
                    yield targetName
                    
                    # [修改] 标记已找到，如果全找到了直接退出
                    if targetSymbol in remaining_targets:
                        remaining_targets.remove(targetSymbol)
                    if len(remaining_targets) == 0:
                        logger.debug(f"账号 {username} 所有目标好友均已找到，停止搜索")
                        return
                    break
            except Exception as e:
                logger.error(
                    f"账号 {username} 处理好友元素时出错: {type(e).__name__}: {e}\n"
                    + traceback.format_exc()
                )
        else:
            # [修复] 检查本轮是否有新好友被发现
            new_found = len(found_targets) > prev_found_count
            if new_found:
                empty_scroll_count = 0  # 有新发现，重置计数器
            else:
                empty_scroll_count += 1  # 无新发现，递增计数器

            # [修复] 状态检测逻辑（多重兜底）
            
            # 1. 检查是否到底（"没有更多了" —— 使用模糊类名匹配）
            if page.locator(no_more_selector).count() > 0:
                logger.info(f"账号 {username} 检测到'没有更多了'标志，已到达底部")
                if len(remaining_targets) > 0:
                    logger.warning(f"账号 {username} 搜索结束，仍有以下好友未找到: {remaining_targets}")
                break

            # 2. [修复] 检查连续空滚动次数，防止死循环
            if empty_scroll_count >= MAX_EMPTY_SCROLLS:
                logger.warning(f"账号 {username} 连续 {MAX_EMPTY_SCROLLS} 次滚动未发现新好友，判定已到达底部")
                if len(remaining_targets) > 0:
                    logger.warning(f"账号 {username} 搜索结束，仍有以下好友未找到: {remaining_targets}")
                break

            # 3. 检查是否正在加载
            if page.locator(loading_selector).count() > 0:
                logger.debug(f"账号 {username} 列表正在加载中 (Loading)...")
                time.sleep(1.5) # 给加载留点时间
                # 不 break，继续去滚动以触发后续内容

            # 4. 滚动容器
            scrollable_element = page.locator(
                scrollable_friends_selector
            ).element_handle()
            
            if scrollable_element:
                # [修复] 记录滚动前的 scrollTop，用于检测是否真的滚动了
                scroll_top_before = page.evaluate(
                    "(element) => element.scrollTop", scrollable_element
                )
                
                page.evaluate(
                    "(element) => element.scrollTop += 800", scrollable_element
                )
                
                # [修复] 检测滚动后的 scrollTop
                time.sleep(0.3)
                scroll_top_after = page.evaluate(
                    "(element) => element.scrollTop", scrollable_element
                )
                
                if scroll_top_before == scroll_top_after:
                    # scrollTop 没有变化，说明已经到底了
                    empty_scroll_count += 2  # 加速判定到底
                    logger.debug(f"账号 {username} scrollTop 未变化 ({scroll_top_before})，可能已到底 (空滚动计数: {empty_scroll_count}/{MAX_EMPTY_SCROLLS})")
                else:
                    logger.debug(f"账号 {username} 滚动好友列表以加载更多好友 (scrollTop: {scroll_top_before} -> {scroll_top_after})")
                
                time.sleep(1.5)
            else:
                logger.error(f"账号 {username} 未找到滚动容器，退出")
                break


def do_user_task(browser, username, cookies, targets):
        page = None
        context = None
        try:
            # [修复] 伪装浏览器指纹：真实 Chrome UA + zh-CN 语言 + Asia/Shanghai 时区
            # 默认 Playwright UA 包含 "HeadlessChrome" 字样，抖音会拒绝登录态
            CHROME_UA = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            )
            # === [深度修复] 构建 Playwright storage_state（含 cookies + localStorage/sessionStorage 指纹） ===
            # 把已扩展的 cookies 直接放在 storage_state 里，而不是先 goto 再 add_cookies，
            # 这样首次网络请求就会带着完整的 Cookie 头，避免先以未登录状态访问被抖音风控标记。
            storage_state = {
                "cookies": [],
                "origins": [
                    {
                        "origin": "https://creator.douyin.com",
                        "localStorage": [
                            {"name": "douyin_sparkflow_ua_spoof", "value": "chrome_128"},
                            {"name": "is_secondary_user", "value": "false"},
                        ],
                        "sessionStorage": [
                            {"name": "douyin_sparkflow_session", "value": "1"},
                        ],
                    },
                    {
                        "origin": "https://www.douyin.com",
                        "localStorage": [
                            {"name": "douyin_sparkflow_ua_spoof", "value": "chrome_128"},
                        ],
                    },
                    {
                        "origin": "https://passport.douyin.com",
                        "localStorage": [
                            {"name": "douyin_sparkflow_ua_spoof", "value": "chrome_128"},
                        ],
                    },
                ],
            }
            for c in cookies:
                # storage_state 需要 integer expires: 用 -1 表示 session
                expires = c.get("expires")
                if expires is None or (isinstance(expires, (int, float)) and expires <= 0):
                    expires_int = -1
                else:
                    try:
                        expires_int = int(float(expires))
                    except Exception:
                        expires_int = -1
                sc = {
                    "name": str(c.get("name", "")),
                    "value": str(c.get("value", "")),
                    "domain": str(c.get("domain", ".douyin.com")),
                    "path": str(c.get("path", "/") or "/"),
                    "expires": expires_int,
                    "httpOnly": bool(c.get("httpOnly", False)),
                    "secure": bool(c.get("secure", c.get("domain", "").endswith("douyin.com"))),
                    "sameSite": "Lax",
                }
                storage_state["cookies"].append(sc)

            # 把 cookies 转成请求头拦截用的 header 格式（key=value; key2=value2），按 domain 分桶
            def _build_cookie_header_for(url):
                host = ""
                try:
                    host = urllib.parse.urlparse(url).hostname or ""
                except Exception:
                    host = ""
                lhost = host.lower()
                parts = []
                seen_names = set()
                for c in cookies:
                    dom = (c.get("domain") or "").lower()
                    if not dom or not (lhost == dom or dom.startswith(".") and (lhost.endswith(dom) or "." + lhost == dom)):
                        continue
                    n = str(c.get("name", ""))
                    if not n or n in seen_names:
                        continue
                    v = str(c.get("value", "") if c.get("value") is not None else "")
                    seen_names.add(n)
                    parts.append(f"{n}={v}")
                return "; ".join(parts)

            # extra_http_headers 用于给所有请求兜底塞 Cookie
            extra_http_headers = {}
            default_cookie_hdr = _build_cookie_header_for("https://creator.douyin.com")
            if default_cookie_hdr:
                extra_http_headers["Cookie"] = default_cookie_hdr

            context = browser.new_context(
                user_agent=CHROME_UA,
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                color_scheme="light",
                viewport={"width": 1440, "height": 900},
                device_scale_factor=1,
                is_mobile=False,
                has_touch=False,
                java_script_enabled=True,
                storage_state=storage_state,
                extra_http_headers=extra_http_headers if extra_http_headers else None,
            )
            context.set_default_navigation_timeout(config["browserTimeout"])
            context.set_default_timeout(config["browserTimeout"])

            # === [深度修复] 注册 request 拦截：强制把 Cookie 塞进请求头 ===
            # 即使 StorageState / add_cookies 因为 domain/path/httpOnly 匹配失败没生效，
            # 这里也会兜底把 sessionid / passport 等关键字段塞进请求头，让服务器认为已登录。
            # 只对 douyin 相关域名生效，避免影响 CDN/第三方。
            first_request_logged = {"v": False}
            def _on_request(route, request):
                url = request.url
                try:
                    host = urllib.parse.urlparse(url).hostname or ""
                except Exception:
                    host = ""
                # 只拦截 douyin / snssdk 核心域
                is_douyin = any(
                    host.endswith(d) or host == d.lstrip(".")
                    for d in (
                        ".douyin.com", "douyin.com", ".creator.douyin.com", "creator.douyin.com",
                        ".passport.douyin.com", "passport.douyin.com", ".www.douyin.com", "www.douyin.com",
                        ".snssdk.com", ".iesdouyin.com", ".bytedance.com", "www.tiktok-cn.com",
                    )
                )
                if is_douyin:
                    h = dict(request.headers or {})
                    cookie_hdr = _build_cookie_header_for(url)
                    if cookie_hdr:
                        h["Cookie"] = cookie_hdr
                    # 确保 Host 正确，避免被抖音反爬虫识别
                    # 不修改 method/post_data，原样转发
                    if not first_request_logged["v"]:
                        first_request_logged["v"] = True
                        logger.debug(
                            f"账号 {username} 首次拦截核心域请求 {request.method} {url[:120]} "
                            f"CookieHeader长度={len(cookie_hdr)}"
                        )
                    route.continue_(headers=h)
                else:
                    route.continue_()
            try:
                context.route("**/*", _on_request)
            except Exception:
                pass

            page = context.new_page()

            # [修复] 注入 webdriver 检测绕过 + 常用反爬虫指纹伪装（navigator.webdriver=false, chrome.runtime, permissions, plugins 等）
            try:
                page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                    Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
                    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN','zh','en-US','en'] });
                    try { window.chrome = { runtime: {} }; } catch(e){}
                    try { const originalQuery = window.navigator.permissions.query;
                          window.navigator.permissions.query = (p) =>
                            (p && (p.name === 'notifications' || p.name === 'persistent-storage'))
                              ? Promise.resolve({ state: Notification.permission })
                              : originalQuery(p);
                    } catch(e){}
                """)
            except Exception:
                pass

            if matchMode == "short_id":  # 使用抖音号进行匹配
                page.on("response", handle_response)

            # ==== [深度修复] 登录流程（顺序调整）：
            # 1) 先 add_cookies 一次 + 补一次到 context（双重保险，storage_state 已注入，这里作为兜底）
            # 2) 直接 goto creator.douyin.com 主域 / 消息页面，不要先访问空白页再跳转空白页
            # ====
            # 兜底：再调用 add_cookies 一次
            try:
                context.add_cookies(cookies)
            except Exception as e:
                logger.warning(f"账号 {username} context.add_cookies 失败（将通过 storage_state + 请求拦截兜底）: {e}")
            logger.debug(f"账号 {username} 已注入 {len(cookies)} 个 Cookie（含 storage_state + add_cookies + 请求拦截三重保险）")

            retry_operation(
                "导航到消息页面",
                page.goto,
                retries=config["taskRetryTimes"],
                delay=5,
                url="https://creator.douyin.com/creator-micro/data/following/chat",
                wait_until="domcontentloaded",
            )
            # 关键：重新加载一次确保 Cookie 实际生效到请求头
            logger.debug(f"账号 {username} Reload 页面以携带 Cookie")
            try:
                page.reload(wait_until="domcontentloaded")
            except Exception as e:
                logger.warning(f"Reload 超时，忽略继续: {e}")
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            # 再留一点时间给前端 React 渲染
            time.sleep(4)

            # 登录校验
            cur_url = page.url
            logger.debug(f"账号 {username} 当前页面 URL: {cur_url}")
            if any(k in cur_url.lower() for k in ["login", "passport", "signin", "sso"]):
                logger.error(f"账号 {username} 页面跳转到了登录页 URL={cur_url}，Cookie 未生效或已过期，请重新获取 Cookie")
                try: page.screenshot(path=f"logs/{username}_login_redirect.png", full_page=True)
                except Exception: pass
                raise RuntimeError(f"Cookie 无效，跳转到登录页: {cur_url}")

            try:
                body_text = page.evaluate("() => document.body ? document.body.innerText.slice(0, 8000) : ''")
            except Exception:
                body_text = ""
            # [深度修复] 额外记录：document.cookie 里能看到什么非 httpOnly cookie
            try:
                doc_cookie = page.evaluate("() => (document.cookie || '').slice(0, 2000)")
            except Exception:
                doc_cookie = ""
            logger.debug(f"账号 {username} document.cookie 可见部分（非 httpOnly）: {doc_cookie}")

            login_keywords = ["扫码登录", "请先登录", "立即登录", "未登录", "登录创作者平台", "手机号登录", "密码登录", "验证后可继续", "登录/注册"]
            hit_keywords = [kw for kw in login_keywords if kw in body_text]

            # 登录成功特征：body 里出现「数据中心」「互动管理」「聊天」「私信」「粉丝」任一
            login_hints = ["数据中心", "互动管理", "作品管理", "私信管理", "消息中心", "朋友私信", "粉丝管理", "聊天", "主页", "会话", "发送消息", "全部朋友"]
            hint_hit = [h for h in login_hints if h in body_text]

            # [深度修复] 如果命中登录关键字但 URL 正确且没跳转，可能是抖音登录弹窗/未登录组件，不是完全未登录。
            # 先尝试关闭登录弹窗再检查一次（用常见弹窗选择器 + ESC 键）
            if hit_keywords and not hint_hit:
                logger.warning(f"账号 {username} 疑似仍显示登录相关文字（{hit_keywords}），尝试关闭登录弹窗后重新检查一次")
                try:
                    # 常见抖音未登录弹窗关闭按钮：X 图标 / 关闭 / 取消 / "我是创作者" / ESC
                    close_xp = "xpath=//div[contains(@class,'close') or contains(@class,'icon-close') or contains(text(),'关闭') or contains(text(),'取消') or @aria-label='关闭']"
                    if page.locator(close_xp).count() > 0:
                        for i in range(min(3, page.locator(close_xp).count())):
                            try:
                                page.locator(close_xp).nth(i).click(timeout=1500)
                            except Exception:
                                pass
                    # 点"我是创作者"按钮（如果存在）
                    creator_btn = "xpath=//div[contains(@class,'tab') and (contains(text(),'我是创作者') or contains(text(),'创作者登录'))]"
                    if page.locator(creator_btn).count() > 0:
                        try: page.locator(creator_btn).first.click(timeout=1500)
                        except Exception: pass
                    page.keyboard.press("Escape")
                    time.sleep(2)
                    try:
                        body_text2 = page.evaluate("() => document.body ? document.body.innerText.slice(0, 8000) : ''")
                    except Exception:
                        body_text2 = ""
                    hint_hit2 = [h for h in login_hints if h in body_text2]
                    hit_keywords2 = [kw for kw in login_keywords if kw in body_text2]
                    if hint_hit2:
                        hint_hit = hint_hit2
                        hit_keywords = hit_keywords2
                        body_text = body_text2
                except Exception:
                    pass

            if hit_keywords and not hint_hit:
                logger.error(
                    f"账号 {username} 页面检测到登录关键字 {hit_keywords}，且没有登录成功特征 {hint_hit}。"
                    f" body 前 600 字：{body_text[:600]}"
                )
                try: page.screenshot(path=f"logs/{username}_need_login.png", full_page=True)
                except Exception: pass
                raise RuntimeError(f"Cookie 未生效，页面仍处于登录态检测：{hit_keywords}")

            logger.debug(
                f"账号 {username} 登录校验通过（登录特征命中: {hint_hit}，URL={cur_url}），开始发送消息流程"
            )

            logger.debug(f"账号 {username} 开始发送消息")
            any_matched = False
            for friend_name in scroll_and_select_user(page, username, targets):
                any_matched = True
                logger.debug(f"账号 {username} 已选中好友 {friend_name} 发送消息")
                chat_input_selector = "xpath=//div[contains(@class, 'chat-input-')]"
                page.wait_for_selector(chat_input_selector, timeout=config["browserTimeout"])
                chat_input = page.locator(chat_input_selector)

                message = build_message()
                lines = message.split("\\n")
                for i, line in enumerate(lines):
                    chat_input.type(line)
                    if i != len(lines) - 1:
                        chat_input.press("Shift+Enter")

                logger.info(
                    f"账号 {username} 准备发送消息给好友 {friend_name}：\n\t{message}"
                )
                chat_input.press("Enter")
                logger.info(f"账号 {username} 给好友 {friend_name} 发送消息完成")
                time.sleep(2)

            if not any_matched:
                logger.warning(
                    f"账号 {username} 未匹配到任何目标好友 targets={targets}（matchMode={matchMode}）。"
                    + (" 请确认 targets 中填写的是【好友抖音号】。若创作者中心接口没有回调 ShortId 数据（userIDDict 为空），请改用 nickname 匹配模式并填好友原始昵称。" if matchMode == "short_id" else "")
                )
                if matchMode == "short_id":
                    logger.warning(
                        f"账号 {username} 当前已收集 userIDDict 条目数: {len(userIDDict)}，内容: {json.dumps(userIDDict, ensure_ascii=False)[:800]}"
                    )
                try: page.screenshot(path=f"logs/{username}_no_match.png", full_page=True)
                except Exception: pass

        except Exception as e:
            try:
                if page is not None:
                    page.screenshot(path=f"logs/{username}_ERROR.png", full_page=True)
                    logger.error(f"账号 {username} 已保存异常截图 logs/{username}_ERROR.png")
                    with open(f"logs/{username}_ERROR_page.html", "w", encoding="utf-8") as f:
                        try: f.write(page.content())
                        except Exception: pass
            except Exception: pass
            logger.error(
                f"账号 {username} 执行任务时发生异常: {type(e).__name__}: {e}\n"
                + traceback.format_exc()
            )
            raise
        finally:
            try:
                if context is not None:
                    context.close()
            except Exception: pass


def runTasks():
    playwright, browser = get_browser()
    try:
        # 检查是否启用多任务和任务数量
        # 创建信号量以限制并发任务数量
        logger.info("开始执行任务")
        logger.debug(f"当前配置如下：")
        logger.debug(f"消息模板: {config.get('messageTemplate', '未找到消息模板')}")
        logger.debug(f"一言类型: {config['hitokotoTypes']}")
        for user in userData:
            logger.debug(f"用户: {user.get('username', '未知用户')}, 目标好友: {user['targets']}")

        for user in userData:
            cookies = user["cookies"]
            targets = user["targets"]
            complates[user["unique_id"]] = []  # 初始化该用户的已完成列表
            username = user.get("username", "未知用户")
            logger.info(f"开始处理账号 {username}")
            # 创建任务
            do_user_task(browser, username, cookies, targets)
            logger.info(f"账号 {username} 任务完成")
    finally:
        # 关闭浏览器实例
        browser.close()
        
        playwright.stop()

        

