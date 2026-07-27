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


CONVERSATION_ITEM_SELECTOR = (
    '[class*="conversationConversationItem"],'
    '[class*="fans-item"],[class*="group-item"],'
    '[class*="fansItem"],[class*="groupItem"],'
    '[class*="messageItem"],[class*="message-item"],'
    '[class*="imItem"],[class*="im-item"],'
    '[class*="chatItem"],[class*="chat-item"],'
    '[class*="PrivateChat"] [role="button"],'
    '[class*="private-chat"] [class*="item"]'
)
CONVERSATION_TITLE_SELECTOR = (
    '[class*="conversationConversationItemtitle"],'
    '[class*="title"],[class*="name"],[class*="nickname"],'
    '[class*="fans-item__title"],[class*="group-item__title"]'
)
CONVERSATION_LIST_SELECTOR = (
    '[class*="conversationConversationList"],'
    '[class*="messageList"],[class*="message-list"],'
    '[class*="chat-list"],[class*="im-list"],[class*="fans-list"],[class*="group-list"]'
)
CHAT_EDITOR_SELECTOR = (
    '[class*="messageEditorimChatEditorContainer"],'
    '[contenteditable="true"][class*="editor"],'
    '[contenteditable="true"][class*="input"],'
    'textarea[class*="comment"],'
    'textarea[class*="editor"]'
)
SEARCH_INPUT_SELECTOR = '[class*="search-input"]'


def handle_response(response: Response):
    """监听多种接口响应（IM+搜索+关注+粉丝+资料），统一收集多种匹配键。"""
    global userIDDict
    try:
        url = response.url
        interested = (
            "aweme/v1/web/im/user/info" in url
            or "aweme/v1/creator/im/user_detail" in url
            or "aweme/v1/web/im/user/list" in url
            or "aweme/v1/web/im/conversation/list" in url
            or "aweme/v1/web/search/sug" in url
            or "aweme/v1/web/search/user" in url
            or "aweme/v1/user/profile" in url
            or "aweme/v1/web/user/profile" in url
            or "userRelation/v1" in url
            or "aweme/v1/following" in url
            or "aweme/v1/fans" in url
            or "im/user" in url or "conversation" in url or "im_friends" in url or "im/friends" in url
        )
        if not interested:
            return
        try:
            json_data = response.json()
        except Exception:
            return
        candidates_lists = []
        if isinstance(json_data, dict):
            for top in ("data", "user_list", "conversations", "conversation_list", "friends",
                        "users", "items", "list", "followings", "fans", "sug_list"):
                if isinstance(json_data.get(top), list):
                    candidates_lists.append(json_data[top])
            if isinstance(json_data.get("data"), dict):
                inner = json_data["data"]
                for kk in ("user_list", "data", "list", "conversations", "conversation_list",
                           "friends", "items", "users", "followings", "fans", "sug_list", "info", "user"):
                    if isinstance(inner.get(kk), list):
                        candidates_lists.append(inner[kk])
                    elif kk == "user" and isinstance(inner.get(kk), dict):
                        candidates_lists.append([inner[kk]])
        for items in candidates_lists:
            for raw in items:
                if not isinstance(raw, dict):
                    continue
                u = raw.get("user") if isinstance(raw.get("user"), dict) else raw
                short_id = str(u.get("short_id") or u.get("shortId") or raw.get("short_id") or "")
                unique_id = str(u.get("unique_id") or u.get("uniqueId") or raw.get("unique_id") or "")
                sec_uid = str(u.get("sec_uid") or u.get("secUid") or raw.get("sec_uid") or "")
                nickname = norm(u.get("nickname") or raw.get("nickname") or raw.get("display_nickname"))
                remark_name = norm(u.get("remark_name") or u.get("remarkName") or raw.get("remark_name")) or nickname
                user_id = str(u.get("user_id") or u.get("userId") or raw.get("user_id") or
                              u.get("uid") or raw.get("uid") or "")
                if not (short_id or unique_id or sec_uid or nickname or user_id):
                    continue
                info = {
                    "short_id": short_id,
                    "unique_id": unique_id,
                    "sec_uid": sec_uid,
                    "nickname": nickname,
                    "remark_name": remark_name,
                    "user_id": user_id,
                }
                keys = set()
                for v in (remark_name, nickname, short_id, unique_id, sec_uid, user_id):
                    vv = norm(v)
                    if vv:
                        keys.add(vv)
                for k in keys:
                    userIDDict[k] = info
    except Exception as e:
        tb = traceback.extract_tb(e.__traceback__)
        last = tb[-1] if tb else None
        logger.debug(f"解析接口响应失败: {e} @ {getattr(last,'filename','?')}:{getattr(last,'lineno','?')}")


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


def _close_login_save_popup(page, username):
    """关闭「是否保存登录信息？」/「下次登录更便捷」等阻塞弹窗（返回是否关闭了弹窗）。"""
    closed = False
    try:
        body = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
    except Exception:
        body = ""
    popup_kw = ["是否保存登录信息", "下次登录更便捷", "个人中心关闭"]
    if not any(k in body for k in popup_kw):
        return False
    logger.debug(f"账号 {username} 检测到保存登录信息弹窗，尝试关闭")
    # 优先点「取消」，再点「保存」，再按 ESC
    for text_match in ("取消", "保存", "关闭", "知道了", "好的", "下次再说", "稍后再说"):
        try:
            cand_xp = f"xpath=//*[self::button or self::div or self::span or self::a][normalize-space(string(.)) = {text_match!r}]"
            n = page.locator(cand_xp).count()
            if n == 0:
                cand_xp = f'xpath=//*[self::button or self::div or self::span or self::a][contains(normalize-space(string(.)), {text_match!r})]'
                n = page.locator(cand_xp).count()
            if n > 0:
                for i in range(min(n, 4)):
                    try:
                        loc = page.locator(cand_xp).nth(i)
                        if loc.is_visible(timeout=600):
                            loc.click(timeout=1200)
                            closed = True
                            time.sleep(0.8)
                            break
                    except Exception:
                        continue
                if closed:
                    break
        except Exception:
            continue
    try:
        page.keyboard.press("Escape")
        page.keyboard.press("Escape")
    except Exception:
        pass
    # 通用关闭 X 按钮
    try:
        close_xp = "xpath=//*[@aria-label='关闭' or contains(@class,'close') or contains(@class,'icon-close') or contains(@class,'Close') or contains(text(),'×')]"
        if page.locator(close_xp).count() > 0:
            for i in range(min(5, page.locator(close_xp).count())):
                try:
                    loc = page.locator(close_xp).nth(i)
                    if loc.is_visible(timeout=500):
                        loc.click(timeout=800)
                        closed = True
                        break
                except Exception:
                    continue
    except Exception:
        pass
    time.sleep(1.2)
    return closed


def _probe_im_apis(page, username):
    """主动调用聊天/搜索/关注相关接口，避免跨域，base 用当前 location.origin。"""
    global userIDDict
    snap = {}
    try:
        snap = page.evaluate("""async () => {
            const res = {};
            const origin = (location && location.origin) ? location.origin : 'https://www.douyin.com';
            const common = 'version_code=1700&device_platform=webapp&aid=6383&webcast_sdk_version=1700';
            const urls = [
                origin + '/aweme/v1/web/im/user/info/?cursor=0&user_source=0&count=100&' + common,
                origin + '/aweme/v1/web/im/conversation/list/?cursor=0&count=50&' + common,
                origin + '/aweme/v1/web/im/user/list/?cursor=0&count=100&' + common,
                'https://creator.douyin.com/aweme/v1/creator/im/user_detail/?user_source=0&count=100&cursor=0&' + common,
                origin + '/aweme/v1/user/following/list/?user_id=&max_time=0&count=50&' + common,
                origin + '/aweme/v1/web/following/list/?user_id=&max_time=0&count=50&' + common,
                origin + '/aweme/v1/user/follower/list/?user_id=&max_time=0&count=50&' + common,
                origin + '/aweme/v1/web/search/sug/?keyword=搜索&from_group_id=&source=general&type=1&' + common,
                origin + '/passport/web/user/info/?aid=6383&device_platform=webapp',
            ];
            for (let i = 0; i < urls.length; i++) {
                try {
                    const u = urls[i];
                    const r = await fetch(u, {credentials:'include', mode:'cors'}).catch(err => ({err}));
                    if (r && r.err) { res['e'+i] = String(r.err).slice(0,200); continue; }
                    const t = await r.text();
                    res['u'+i] = (t || '').slice(0, 500);
                    res['s'+i] = r.status;
                } catch(e) {
                    res['e'+i] = String(e).slice(0,200);
                }
                await new Promise(r => setTimeout(r, 350));
            }
            return res;
        }""")
        parts = []
        for k in sorted(snap.keys()):
            v = snap[k]
            sv = str(v)
            if sv[:4] in ("<htm",):
                continue
            if k.startswith("u"):
                sv = sv.replace("\n", " ")[:300]
            parts.append(f"{k}={sv!r}")
        logger.debug(f"账号 {username} 主动探测 9 个接口结果：" + " ".join(parts))
    except Exception as e:
        logger.debug(f"探测接口异常: {e}")
    time.sleep(2.5)
    return snap


def wait_chat_page_ready(page, username, max_wait=60):
    """轮询等待：关阻塞弹窗 + 骨架屏消失 + 有真实聊天内容（非空壳）。"""
    global userIDDict
    def _is_still_blocked():
        try:
            txt = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
        except Exception:
            txt = ""
        # 有阻塞弹窗/登录提示优先判定为阻塞
        block_kw = [
            "是否保存登录信息", "下次登录更便捷", "个人中心关闭",
            "扫码登录", "请先登录", "立即登录", "手机号登录", "密码登录",
            "请使用抖音扫码", "请使用抖音APP扫码", "登录后查看", "登录后可查看",
            "短信登录", "验证码登录", "验证手机号",
        ]
        if any(k in txt for k in block_kw):
            return True
        lines = [l.strip() for l in txt.splitlines() if l.strip()]
        # 空壳拦截：body 可见字符 < 50，或者行数 <= 3（只有读屏标签等极少内容） → 还没加载出来
        if len(lines) <= 3 or len("".join(lines)) < 50:
            return True
        # 骨架屏：有「抖音聊天」但没有真实 UI 文字
        if "抖音聊天" in txt or "聊天" in txt and len(lines) <= 5:
            extra = ["发送", "消息", "会话", "朋友", "粉丝", "好友", "聊天记录", "搜索", "今日", "昨天", "刚刚", "私信", "全部", "消息中心"]
            if not any(k in txt for k in extra):
                return True
        return False

    start = time.time()
    skel_rounds = 0
    closed_popup_once = False
    while time.time() - start < max_wait:
        if _close_login_save_popup(page, username):
            closed_popup_once = True
        if not _is_still_blocked():
            if len(userIDDict) == 0 and skel_rounds >= 2:
                _probe_im_apis(page, username)
            logger.debug(f"账号 {username} 聊天页加载完毕（关过弹窗={closed_popup_once}），userIDDict={len(userIDDict)} 条")
            return True
        skel_rounds += 1
        if skel_rounds % 2 == 1:
            _probe_im_apis(page, username)
            try:
                vw = page.evaluate("() => window.innerWidth || 1440")
                vh = page.evaluate("() => window.innerHeight || 900")
                page.mouse.click(vw / 2, vh / 2)
            except Exception:
                pass
        time.sleep(3)
    body_txt = ""
    try:
        body_txt = page.evaluate("() => document.body ? document.body.innerText.slice(0, 1000) : ''") or ""
    except Exception:
        pass
    logger.warning(f"账号 {username} 聊天页等待超时({max_wait}s)，关过弹窗={closed_popup_once}。body前1000字：{body_txt[:1000]}")
    _close_login_save_popup(page, username)
    _probe_im_apis(page, username)
    return False


def _ensure_creator_message_view(page, username, max_wait=35):
    """creator 中心不一定真的进入"私信"视图：必要时点击"消息/私信入口，或者直接跳 /creator-micro/message 路径。"""
    urls_to_try = [
        "https://creator.douyin.com/creator-micro/message",
        "https://creator.douyin.com/creator-micro/message?tab=private",
        "https://creator.douyin.com/creator-micro/data-center/message",
        "https://creator.douyin.com/creator-micro/interaction/manage",
    ]
    need_enter = False
    try:
        cur = page.url or ""
        txt = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
    except Exception:
        txt = ""
    im_kws = ["私信", "全部消息", "会话", "聊天记录", "发送消息", "消息中心", "粉丝消息", "互动消息"]
    if not any(k in txt for k in im_kws) and not any(u in cur for u in ("/message", "/im")):
        need_enter = True
    if not need_enter and "粉丝" in txt and "互动管理" in txt and "数据中心" in txt:
            need_enter = True
    if not need_enter:
        logger.debug(f"creator IM 视图已就位 (url={cur[:90]})")
        return True
    logger.info(f"creator 中心还没进入消息视图 (url={cur[:90]})，尝试点击入口+跳转 URL")

    # 1) 先尝试点击页面上可见的「消息/私信/互动管理/粉丝管理入口
    click_candidates = [
        "xpath=//div[contains(text(),'消息') and (contains(@class,'tab') or contains(@class,'menu') or contains(@class,'item') or contains(@class,'nav') or self::a or self::button)]",
        "xpath=//*[self::a or self::button or self::div or self::span][contains(normalize-space(.),'私信')]",
        "xpath=//*[self::a or self::button][contains(@href,'message') or contains(@href,'im') or contains(@href,'interaction')]",
        '[class*="message"] [role="button"], [class*="Message"] [onclick*="message"], [class*="icon-message"]',
        '[class*="mail"] [role="button"], [class*="icon-chat"] [role="button"], [class*="nav"] [class*="im"]',
    ]
    clicked_any = False
    for cx in click_candidates:
        try:
            n = page.locator(cx).count()
            for i in range(min(3, n)):
                try:
                    loc = page.locator(cx).nth(i)
                    try:
                        vis = loc.is_visible(timeout=600)
                    except Exception:
                        vis = True
                    if not vis:
                        continue
                    txt = ""
                    try:
                        txt = (loc.inner_text(timeout=600) or "")
                    except Exception:
                        pass
                    if any(k in txt for k in ["首页", "管理首页", "数据中心"]) and not any(
                        k in txt for k in ["消息", "私信", "互动", "粉丝"]):
                        continue
                    loc.click(timeout=1500)
                    clicked_any = True
                    time.sleep(2)
                    break
                except Exception:
                    continue
            if clicked_any:
                break
        except Exception:
            continue
    if clicked_any:
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            pass
        time.sleep(2)
        wait_chat_page_ready(page, username, max_wait=25)

    # 2) 如果点击之后还没就位，就逐个 URL 试
    try:
        cur = page.url or ""
        txt_after = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
    except Exception:
        txt_after = ""
    if not any(k in txt_after for k in im_kws):
        for u in urls_to_try:
            try:
                page.goto(u, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                pass
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            wait_chat_page_ready(page, username, max_wait=25)
            try:
                txt = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
            except Exception:
                txt = ""
            if any(k in txt for k in im_kws):
                logger.info(f"creator 跳转 {u} 已就位")
                return True
    return True


def _find_conversation_rows(page):
    """哈希 class 全失效时兜底：用 DOM 结构特征找会话行（左侧有圆形头像+昵称+两行文本的列表项），返回 Locator list。"""
    try:
        found = page.evaluate("""() => {
            const rows = [];
            const all = document.querySelectorAll('div');
            for (let i = 0; i < all.length; i++) {
                const el = all[i];
                const cs = getComputedStyle(el);
                const h = parseFloat(cs.height);
                const w = parseFloat(cs.width);
                const disp = cs.display;
                if (disp === 'none' || h < 48 || h > 140 || w < 100) continue;
                // 特征：display 是 flex/grid 或有 3+ 个子元素
                const kids = Array.from(el.children || []);
                if (kids.length < 2 || kids.length > 12) continue;
                // 子元素里应该有：一个头像（border-radius 接近50%/100% 或圆形img）+ 至少一个文本节点
                let hasAvatar = false;
                let hasText = false;
                let textSample = '';
                for (const k of kids) {
                    const kcs = getComputedStyle(k);
                    const br = parseFloat(kcs.borderRadius);
                    const kh = parseFloat(kcs.height);
                    const kw = parseFloat(kcs.width);
                    if ((br > 0 && Math.abs(br - kh/2) < kh*0.2) || (kh > 20 && kh < 80 && Math.abs(kh-kw) < 10)) {
                        hasAvatar = true;
                    }
                    const t = (k.innerText || '').trim();
                    if (t && t.length < 40) {
                        if (!textSample) textSample = t.split(/\\n/)[0].trim();
                        if (textSample) hasText = true;
                    }
                }
                if (hasAvatar && hasText && textSample && !/^(直播|加载|骨架)$/.test(textSample)) {
                    rows.push({index: i, text: textSample});
                }
            }
            return rows.slice(0, 50);
        }""")
        if not found:
            return []
        locs = []
        for r in found:
            try:
                loc = page.evaluate_handle(
                    "(idx) => document.querySelectorAll('div')[idx]",
                    r["index"],
                )
                if loc:
                    from playwright.sync_api import Locator
                    locs.append((r["text"], loc.as_element()))
            except Exception:
                continue
        return locs
    except Exception as e:
        logger.debug(f"DOM 结构找会话行异常: {e}")
        return []


def _try_search_and_enter(page, username, targets):
    """终极兜底：在聊天页找搜索框，输入目标抖音号/昵称，搜索后进入第一个结果。"""
    search_candidates = [
        'input[placeholder*="搜索"]',
        'input[placeholder*="search" i]',
        'input[type="search"]',
        'div[contenteditable="true"][data-placeholder*="搜索"]',
        'div[contenteditable="true"][placeholder*="搜索"]',
        'textarea[placeholder*="搜索"]',
        '[class*="search"] input',
        '[class*="Search"] input',
        '[class*="sidebar"] input',
        '[class*="aside"] input',
        '[class*="side"] input',
        '[role="search"] input',
    ]
    search_loc = None
    for sc in search_candidates:
        try:
            n = page.locator(sc).count()
            if n > 0:
                search_loc = page.locator(sc).first
                logger.debug(f"账号 {username} 找到搜索框选择器: {sc}")
                break
        except Exception:
            continue
    if search_loc is None:
        # 再兜底：所有 input 里第一个可见的
        try:
            for i in range(min(page.locator("input").count(), 6)):
                cand = page.locator("input").nth(i)
                if cand.is_visible(timeout=800):
                    search_loc = cand
                    break
        except Exception:
            pass
    if search_loc is None:
        logger.warning(f"账号 {username} 没找到搜索框，跳过 search 兜底")
        return False

    matched_any = False
    for t in list(targets):
        try:
            logger.info(f"账号 {username} 尝试用搜索框搜索目标 {t!r}")
            search_loc.click(timeout=2000)
            time.sleep(0.3)
            # 清空
            try:
                search_loc.fill("")
                for _ in range(3):
                    search_loc.press("End")
                    search_loc.press("Backspace")
            except Exception:
                pass
            search_loc.type(str(t), timeout=4000)
            time.sleep(0.5)
            search_loc.press("Enter")
            time.sleep(2.5)
            # 点第一个搜索结果
            clicked = False
            for sel in (
                '[class*="result"] >> nth=0',
                '[class*="Result"] >> nth=0',
                '[class*="search"] [class*="item"] >> nth=0',
                'li >> nth=0',
            ):
                try:
                    cand = page.locator(sel)
                    if cand.count() > 0 and cand.first.is_visible(timeout=1000):
                        cand.first.click(timeout=2000)
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                # 直接按 Enter 选中第一个
                page.keyboard.press("Enter")
                time.sleep(0.5)
                page.keyboard.press("Enter")
            time.sleep(2)
            # 判定是否成功进入：看是否有 contenteditable 输入框
            has_editor = False
            for fsel in ('[contenteditable="true"]', '[class*="chat-input"]', 'textarea'):
                try:
                    if page.locator(fsel).count() > 0:
                        has_editor = True
                        break
                except Exception:
                    continue
            if has_editor:
                logger.info(f"账号 {username} 搜索 {t!r} 后疑似进入会话（输入框已出现）")
                matched_any = True
                yield t
        except Exception as e:
            logger.warning(f"搜索 {t!r} 异常: {e}")
    return matched_any


def scroll_and_select_user(page, username, targets):
    """三重策略：① class 前缀匹配(dev 老方案) ② DOM 结构找会话行 ③ 搜索框兜底。"""
    logger.debug(f"账号 {username} 开始查找目标好友列表（creator IM），targets={targets}")

    found_targets = set()
    remaining_targets = set(targets)
    empty_scroll_count = 0
    MAX_EMPTY_SCROLLS = 10

    for _round in range(25):
        target_elements_info = []  # list of (text, handle_or_locator)
        # Strategy 1: dev 分支的旧 class 选择器
        try:
            n = page.locator(CONVERSATION_ITEM_SELECTOR).count()
            if n > 0:
                for i in range(n):
                    try:
                        loc = page.locator(CONVERSATION_ITEM_SELECTOR).nth(i)
                        try:
                            t = loc.locator(CONVERSATION_TITLE_SELECTOR).first.inner_text(timeout=800)
                        except Exception:
                            t = loc.inner_text(timeout=800).splitlines()[0]
                        t = norm(t)
                        if t:
                            target_elements_info.append((t, loc))
                    except Exception:
                        continue
        except Exception:
            pass
        # Strategy 2: DOM 结构特征兜底
        if not target_elements_info:
            rows = _find_conversation_rows(page)
            for txt, el in rows:
                if el is not None:
                    target_elements_info.append((norm(txt), el))
        # Strategy 2b: 直接拿 body 里所有非空行文本做一层伪匹配（如果 DOM 里还是没有元素但 userIDDict 有）
        if not target_elements_info and len(userIDDict) > 0:
            try:
                all_txt = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
                for line in all_txt.splitlines():
                    ll = norm(line)
                    if not ll or len(ll) < 2 or ll in found_targets:
                        continue
                    if len(ll) > 30:
                        continue
                    target_elements_info.append((ll, None))
            except Exception:
                pass

        if not target_elements_info:
            empty_scroll_count += 1
            logger.debug(f"第 {_round} 轮无会话候选，empty_scroll={empty_scroll_count}/{MAX_EMPTY_SCROLLS}（剩余={remaining_targets}）")
            # 先兜底：如果已经达到空滚动阈值 OR userIDDict 有目标但就是没 DOM，直接走搜索框
            if (empty_scroll_count >= MAX_EMPTY_SCROLLS and remaining_targets) or (
                len(userIDDict) > 0 and empty_scroll_count >= 3 and remaining_targets
            ):
                logger.warning(f"会话列表策略无法继续（empty={empty_scroll_count}, userIDDict={len(userIDDict)}），进入搜索框兜底")
                try:
                    search_gen = _try_search_and_enter(page, username, remaining_targets)
                    any_from_search = False
                    for t in search_gen:
                        any_from_search = True
                        yield t
                        try:
                            remaining_targets.discard(t)
                        except Exception:
                            pass
                        if len(remaining_targets) == 0:
                            return
                    if any_from_search or empty_scroll_count >= MAX_EMPTY_SCROLLS + 3:
                        break
                except Exception as e:
                    logger.warning(f"搜索兜底异常: {e}")
                    if empty_scroll_count >= MAX_EMPTY_SCROLLS + 3:
                        break
            try:
                page.evaluate("window.scrollBy(0, 500)")
            except Exception:
                pass
            time.sleep(1.8)
            if empty_scroll_count >= MAX_EMPTY_SCROLLS + 5:
                break
            continue

        prev_found_count = len(found_targets)
        matched_in_round = False

        for targetName, element in target_elements_info:
            try:
                targetName = norm(targetName)
                if not targetName or targetName in found_targets:
                    continue
                found_targets.add(targetName)
                logger.debug(f"账号 {username} 找到候选会话 {targetName!r}")

                targetSymbol = checkTargetName(targetName, targets)
                if targetSymbol:
                    if element is not None:
                        try:
                            if hasattr(element, "scroll_into_view_if_needed"):
                                element.scroll_into_view_if_needed(timeout=1500)
                            if hasattr(element, "click"):
                                element.click(timeout=2500)
                            else:
                                # JS element
                                page.evaluate("(e) => e && e.click && e.click()", element)
                        except Exception as e:
                            logger.warning(f"点击会话项失败（DOM 句柄），重试 bounding_box: {e}")
                            try:
                                box = element.bounding_box() if hasattr(element, "bounding_box") else None
                                if box:
                                    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                                else:
                                    page.mouse.click(600, 400)
                            except Exception:
                                continue
                    else:
                        # element=None：纯文本命中 userIDDict，尝试用搜索框兜底下
                        pass
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
            logger.warning(f"账号 {username} 连续 {MAX_EMPTY_SCROLLS} 轮滚动无新会话/未匹配，尝试搜索框兜底")
            # 搜索框兜底
            if remaining_targets:
                search_gen = _try_search_and_enter(page, username, remaining_targets)
                try:
                    for t in search_gen:
                        matched_in_round = True
                        yield t
                        try:
                            remaining_targets.discard(t)
                        except Exception:
                            pass
                        if len(remaining_targets) == 0:
                            return
                except Exception as e:
                    logger.warning(f"搜索框兜底生成器异常: {e}")
            break

        # 滚动
        try:
            scroll_el = page.locator(CONVERSATION_LIST_SELECTOR).first
            scrolled = False
            if scroll_el.count() > 0:
                eh = scroll_el.element_handle()
                if eh:
                    st_before = page.evaluate("e => e.scrollTop", eh)
                    page.evaluate("e => e.scrollTop += 600", eh)
                    time.sleep(0.3)
                    st_after = page.evaluate("e => e.scrollTop", eh)
                    if st_before != st_after:
                        scrolled = True
                        logger.debug(f"会话列表滚动 {st_before}->{st_after}")
            if not scrolled:
                page.evaluate("window.scrollBy(0, 600)")
        except Exception:
            try:
                page.evaluate("window.scrollBy(0, 600)")
            except Exception:
                pass
        time.sleep(1.2)

    if remaining_targets:
        logger.warning(f"账号 {username} 搜索结束，仍未匹配: {remaining_targets}（userIDDict={len(userIDDict)} 条）")
        if len(userIDDict) < 5:
            logger.debug(f"userIDDict: {json.dumps(userIDDict, ensure_ascii=False)[:800]}")


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
            "导航到抖音首页（种 Cookie + 验证登录态）",
            page.goto,
            retries=config["taskRetryTimes"],
            delay=5,
            url="https://www.douyin.com/",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        try:
            body_home = page.evaluate("() => document.body ? document.body.innerText.slice(0,1000) : ''") or ""
        except Exception:
            body_home = ""
        bad_kw = [k for k in ["扫码登录", "请先登录", "立即登录", "登录/注册", "手机号登录"] if k in body_home]
        good_kw = [k for k in ["推荐", "热榜", "关注", "搜索", "首页", "消息"] if k in body_home]
        logger.debug(f"账号 {username} 首页登录特征：bad={bad_kw} good={good_kw}")
        if bad_kw and not good_kw:
            logger.warning(f"首页疑似未登录（{bad_kw}），仍尝试进入 creator 中心 IM")

        retry_operation(
            "导航到抖音 creator 中心消息页面（直接 message 专门页）",
            page.goto,
            retries=config["taskRetryTimes"],
            delay=5,
            url="https://creator.douyin.com/creator-micro/message",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        try:
            page.reload(wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            logger.warning(f"creator/message 页 reload 超时，忽略继续: {e}")
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        wait_chat_page_ready(page, username, max_wait=60)
        _ensure_creator_message_view(page, username, max_wait=40)

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
            "聊天", "消息", "会话", "发送", "朋友", "粉丝", "私信",
            "conversation", "搜索", "聊天记录", "互动管理", "数据中心",
            "全部消息", "好友",
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

        if len(userIDDict) == 0:
            logger.info(f"userIDDict 为空，进入好友匹配前再强制探测 9 个接口")
            _probe_im_apis(page, username)
            _probe_im_apis(page, username)

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
