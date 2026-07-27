import traceback
from utils.logger import setup_logger
from utils.config import get_config, get_userData
from utils import norm
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


CONVERSATION_ITEM_SELECTOR = '[class*="conversationConversationItem"]'
CONVERSATION_TITLE_SELECTOR = '[class*="conversationConversationItemtitle"]'
CONVERSATION_LIST_SELECTOR = '[class*="conversationConversationList"]'
CHAT_EDITOR_SELECTOR = '[class*="messageEditorimChatEditorContainer"]'
SEARCH_INPUT_SELECTOR = '[class*="search-input"]'


def handle_response(response: Response):
    """监听聊天页 user/info 接口响应，收集多种匹配键（备注/昵称/抖音号/unique_id/sec_uid）。"""
    global userIDDict
    if "aweme/v1/web/im/user/info" in response.url:
        try:
            json_data = response.json()
            for item in json_data.get("data", []):
                short_id = item.get("short_id") or ""
                unique_id = item.get("unique_id") or ""
                sec_uid = item.get("sec_uid", "") or ""
                nickname = norm(item.get("nickname"))
                remark_name = norm(item.get("remark_name")) or nickname
                info = {
                    "short_id": str(short_id),
                    "unique_id": str(unique_id),
                    "sec_uid": str(sec_uid),
                    "nickname": nickname,
                    "remark_name": remark_name,
                }
                keys = set()
                for v in (remark_name, nickname, str(short_id), str(unique_id), sec_uid):
                    vv = norm(v)
                    if vv:
                        keys.add(vv)
                for k in keys:
                    userIDDict[k] = info
        except Exception as e:
            tb = traceback.extract_tb(e.__traceback__)
            last = tb[-1]
            logger.debug(f"解析 web/im/user/info 响应失败: {e} @ {last.filename}:{last.lineno}")


def retry_operation(name, operation, retries=3, delay=2, *args, **kwargs):
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


def checkTargetName(targetName, targets):
    """5 种键匹配：备注名/昵称/short_id/unique_id/sec_uid，兼容 matchMode 模式。"""
    targetName = norm(targetName)
    if not targetName:
        return None

    if matchMode == "short_id":
        info = userIDDict.get(targetName)
        if info:
            for field in ("short_id", "unique_id", "sec_uid", "nickname", "remark_name"):
                v = norm(info.get(field))
                if v and v in targets:
                    return v
        if targetName in targets:
            return targetName
        return None

    if targetName in targets:
        return targetName
    info = userIDDict.get(targetName)
    if info:
        for field in ("nickname", "remark_name", "short_id", "unique_id"):
            v = norm(info.get(field))
            if v and v in targets:
                return v
    return None


def scroll_and_select_user(page, username, targets):
    """www.douyin.com/chat 会话列表：class 模糊匹配 + 滚动加载 + 智能匹配。"""
    logger.debug(f"账号 {username} 开始查找目标好友列表（www.douyin.com/chat），targets={targets}")

    found_targets = set()
    remaining_targets = set(targets)
    empty_scroll_count = 0
    MAX_EMPTY_SCROLLS = 10

    for _round in range(30):
        try:
            target_elements = page.locator(CONVERSATION_ITEM_SELECTOR).all()
        except Exception as e:
            logger.debug(f"查找会话项异常（第{_round}轮）: {e}")
            target_elements = []

        prev_found_count = len(found_targets)
        matched_in_round = False

        for element in target_elements:
            try:
                try:
                    title_el = element.locator(CONVERSATION_TITLE_SELECTOR).first
                    targetName = title_el.inner_text(timeout=1200) if title_el.count() > 0 else ""
                except Exception:
                    targetName = ""
                if not targetName:
                    try:
                        targetName = element.inner_text(timeout=1200).splitlines()[0]
                    except Exception:
                        targetName = ""
                targetName = norm(targetName)
                if not targetName or targetName in found_targets:
                    continue
                found_targets.add(targetName)
                logger.debug(f"账号 {username} 找到会话 {targetName!r}")

                targetSymbol = checkTargetName(targetName, targets)
                if targetSymbol:
                    try:
                        element.scroll_into_view_if_needed(timeout=1500)
                        element.click(timeout=2500)
                    except Exception as e:
                        logger.warning(f"点击会话项失败，重试: {e}")
                        try:
                            box = element.bounding_box()
                            if box:
                                page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                            else:
                                element.click(timeout=2500)
                        except Exception:
                            continue
                    time.sleep(1.5)
                    logger.info(f"账号 {username} 已选中目标会话 {targetName!r}（匹配键={targetSymbol!r}）")
                    matched_in_round = True
                    yield targetName
                    if targetSymbol in remaining_targets:
                        remaining_targets.remove(targetSymbol)
                    if targetName in remaining_targets:
                        remaining_targets.remove(targetName)
                    if len(remaining_targets) == 0:
                        logger.info(f"账号 {username} 所有目标好友均已处理完成")
                        return
                    break
            except Exception as e:
                logger.debug(f"处理会话元素异常: {type(e).__name__}: {e}")

        if len(found_targets) > prev_found_count or matched_in_round:
            empty_scroll_count = 0
        else:
            empty_scroll_count += 1

        if empty_scroll_count >= MAX_EMPTY_SCROLLS:
            logger.warning(f"账号 {username} 连续 {MAX_EMPTY_SCROLLS} 次滚动无新会话，停止搜索")
            if remaining_targets:
                logger.warning(f"仍未匹配的目标: {remaining_targets}")
            break

        try:
            scroll_el = page.locator(CONVERSATION_LIST_SELECTOR).first
            if scroll_el.count() > 0:
                eh = scroll_el.element_handle()
                if eh:
                    st_before = page.evaluate("e => e.scrollTop", eh)
                    page.evaluate("e => e.scrollTop += 600", eh)
                    time.sleep(0.3)
                    st_after = page.evaluate("e => e.scrollTop", eh)
                    if st_before == st_after:
                        empty_scroll_count += 1
                        logger.debug(f"scrollTop 未变 ({st_before})，可能到底")
                    else:
                        logger.debug(f"会话列表滚动 {st_before}->{st_after}")
                else:
                    page.evaluate("window.scrollBy(0, 600)")
            else:
                page.evaluate("window.scrollBy(0, 600)")
        except Exception as e:
            logger.debug(f"滚动异常: {e}")
            try:
                page.evaluate("window.scrollBy(0, 600)")
            except Exception:
                pass
        time.sleep(1.2)

    if remaining_targets:
        logger.warning(f"账号 {username} 结束搜索，仍未匹配: {remaining_targets}（userIDDict 当前条目={len(userIDDict)}）")
        if len(userIDDict) < 5:
            logger.debug(f"userIDDict 内容: {json.dumps(userIDDict, ensure_ascii=False)[:800]}")


def do_user_task(browser, username, cookies, targets):
    page = None
    context = None
    try:
        CHROME_UA = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        )

        storage_state = {
            "cookies": [],
            "origins": [
                {
                    "origin": "https://www.douyin.com",
                    "localStorage": [
                        {"name": "douyin_sparkflow_ua_spoof", "value": "chrome_128"},
                    ],
                },
                {
                    "origin": "https://creator.douyin.com",
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
                "secure": bool(c.get("secure", True)),
                "sameSite": "Lax",
            }
            storage_state["cookies"].append(sc)

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
                if not dom or not (
                    lhost == dom
                    or (dom.startswith(".") and (lhost.endswith(dom) or "." + lhost == dom))
                ):
                    continue
                n = str(c.get("name", ""))
                if not n or n in seen_names:
                    continue
                v = str(c.get("value") if c.get("value") is not None else "")
                seen_names.add(n)
                parts.append(f"{n}={v}")
            return "; ".join(parts)

        extra_http_headers = {}
        default_cookie_hdr = _build_cookie_header_for("https://www.douyin.com")
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

        first_request_logged = {"v": False}

        def _on_request(route, request):
            url = request.url
            try:
                host = urllib.parse.urlparse(url).hostname or ""
            except Exception:
                host = ""
            is_douyin = any(
                host.endswith(d) or host == d.lstrip(".")
                for d in (
                    ".douyin.com",
                    "douyin.com",
                    ".creator.douyin.com",
                    "creator.douyin.com",
                    ".passport.douyin.com",
                    "passport.douyin.com",
                    ".www.douyin.com",
                    "www.douyin.com",
                    ".snssdk.com",
                    ".iesdouyin.com",
                    ".bytedance.com",
                )
            )
            if is_douyin:
                h = dict(request.headers or {})
                cookie_hdr = _build_cookie_header_for(url)
                if cookie_hdr:
                    h["Cookie"] = cookie_hdr
                if not first_request_logged["v"]:
                    first_request_logged["v"] = True
                    logger.debug(
                        f"账号 {username} 首次拦截核心域 {request.method} {url[:100]} "
                        f"CookieLen={len(cookie_hdr)}"
                    )
                route.continue_(headers=h)
            else:
                route.continue_()

        try:
            context.route("**/*", _on_request)
        except Exception:
            pass

        page = context.new_page()

        try:
            page.add_init_script(
                """
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN','zh','en-US','en'] });
                try { window.chrome = { runtime: {} }; } catch(e){}
                try {
                    const oq = window.navigator.permissions.query;
                    window.navigator.permissions.query = (p) =>
                      (p && (p.name === 'notifications' || p.name === 'persistent-storage'))
                        ? Promise.resolve({ state: Notification.permission })
                        : oq(p);
                } catch(e){}
            """
            )
        except Exception:
            pass

        page.on("response", handle_response)

        try:
            context.add_cookies(cookies)
        except Exception as e:
            logger.warning(f"context.add_cookies 失败（将通过 storage_state+拦截兜底）: {e}")
        logger.debug(f"账号 {username} 已注入 {len(cookies)} 条 Cookie（三重保险）")

        retry_operation(
            "导航到抖音聊天页",
            page.goto,
            retries=config["taskRetryTimes"],
            delay=5,
            url="https://www.douyin.com/chat",
            wait_until="domcontentloaded",
        )
        try:
            page.reload(wait_until="domcontentloaded")
        except Exception as e:
            logger.warning(f"聊天页 reload 超时，忽略继续: {e}")
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        time.sleep(5)

        cur_url = page.url
        logger.debug(f"账号 {username} 当前页面 URL: {cur_url}")
        if any(k in cur_url.lower() for k in ["login", "passport", "signin", "sso"]):
            logger.error(f"跳转到登录页 URL={cur_url}，Cookie 无效或过期")
            try:
                page.screenshot(path=f"logs/{username}_login_redirect.png", full_page=True)
            except Exception:
                pass
            raise RuntimeError(f"Cookie 无效，跳转到登录页: {cur_url}")

        try:
            body_text = page.evaluate("() => document.body ? document.body.innerText.slice(0, 8000) : ''")
        except Exception:
            body_text = ""
        try:
            doc_cookie = page.evaluate("() => (document.cookie || '').slice(0, 2000)")
        except Exception:
            doc_cookie = ""
        logger.debug(f"document.cookie（非httpOnly）: {doc_cookie[:500]}")

        login_keywords = ["扫码登录", "请先登录", "立即登录", "未登录", "手机号登录", "密码登录", "登录/注册"]
        hit_keywords = [kw for kw in login_keywords if kw in body_text]
        login_hints = [
            "聊天", "消息", "会话", "发送", "朋友", "粉丝",
            "conversation", "搜索", "聊天记录",
        ]
        hint_hit = [h for h in login_hints if h in body_text]

        if hit_keywords and not hint_hit:
            logger.warning(f"疑似登录组件未关闭（{hit_keywords}），尝试关闭弹窗/ESC")
            try:
                close_xp = "xpath=//div[contains(@class,'close') or contains(@class,'icon-close') or contains(text(),'关闭') or contains(text(),'取消') or @aria-label='关闭']"
                if page.locator(close_xp).count() > 0:
                    for i in range(min(3, page.locator(close_xp).count())):
                        try:
                            page.locator(close_xp).nth(i).click(timeout=1200)
                        except Exception:
                            pass
                page.keyboard.press("Escape")
                time.sleep(2)
                try:
                    body_text2 = page.evaluate("() => document.body ? document.body.innerText.slice(0, 8000) : ''")
                except Exception:
                    body_text2 = ""
                hint_hit2 = [h for h in login_hints if h in body_text2]
                if hint_hit2:
                    hint_hit = hint_hit2
                    body_text = body_text2
            except Exception:
                pass

        if hit_keywords and not hint_hit:
            logger.error(
                f"登录校验失败：命中 {hit_keywords}，未发现聊天特征。"
                f" body前600字：{body_text[:600]}"
            )
            try:
                page.screenshot(path=f"logs/{username}_need_login.png", full_page=True)
                with open(f"logs/{username}_need_login.html", "w", encoding="utf-8") as f:
                    f.write(page.content())
            except Exception:
                pass
            raise RuntimeError(f"Cookie 未生效，页面仍显示登录提示：{hit_keywords}")

        logger.info(
            f"账号 {username} 进入聊天页成功（特征命中={hint_hit}，URL={cur_url[:120]}），开始匹配好友"
        )
        try:
            logger.debug(f"页面文本前1200字：{body_text[:1200]}")
        except Exception:
            pass

        any_matched = False
        for friend_name in scroll_and_select_user(page, username, targets):
            any_matched = True
            logger.info(f"账号 {username} 选中好友/会话 {friend_name!r}，准备发送消息")
            try:
                page.wait_for_selector(CHAT_EDITOR_SELECTOR, timeout=20000)
            except Exception as e:
                logger.warning(f"未找到聊天输入框 {CHAT_EDITOR_SELECTOR}，尝试降级选择器: {e}")
                fallback = (
                    '[class*="chat-input"]',
                    '[contenteditable="true"]',
                    'div[class*="input"]',
                    'textarea',
                )
                for fs in fallback:
                    try:
                        if page.locator(fs).count() > 0:
                            logger.debug(f"使用降级输入框选择器: {fs}")
                            break
                    except Exception:
                        continue
            chat_input = page.locator(CHAT_EDITOR_SELECTOR).first
            if chat_input.count() == 0:
                for fs in ('[class*="chat-input"]', '[contenteditable="true"]', 'div[class*="input"]'):
                    try:
                        if page.locator(fs).count() > 0:
                            chat_input = page.locator(fs).first
                            break
                    except Exception:
                        pass

            message = build_message()
            lines = message.split("\\n")
            for i, line in enumerate(lines):
                try:
                    chat_input.type(line, timeout=5000)
                except Exception as e:
                    logger.warning(f"输入行失败，尝试 click 聚焦后重输: {e}")
                    try:
                        chat_input.click(timeout=2000)
                        time.sleep(0.3)
                        chat_input.type(line, timeout=5000)
                    except Exception as e2:
                        logger.error(f"仍无法输入，跳过该好友: {e2}")
                        break
                if i != len(lines) - 1:
                    chat_input.press("Shift+Enter")
                    time.sleep(0.1)

            logger.info(f"账号 {username} -> {friend_name!r} 准备发送：\n\t{message}")
            try:
                send_btns = [
                    'button[class*="send"]',
                    'div[class*="send-button"]',
                    'svg[class*="send"]',
                    '[aria-label="发送"]',
                ]
                clicked_send = False
                for sb in send_btns:
                    try:
                        if page.locator(sb).count() > 0:
                            page.locator(sb).first.click(timeout=1500)
                            clicked_send = True
                            logger.debug(f"点击发送按钮 {sb}")
                            break
                    except Exception:
                        continue
                if not clicked_send:
                    chat_input.press("Enter")
            except Exception:
                chat_input.press("Enter")
            logger.info(f"账号 {username} -> {friend_name!r} 消息发送完成")
            time.sleep(2)

        if not any_matched:
            logger.warning(
                f"账号 {username} 未匹配到任何目标会话 targets={targets}（matchMode={matchMode}）。"
                + (" 若 userIDDict 为空，请确认抖音号/unique_id 是否可被 web/im/user/info 接口返回。" if matchMode == "short_id" else "")
            )
            try:
                page.screenshot(path=f"logs/{username}_no_match.png", full_page=True)
                with open(f"logs/{username}_no_match.html", "w", encoding="utf-8") as f:
                    f.write(page.content())
            except Exception:
                pass

    except Exception as e:
        try:
            if page is not None:
                try:
                    page.screenshot(path=f"logs/{username}_ERROR.png", full_page=True)
                    with open(f"logs/{username}_ERROR_page.html", "w", encoding="utf-8") as f:
                        try:
                            f.write(page.content())
                        except Exception:
                            pass
                except Exception:
                    pass
                logger.error(f"已保存异常截图 logs/{username}_ERROR.png")
        except Exception:
            pass
        logger.error(
            f"账号 {username} 执行任务异常: {type(e).__name__}: {e}\n" + traceback.format_exc()
        )
        raise
    finally:
        try:
            if context is not None:
                context.close()
        except Exception:
            pass


def runTasks():
    playwright, browser = get_browser()
    try:
        logger.info("开始执行任务")
        logger.debug(f"当前配置如下：")
        logger.debug(f"消息模板: {config.get('messageTemplate', '未找到消息模板')}")
        logger.debug(f"一言类型: {config['hitokotoTypes']}")
        for user in userData:
            logger.debug(f"用户: {user.get('username', '未知用户')}, 目标好友: {user['targets']}")

        for user in userData:
            cookies = user["cookies"]
            targets = user["targets"]
            complates[user["unique_id"]] = []
            uname = user.get("username", "未知用户")
            logger.info(f"开始处理账号 {uname}")
            do_user_task(browser, uname, cookies, targets)
            logger.info(f"账号 {uname} 任务完成")
    finally:
        browser.close()
        playwright.stop()
