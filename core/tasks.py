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


def _close_fullscreen_login_panel(page, username, timeout_each=1500):
    """关闭抖音 www 首页的全屏登录遮罩 (login-full-panel)。

    从第六版 run ea65233 的日志确认：<div id="login-full-panel-xxx"> subtree intercepts pointer events
    导致任何 UI 点击（搜索框/消息按钮）都超时。
    """
    closed = False
    # 1) 先找显式的关闭按钮
    close_selectors = [
        'div[id*="login-full-panel"] [class*="close"] [role="button"]',
        'div[id*="login-full-panel"] [data-e2e*="close"]',
        'div[id*="login-full-panel"] [class*="icon-close"]',
        'div[id*="login-full-panel"] [class*="Close"]',
        'div[id*="login-full-panel"] [aria-label="关闭"]',
        'div[id*="login-panel"] [class*="close"] [role="button"]',
        'svg[class*="login"] [class*="close"]',
        "xpath=//div[contains(@id,'login-full-panel')]//*[name()='svg' or @role='button' or self::span or self::div][contains(normalize-space(.),'关闭') or contains(@class,'close') or contains(@aria-label,'关闭')]",
    ]
    for sx in close_selectors:
        try:
            n = page.locator(sx).count()
            if n == 0:
                continue
            for i in range(min(2, n)):
                try:
                    loc = page.locator(sx).nth(i)
                    try:
                        vis = loc.is_visible(timeout=600)
                    except Exception:
                        vis = True
                    if not vis:
                        continue
                    loc.click(timeout=timeout_each)
                    closed = True
                    time.sleep(0.8)
                except Exception:
                    continue
        except Exception:
            continue
    # 2) 按 ESC
    try:
        page.keyboard.press("Escape")
        time.sleep(0.4)
    except Exception:
        pass
    # 3) 暴力兜底：用 JS 把所有 login 全屏遮罩设为 display:none / pointer-events:none，然后移除 id=login-full-panel-xxx
    try:
        removed = page.evaluate("""() => {
            let count = 0;
            const kill = (el) => {
                if (!el) return;
                try { el.style.display='none'; } catch(_){}
                try { el.style.pointerEvents='none'; } catch(_){}
                try { el.style.visibility='hidden'; } catch(_){}
                count++;
            };
            document.querySelectorAll('div[id*="login-full-panel"],div[id*="login-panel"],div[class*="login-full-panel"],div[class*="LoginFullPanel"],div[class*="loginMask"],div[class*="login-mask"]').forEach(kill);
            document.querySelectorAll('[role="dialog"][aria-label*="登录"]').forEach(kill);
            // 清 z-index 最高的遮罩层
            const all = document.querySelectorAll('*');
            for (let i=all.length-1; i>=Math.max(0,all.length-200); i--) {
                const el = all[i];
                try {
                    const cs = getComputedStyle(el);
                    const z = parseInt(cs.zIndex || '0',10);
                    const pos = cs.position;
                    const h = cs.height;
                    const w = cs.width;
                    const op = parseFloat(cs.opacity || '1');
                    if ( (z>9000 || (pos==='fixed' && ((h==='100%' || h==='100vh' || parseInt(h||'0',10)>=window.innerHeight*0.9) && (w==='100%' || w==='100vw' || parseInt(w||'0',10)>=window.innerWidth*0.9)))) && op>=0.2) {
                        const txt = (el.innerText || '').slice(0,120);
                        if (txt.includes('登录') || txt.includes('扫码') || txt.includes('验证码') || /login-/i.test(el.id || '')) {
                            kill(el);
                        }
                    }
                } catch(_) {}
            }
            // 页面级 overflow 恢复
            try { document.body.style.overflow = 'auto'; } catch(_){}
            try { document.documentElement.style.overflow = 'auto'; } catch(_){}
            return count;
        }""")
        if removed and removed > 0:
            logger.debug(f"账号 {username} 暴力移除 {removed} 个全屏登录遮罩节点")
            closed = True
    except Exception as e:
        logger.debug(f"暴力遮罩清除异常: {e}")
    time.sleep(0.4)
    return closed


def _probe_im_apis(page, username):
    """所有接口强制走 https://www.douyin.com (IM/搜索/关注 API 仅在 www 域名可命中)."""
    global userIDDict
    snap = {}
    try:
        snap = page.evaluate("""async () => {
            const res = {};
            const www = 'https://www.douyin.com';
            const common = 'version_code=1700&device_platform=webapp&aid=6383&webcast_sdk_version=1700&X-Bogus=0';
            const urls = [
                www + '/aweme/v1/web/im/user/info/?cursor=0&user_source=0&count=100&' + common,
                www + '/aweme/v1/web/im/conversation/list/?cursor=0&count=50&' + common,
                www + '/aweme/v1/web/im/user/list/?cursor=0&count=100&' + common,
                www + '/aweme/v1/web/user/following/list/?user_id=&max_time=0&count=100&' + common,
                www + '/aweme/v1/web/user/follower/list/?user_id=&max_time=0&count=100&' + common,
                www + '/aweme/v1/user/following/list/?user_id=&max_time=0&count=100&' + common,
                www + '/aweme/v1/user/follower/list/?user_id=&max_time=0&count=100&' + common,
                www + '/aweme/v1/web/search/sug/?keyword=抖音&from_group_id=&source=general&type=1&' + common,
                www + '/aweme/v1/web/search/user/?keyword=抖音&search_channel=aweme_user_web&search_source=normal&count=10&' + common,
            ];
            for (let i = 0; i < urls.length; i++) {
                try {
                    const r = await fetch(urls[i], {credentials:'include', mode:'cors'}).catch(err => ({err}));
                    if (r && r.err) { res['e'+i] = String(r.err).slice(0,200); continue; }
                    res['s'+i] = r.status;
                    try {
                        const j = await r.json();
                        res['u'+i] = JSON.stringify(j).slice(0,600);
                    } catch(_) {
                        const t = await r.text();
                        res['u'+i] = (t||'').slice(0,400);
                    }
                } catch(e) {
                    res['e'+i] = String(e).slice(0,200);
                }
                await new Promise(r => setTimeout(r, 400));
            }
            return res;
        }""")
        parts = []
        for k in sorted(snap.keys()):
            v = snap[k]
            sv = str(v).replace("\n", " ")
            if k.startswith("u"):
                sv = sv[:400]
            parts.append(f"{k}={sv!r}")
        logger.debug(f"账号 {username} 主动探测(固定www域名) 9 个接口结果：" + " ".join(parts))
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
    # 先清全屏登录遮罩（可能在点消息弹层后再次弹出）
    _close_fullscreen_login_panel(page, username)
    _close_login_save_popup(page, username)
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
            # 失败重试：被 login-full-panel 拦截时，暴力清遮罩后再 dispatchEvent
            for attempt in range(3):
                try:
                    _close_fullscreen_login_panel(page, username)
                    if attempt == 0:
                        search_loc.click(timeout=2000)
                    else:
                        # 暴力遮罩清不掉时，用 focus + dispatchEvent('click')
                        try:
                            page.evaluate("(el) => { if(!el) return; try { el.scrollIntoView({block:'center'}); } catch(_){} try { el.focus(); } catch(_){} try { const ev = new MouseEvent('click', {bubbles:true,cancelable:true}); el.dispatchEvent(ev); } catch(_){} }", search_loc.element_handle())
                        except Exception:
                            pass
                    time.sleep(0.3)
                    break
                except Exception as e:
                    if attempt == 2:
                        raise e
                    logger.debug(f"搜索框点击异常({attempt+1}/3)，清遮罩后重试: {e}")
                    time.sleep(0.6)
            # 清空
            try:
                for attempt in range(3):
                    try:
                        _close_fullscreen_login_panel(page, username)
                        search_loc.fill("")
                        for _ in range(3):
                            search_loc.press("End")
                            search_loc.press("Backspace")
                        break
                    except Exception as e:
                        if attempt == 2:
                            raise e
                        logger.debug(f"搜索框清空异常({attempt+1}/3): {e}")
                        time.sleep(0.5)
            except Exception:
                pass
            # type 输入，失败时用 JS 设置 value + dispatchEvent('input')
            try:
                search_loc.type(str(t), timeout=4000)
            except Exception as e:
                logger.debug(f"搜索框 type 失败，改用 JS: {e}")
                try:
                    _close_fullscreen_login_panel(page, username)
                    page.evaluate("""([el, val]) => {
                        if(!el) return;
                        try { el.scrollIntoView({block:'center'}); } catch(_){}
                        try { el.focus(); } catch(_){}
                        try { el.value = val; } catch(_){ try { el.textContent = val; } catch(_){} }
                        try { el.dispatchEvent(new Event('input', {bubbles:true})); } catch(_){}
                        try { el.dispatchEvent(new Event('change', {bubbles:true})); } catch(_){}
                    }""", [search_loc.element_handle(), str(t)])
                except Exception as ex:
                    raise Exception(f"搜索框输入失败: {e}; JS也失败: {ex}") from e
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


def _search_and_open_profile_pm(page, username, target_short_ids):
    """终极兜底：www.douyin.com 搜索框搜抖音号 → 进用户主页 → 点「私信」进入聊天窗。

    目标列表是 target_short_ids（通常就是 ['1351217349'] 这种抖音号 / unique_id / 昵称）。
    每成功进入聊天窗就 yield 一个识别后的名字（用于日志/后续发送）。
    """
    _close_fullscreen_login_panel(page, username)
    for t in target_short_ids:
        try:
            t = norm(t)
            if not t:
                continue
            _close_fullscreen_login_panel(page, username)
            logger.info(f"[终极兜底] 开始搜索抖音号/昵称 {t!r} → 个人页 → 私信")
            # 1) 先回到首页，确保有搜索框
            try:
                cur = page.url or ""
                if "douyin.com" not in cur or cur.startswith("https://creator"):
                    page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=50000)
                    try: page.wait_for_load_state("networkidle", timeout=12000)
                    except Exception: pass
                    time.sleep(2)
                    _close_fullscreen_login_panel(page, username)
                    _close_login_save_popup(page, username)
            except Exception:
                pass

            # 2) 尝试多种 www 首页搜索框
            search_locs = [
                'input[placeholder*="搜索"]',
                'input[placeholder*="search" i]',
                'input[type="search"]',
                '[class*="search"] input',
                '[class*="header"] input[placeholder*="搜索"]',
                '[class*="top-search"] input',
                '[class*="Search"] input',
            ]
            search_input = None
            for sl in search_locs:
                try:
                    if page.locator(sl).count() > 0:
                        search_input = page.locator(sl).first
                        logger.debug(f"[终极兜底] 找到搜索框 {sl}")
                        break
                except Exception:
                    continue
            if search_input is None:
                # 直接跳搜索结果页
                search_url = f"https://www.douyin.com/search/{urllib.parse.quote(t)}?type=user"
                logger.info(f"[终极兜底] 未找到搜索框，直接跳 URL {search_url[:90]}")
                try:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=50000)
                except Exception as e:
                    logger.warning(f"跳搜索结果页失败: {e}")
                    continue
            else:
                search_input_failed = False
                try:
                    for attempt in range(3):
                        try:
                            _close_fullscreen_login_panel(page, username)
                            if attempt == 0:
                                search_input.click(timeout=2000)
                            else:
                                try:
                                    page.evaluate("(el) => { if(!el) return; try { el.scrollIntoView({block:'center'}); } catch(_){} try { el.focus(); } catch(_){} try { el.dispatchEvent(new MouseEvent('click', {bubbles:true,cancelable:true})); } catch(_){} }", search_input.element_handle())
                                except Exception:
                                    pass
                            time.sleep(0.4)
                            break
                        except Exception as e:
                            if attempt == 2:
                                raise e
                            logger.debug(f"[终极兜底] 搜索框 click 异常({attempt+1}/3): {e}")
                            time.sleep(0.6)
                    # fill + type，失败时走 JS
                    try:
                        for attempt in range(3):
                            try:
                                _close_fullscreen_login_panel(page, username)
                                search_input.fill("")
                                search_input.type(t, delay=40)
                                break
                            except Exception as e:
                                if attempt == 2:
                                    raise e
                                logger.debug(f"[终极兜底] 搜索框 fill 异常({attempt+1}/3): {e}")
                                time.sleep(0.5)
                    except Exception as e:
                        logger.debug(f"[终极兜底] 搜索框 type 失败，改用 JS: {e}")
                        try:
                            _close_fullscreen_login_panel(page, username)
                            page.evaluate("""([el, val]) => {
                                if(!el) return;
                                try { el.scrollIntoView({block:'center'}); } catch(_){}
                                try { el.focus(); } catch(_){}
                                try { el.value = val; } catch(_){ try { el.textContent = val; } catch(_){} }
                                try { el.dispatchEvent(new Event('input', {bubbles:true})); } catch(_){}
                                try { el.dispatchEvent(new Event('change', {bubbles:true})); } catch(_){}
                            }""", [search_input.element_handle(), str(t)])
                        except Exception as ex:
                            logger.warning(f"[终极兜底] 搜索框输入 '...{t[-5:]}' 失败: {e}; JS也失败: {ex}")
                            search_input_failed = True
                    if not search_input_failed:
                        time.sleep(1.2)
                        search_input.press("Enter")
                except Exception as e:
                    logger.warning(f"搜索框输入 {t!r} 失败: {e}")
                    search_url = f"https://www.douyin.com/search/{urllib.parse.quote(t)}?type=user"
                    try:
                        page.goto(search_url, wait_until="domcontentloaded", timeout=50000)
                    except Exception:
                        continue
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            time.sleep(2.5)

            # 3) 切到「用户」tab（如果有）
            try:
                user_tabs = [
                    "xpath=//div[contains(@class,'tab')]//*[text()='用户' or contains(normalize-space(.),'用户')]",
                    "xpath=//a[contains(text(),'用户')]",
                    "xpath=//div[contains(@class,'search-tabs')]//*[text()='用户']",
                ]
                for ut in user_tabs:
                    try:
                        if page.locator(ut).count() > 0:
                            page.locator(ut).first.click(timeout=1500)
                            time.sleep(1.5)
                            break
                    except Exception:
                        continue
            except Exception:
                pass

            # 4) 找用户卡片里第一个含 target 的结果，点进主页
            user_card_selectors = [
                "xpath=//a[contains(@href,'/user/')]",
                "xpath=//li[contains(@class,'user')]//a[contains(@href,'/user/')]",
                "xpath=//div[contains(@class,'user-card')]//a[contains(@href,'/user/')]",
                "xpath=//div[contains(@class,'UserCard')]//a[contains(@href,'/user/')]",
            ]
            matched_href = None
            found_nickname = None
            try:
                for ucs in user_card_selectors:
                    n = page.locator(ucs).count()
                    for i in range(min(6, n)):
                        try:
                            loc = page.locator(ucs).nth(i)
                            href = ""
                            try: href = loc.get_attribute("href", timeout=600) or ""
                            except Exception: pass
                            txt = ""
                            try: txt = norm(loc.inner_text(timeout=800))
                            except Exception: pass
                            # 同一卡片附近再搜一下昵称/抖音号文本
                            nick_txt = txt
                            try:
                                p = loc.locator("xpath=./..").first
                                nick_txt = norm(p.inner_text(timeout=800))
                            except Exception:
                                pass
                            if t and (t in nick_txt or t in txt or (t.isdigit() and t in href)):
                                matched_href = href
                                found_nickname = nick_txt.splitlines()[0] if nick_txt else t
                                logger.info(f"[终极兜底] 命中用户卡片 {found_nickname!r} href={href[:80]}")
                                break
                        except Exception:
                            continue
                    if matched_href: break
            except Exception:
                pass
            if not matched_href:
                try:
                    # 扫一遍 body 里所有 /user/ 链接
                    links = page.evaluate("""() => {
                        const arr = [];
                        const all = document.querySelectorAll('a[href*="/user/"]');
                        for (let i=0;i<all.length && i<12;i++) {
                            const a = all[i];
                            arr.push({href: a.getAttribute('href'), txt: (a.innerText||'').trim().slice(0,60)});
                        }
                        return arr;
                    }""")
                    for l in links:
                        h = l.get("href") or ""
                        tx = norm(l.get("txt",""))
                        if t and (t in tx or (t.isdigit() and t in h)):
                            matched_href = h
                            found_nickname = (tx or t).splitlines()[0]
                            logger.info(f"[终极兜底] 二次匹配命中 {found_nickname!r} href={h[:80]}")
                            break
                except Exception as e:
                    logger.debug(f"兜底扫链接失败: {e}")
            if not matched_href:
                logger.warning(f"[终极兜底] 搜索 {t!r} 没找到用户卡片，跳过")
                continue
            # 拼接完整 URL
            if matched_href.startswith("/"):
                matched_href = "https://www.douyin.com" + matched_href
            # 5) 点进个人主页
            try:
                page.goto(matched_href, wait_until="domcontentloaded", timeout=55000)
                try: page.wait_for_load_state("networkidle", timeout=12000)
                except Exception: pass
                time.sleep(2)
            except Exception as e:
                logger.warning(f"[终极兜底] 跳主页 {matched_href[:80]} 失败: {e}")
                continue
            # 6) 点「私信」按钮（各种文案/图标）
            pm_btns = [
                "xpath=//button[contains(normalize-space(.),'私信')]",
                "xpath=//div[contains(@role,'button')][contains(normalize-space(.),'私信')]",
                "xpath=//a[contains(normalize-space(.),'私信')]",
                "xpath=//button/*[self::span or self::div][contains(.,'私信')]/ancestor::button[1]",
                '[class*="private-message"] [role="button"]',
                '[class*="PrivateMessage"] [onclick]',
                '[class*="pmBtn"]',
            ]
            pm_clicked = False
            for pb in pm_btns:
                try:
                    n = page.locator(pb).count()
                    for i in range(min(3, n)):
                        try:
                            loc = page.locator(pb).nth(i)
                            try: vis = loc.is_visible(timeout=500)
                            except Exception: vis = True
                            if not vis: continue
                            loc.click(timeout=2500)
                            pm_clicked = True
                            time.sleep(2.5)
                            break
                        except Exception:
                            continue
                    if pm_clicked: break
                except Exception:
                    continue
            if not pm_clicked:
                logger.warning(f"[终极兜底] 主页未找到「私信」按钮，跳过 {t!r}")
                continue
            # 7) 等待聊天输入框（弹层或页面）
            try:
                page.wait_for_selector(CHAT_EDITOR_SELECTOR, timeout=18000)
            except Exception as e:
                logger.warning(f"[终极兜底] 私信输入框 {CHAT_EDITOR_SELECTOR} 未出现，尝试降级: {e}")
                for fs in ('[contenteditable="true"]', 'textarea', '[class*="chat-input"]', '[class*="editor"] textarea'):
                    try:
                        if page.locator(fs).count() > 0:
                            logger.info(f"[终极兜底] 使用降级输入框 {fs}")
                            break
                    except Exception:
                        continue
            yield found_nickname or t
        except Exception as e:
            logger.warning(f"[终极兜底] {t!r} 搜索进私信流程异常: {e}\n{traceback.format_exc(limit=2)}")
            continue


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
        # 新的判断：有「登录」按钮（非"退出登录"语境）= 疑似未登录（无论是否有导航按钮）
        has_login_btn = False
        try:
            has_login_btn = bool(page.evaluate("""() => {
                const nodes = Array.from(document.querySelectorAll('a, button, div[role=button], span'));
                for (const n of nodes) {
                    const t = (n.innerText || '').trim();
                    if (!t || t.length > 12) continue;
                    if (t === '登录' || /^\\s*登录\\s*$/.test(t)) {
                        try {
                            const cs = getComputedStyle(n);
                            if (cs.visibility === 'hidden' || cs.display === 'none') continue;
                        } catch(_){}
                        return true;
                    }
                }
                return false;
            }"""))
        except Exception:
            has_login_btn = "登录" in body_home and "退出登录" not in body_home
        logger.debug(f"账号 {username} 首页登录特征：bad={bad_kw} good={good_kw} has_login_btn={has_login_btn}")

        # 先关全屏登录遮罩（EA65233 明确有 login-full-panel 拦截所有点击）
        _close_fullscreen_login_panel(page, username)
        _close_login_save_popup(page, username)

        # 不再跳 creator 中心/message（2026年改版后 creator/message 只是通知中心，没有会话列表）
        # 留在 www.douyin.com（所有 IM API 都在这个域，且有首页搜索框）
        # 先尝试在首页点「消息」展开私信弹层（如果有）
        _close_fullscreen_login_panel(page, username)
        try:
            msg_btns = [
                "xpath=//a[contains(normalize-space(.),'消息') and not(contains(@href,'login'))]",
                "xpath=//div[contains(@class,'message')][contains(@role,'button')]",
                "xpath=//*[contains(@class,'topNav')]//*[contains(normalize-space(.),'消息')]",
                "xpath=//header//*[self::span or self::a or self::button or self::div][normalize-space()='消息']",
            ]
            msg_clicked = False
            for mb in msg_btns:
                try:
                    n = page.locator(mb).count()
                    for i in range(min(3, n)):
                        loc = page.locator(mb).nth(i)
                        try: vis = loc.is_visible(timeout=500)
                        except Exception: vis = True
                        if not vis: continue
                        try:
                            txt = norm(loc.inner_text(timeout=600))
                        except Exception:
                            txt = ""
                        if any(k in txt for k in ["首页", "推荐", "朋友"]):
                            continue
                        loc.click(timeout=1800)
                        msg_clicked = True
                        time.sleep(2)
                        break
                    if msg_clicked: break
                except Exception:
                    continue
            if msg_clicked:
                try: page.wait_for_load_state("networkidle", timeout=8000)
                except Exception: pass
                wait_chat_page_ready(page, username, max_wait=25)
                logger.info("首页「消息」弹层已点击")
        except Exception as e:
            logger.debug(f"点消息弹层异常: {e}")

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
            "全部消息", "好友", "推荐", "关注", "热榜",
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
                f"登录校验失败：命中 {hit_keywords}，未发现聊天/内容特征。"
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
            f"账号 {username} 登录校验成功（good={good_kw} hints={hint_hit}，URL={cur_url[:120]}），开始匹配好友"
        )
        try:
            logger.debug(f"页面文本前1200字：{body_text[:1200]}")
        except Exception:
            pass

        # 先强制探测 2 轮，保证 userIDDict 在 www 域下有机会命中
        probes_snaps = []
        for _ in range(2):
            probes_snaps.append(_probe_im_apis(page, username))

        # 严格登录态校验（EA65233 确认：导航按钮可能有 good_kw 但 followings 接口返回 status_code=8 → 实际未登录）
        # 1. 解析探测接口里是否有 status_code == 8（用户未登录）
        auth_fail_any = False
        auth_fail_codes = []
        for snap in probes_snaps:
            if not isinstance(snap, dict):
                continue
            for u_idx in ["u0", "u1", "u2", "u3", "u4", "u5", "u6", "u7", "u8"]:
                raw = snap.get(u_idx)
                if not isinstance(raw, str) or not raw.startswith("{"):
                    continue
                try:
                    j = json.loads(raw)
                    sc = j.get("status_code")
                    sm = str(j.get("status_msg") or "")
                    if sc == 8 or "用户未登录" in sm:
                        auth_fail_any = True
                        auth_fail_codes.append(f"{u_idx}=sc{sc}({sm[:30]})")
                        break
                except Exception:
                    continue
            if auth_fail_any:
                break
        if has_login_btn and auth_fail_any:
            logger.error(
                f"账号 {username} 登录态校验失败：has_login_btn=True + 接口 status_code=8(用户未登录)={auth_fail_codes}。"
                f" 说明 Cookie 未通过浏览器设备风控（Playwright 无头环境被识别为新设备 / 触发全屏登录遮罩 login-full-panel）。"
                f" 请 1) 使用更新鲜的 Cookie（从浏览器正常登录后立刻复制）；2) 或改用本地手动扫码登录后把 cookie 覆盖到 GitHub Actions Secret。"
                f" 当前 sessionid={sessionid_short!r}"
            )
            try:
                body_now = page.evaluate("() => document.body ? document.body.innerText.slice(0, 1200) : ''") or ""
                logger.debug(f"当前 body 前1200字: {body_now}")
                page.screenshot(path=f"logs/{username}_auth_failed_sc8.png", full_page=True)
                with open(f"logs/{username}_auth_failed_sc8.html", "w", encoding="utf-8") as f:
                    f.write(page.content())
            except Exception:
                pass
            raise RuntimeError(
                f"Cookie 在无头环境下被风控（status_code=8 用户未登录），请重新抓取并在 Secret 替换最新 Cookie。"
                f" 本次探测: {auth_fail_codes}"
            )

        any_matched = False
        already_sent = set()

        # 优先：会话列表策略（如果消息弹层/聊天页能看到会话）
        for friend_name in scroll_and_select_user(page, username, targets):
            if friend_name in already_sent:
                continue
            any_matched = True
            already_sent.add(friend_name)
            logger.info(f"账号 {username} 选中好友/会话 {friend_name!r}（会话策略），准备发送消息")
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

        # 终极兜底：会话列表找不到 → 走「搜索抖音号→个人页→私信→发送」
        if not any_matched or len(already_sent) < len(targets):
            remaining_for_fallback = [t for t in targets if not any(
                norm(t) == norm(a) or norm(t) in norm(a) or norm(a) in norm(t)
                for a in already_sent)]
            if remaining_for_fallback:
                logger.warning(f"[终极兜底] 进入搜索→个人页→私信流程（targets={remaining_for_fallback}）")
                for friend_name in _search_and_open_profile_pm(page, username, remaining_for_fallback):
                    if friend_name in already_sent:
                        continue
                    any_matched = True
                    already_sent.add(friend_name)
                    logger.info(f"账号 {username} 选中好友/会话 {friend_name!r}（兜底策略），准备发送消息")
                    try:
                        page.wait_for_selector(CHAT_EDITOR_SELECTOR, timeout=20000)
                    except Exception as e:
                        logger.warning(f"兜底：聊天输入框 {CHAT_EDITOR_SELECTOR} 未出现，尝试降级: {e}")
                        for fs in ('[class*="chat-input"]', '[contenteditable="true"]', 'div[class*="input"]', 'textarea'):
                            try:
                                if page.locator(fs).count() > 0:
                                    logger.debug(f"兜底用输入框选择器: {fs}")
                                    break
                            except Exception:
                                continue
                    chat_input = page.locator(CHAT_EDITOR_SELECTOR).first
                    if chat_input.count() == 0:
                        for fs in ('[class*="chat-input"]', '[contenteditable="true"]', 'div[class*="input"]', 'textarea'):
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
                            logger.warning(f"兜底输入行失败，尝试 click 聚焦后重输: {e}")
                            try:
                                chat_input.click(timeout=2000)
                                time.sleep(0.3)
                                chat_input.type(line, timeout=5000)
                            except Exception as e2:
                                logger.error(f"兜底仍无法输入，跳过该好友: {e2}")
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
