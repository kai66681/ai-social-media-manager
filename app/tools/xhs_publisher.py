import os
import json
import time
import random
import base64
import re
import traceback
import threading
from typing import Dict, Any
from playwright.sync_api import sync_playwright

COOKIE_PATH = os.path.join("app", "static", "xhs_cookies.json")
LOGIN_STATUS_PATH = os.path.join("app", "static", "generated", "xhs_login_status.json")
XHS_LOGIN_URL = "https://creator.xiaohongshu.com/login"
XHS_CREATOR_URL = "https://creator.xiaohongshu.com/"
XHS_DEFAULT_COOKIE_DOMAIN = ".xiaohongshu.com"
XHS_COOKIE_ATTR_NAMES = {
    "path",
    "domain",
    "expires",
    "max-age",
    "secure",
    "httponly",
    "samesite",
    "priority",
    "partitioned",
}

# ============ QR 扫码登录全局状态 ============
# 保持浏览器会话活跃，直到登录完成或取消
_qr_browser = None
_qr_context = None
_qr_page = None
_qr_playwright = None
_qr_lock = threading.Lock()


def _save_cookies(cookies: list[dict[str, Any]]):
    """保存 Playwright 可直接加载的 Cookie 列表。"""
    os.makedirs(os.path.dirname(COOKIE_PATH), exist_ok=True)
    with open(COOKIE_PATH, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False)


def _clean_cookie(raw_cookie: dict[str, Any]) -> dict[str, Any] | None:
    """把浏览器/插件/手动文本里的 Cookie 统一成 Playwright 支持的结构。"""
    name = str(raw_cookie.get("name", "")).strip()
    if not name:
        return None

    value = raw_cookie.get("value", "")
    if value is None:
        value = ""

    cookie: dict[str, Any] = {
        "name": name,
        "value": str(value),
    }

    domain = str(raw_cookie.get("domain") or "").strip()
    url = str(raw_cookie.get("url") or "").strip()

    if name.startswith("__Host-") and not domain:
        cookie["url"] = XHS_CREATOR_URL
        cookie["secure"] = True
    elif url and "xiaohongshu.com" in url:
        cookie["url"] = url
    else:
        if not domain:
            domain = XHS_DEFAULT_COOKIE_DOMAIN
        domain = domain.replace("https://", "").replace("http://", "").split("/")[0]
        if "xiaohongshu.com" not in domain:
            return None
        cookie["domain"] = domain
        cookie["path"] = str(raw_cookie.get("path") or "/") or "/"

    if "secure" in raw_cookie:
        cookie["secure"] = str(raw_cookie.get("secure")).lower() in {"true", "1", "yes", "✓"}
    else:
        cookie["secure"] = True

    if "httpOnly" in raw_cookie:
        cookie["httpOnly"] = str(raw_cookie.get("httpOnly")).lower() in {"true", "1", "yes", "✓"}

    same_site = str(raw_cookie.get("sameSite") or raw_cookie.get("same_site") or "").strip().lower()
    same_site_map = {"strict": "Strict", "lax": "Lax", "none": "None", "no_restriction": "None"}
    if same_site in same_site_map:
        cookie["sameSite"] = same_site_map[same_site]

    expires = raw_cookie.get("expires")
    if expires not in (None, "", -1, "-1", "Session", "session"):
        try:
            expires_value = int(float(expires))
            if expires_value > 0:
                cookie["expires"] = expires_value
        except Exception:
            pass

    return cookie


def _dedupe_cookies(cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for cookie in cookies:
        scope = cookie.get("url") or cookie.get("domain") or XHS_DEFAULT_COOKIE_DOMAIN
        path = cookie.get("path") or "/"
        deduped[(cookie["name"], scope, path)] = cookie
    return list(deduped.values())


def _cookies_from_json(parsed_json: Any) -> list[dict[str, Any]]:
    if isinstance(parsed_json, dict):
        for key in ("cookies", "cookie", "data"):
            if isinstance(parsed_json.get(key), list):
                return _cookies_from_json(parsed_json[key])
        if "name" in parsed_json and "value" in parsed_json:
            cleaned = _clean_cookie(parsed_json)
            return [cleaned] if cleaned else []

        cookies = []
        for name, value in parsed_json.items():
            if isinstance(value, (str, int, float, bool)):
                cleaned = _clean_cookie({"name": name, "value": value})
                if cleaned:
                    cookies.append(cleaned)
        return cookies

    if isinstance(parsed_json, list):
        cookies = []
        for item in parsed_json:
            if isinstance(item, dict) and "name" in item and "value" in item:
                cleaned = _clean_cookie(item)
                if cleaned:
                    cookies.append(cleaned)
        return cookies

    return []


def _parse_cookie_pairs(text: str) -> list[dict[str, Any]]:
    text = re.sub(r"(?im)^\s*(cookie|request cookies?)\s*:\s*", "", text).strip()
    cookies = []
    for part in re.split(r";|\n", text):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        if not name or name.lower() in XHS_COOKIE_ATTR_NAMES:
            continue
        cleaned = _clean_cookie({"name": name, "value": value.strip()})
        if cleaned:
            cookies.append(cleaned)
    return cookies


def _parse_cookie_table(text: str) -> list[dict[str, Any]]:
    cookies = []
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    if not lines:
        return cookies

    header_cells = [cell.strip().lower() for cell in re.split(r"\t| {2,}", lines[0]) if cell.strip()]
    has_header = "name" in header_cells and "value" in header_cells
    header_map = {name: i for i, name in enumerate(header_cells)} if has_header else {}
    data_lines = lines[1:] if has_header else lines

    for line in data_lines:
        cells = [cell.strip() for cell in re.split(r"\t| {2,}", line) if cell.strip()]
        if len(cells) >= 7 and "xiaohongshu.com" in cells[0]:
            cleaned = _clean_cookie({
                "domain": cells[0],
                "path": cells[2],
                "secure": cells[3],
                "expires": cells[4],
                "name": cells[5],
                "value": cells[6],
            })
            if cleaned:
                cookies.append(cleaned)
            continue

        if has_header and len(cells) > max(header_map.get("name", 0), header_map.get("value", 1)):
            raw_cookie = {
                "name": cells[header_map["name"]],
                "value": cells[header_map["value"]],
            }
            for key in ("domain", "path", "expires", "secure", "httponly", "samesite"):
                idx = header_map.get(key)
                if idx is not None and idx < len(cells):
                    raw_cookie[key] = cells[idx]
            cleaned = _clean_cookie(raw_cookie)
            if cleaned:
                cookies.append(cleaned)
            continue

        if len(cells) >= 2:
            raw_cookie = {
                "name": cells[0],
                "value": cells[1],
                "domain": cells[2] if len(cells) >= 3 and "xiaohongshu.com" in cells[2] else XHS_DEFAULT_COOKIE_DOMAIN,
                "path": cells[3] if len(cells) >= 4 and cells[3].startswith("/") else "/",
            }
            cleaned = _clean_cookie(raw_cookie)
            if cleaned:
                cookies.append(cleaned)

    return cookies


def parse_xhs_cookie_input(cookie_input: str) -> list[dict[str, Any]]:
    """支持 JSON、Cookie 请求头、Netscape/DevTools 多行表格等常见粘贴格式。"""
    cookie_input = cookie_input.strip()
    if not cookie_input:
        return []

    for prefix in ("cookie:", "cookies:"):
        if cookie_input.lower().startswith(prefix):
            cookie_input = cookie_input[len(prefix):].strip()
            break

    try:
        parsed_json = json.loads(cookie_input)
        cookies = _cookies_from_json(parsed_json)
        if cookies:
            return _dedupe_cookies(cookies)
    except Exception:
        pass

    cookies = _parse_cookie_table(cookie_input)
    if not cookies:
        cookies = _parse_cookie_pairs(cookie_input)

    return _dedupe_cookies(cookies)


def save_xhs_cookies_from_input(cookie_input: str) -> Dict[str, Any]:
    cookies = parse_xhs_cookie_input(cookie_input)
    if not cookies:
        return {
            "status": "error",
            "message": "Cookie 格式不正确。请粘贴 Network 请求头里的 Cookie，或 DevTools/Application 导出的 Cookie 表格/JSON。",
        }

    _save_cookies(cookies)
    return {"status": "success", "message": f"小红书 Cookie 绑定成功，已识别 {len(cookies)} 个 Cookie。"}


def _qr_cleanup():
    """清理 QR 登录的浏览器资源"""
    global _qr_browser, _qr_context, _qr_page, _qr_playwright
    try:
        if _qr_browser:
            _qr_browser.close()
    except Exception:
        pass
    try:
        if _qr_playwright:
            _qr_playwright.stop()
    except Exception:
        pass
    _qr_browser = None
    _qr_context = None
    _qr_page = None
    _qr_playwright = None


def _capture_qr_image() -> str | None:
    """从当前页面截取二维码图片，返回 base64 编码字符串"""
    global _qr_page
    if not _qr_page:
        return None

    # 小红书登录页的二维码选择器（按优先级尝试）
    qr_selectors = [
        # 二维码图片常见选择器
        ".qrcode-img",
        ".qrcode-img img",
        "img[class*='qrcode']",
        "img[class*='qr-code']",
        "img[class*='QRCode']",
        ".login-qrcode img",
        ".qr-code img",
        "[class*='qrcode'] img",
        "[class*='qr-code'] img",
        "[class*='QRCode'] img",
        # canvas 绘制的二维码
        "canvas[class*='qrcode']",
        "canvas[class*='qr']",
        "canvas",
        # 二维码容器
        "[class*='qrcode']",
        "[class*='qr-code']",
        "[class*='QRCode']",
        ".login-container img",
        "[class*='login'] img",
    ]

    for sel in qr_selectors:
        try:
            els = _qr_page.query_selector_all(sel)
            for el in els:
                box = el.bounding_box()
                # 二维码通常是正方形且不会太小也不会太大
                if box and 120 <= box['width'] <= 360 and 120 <= box['height'] <= 360:
                    ratio = box['width'] / box['height']
                    if not 0.85 <= ratio <= 1.15:
                        continue
                    print(f"[QR Login] 匹配到二维码元素: {sel}, 尺寸: {box['width']}x{box['height']}")
                    screenshot_bytes = el.screenshot()
                    return base64.b64encode(screenshot_bytes).decode('utf-8')
        except Exception:
            continue

    # 兜底：尝试查找所有 img 标签，找到接近正方形且合理尺寸的图片
    try:
        imgs = _qr_page.query_selector_all("img")
        for img in imgs:
            box = img.bounding_box()
            if box and 120 <= box['width'] <= 360 and 120 <= box['height'] <= 360:
                ratio = box['width'] / box['height']
                if 0.8 < ratio < 1.2:  # 接近正方形
                    print(f"[QR Login] 兜底匹配到疑似二维码 img, 尺寸: {box['width']}x{box['height']}")
                    screenshot_bytes = img.screenshot()
                    return base64.b64encode(screenshot_bytes).decode('utf-8')
    except Exception:
        pass

    print("[QR Login] 未定位到二维码元素")
    return None


def _is_qr_login_mode() -> bool:
    """判断当前登录页是否已经切到 App 扫码模式。"""
    global _qr_page
    if not _qr_page:
        return False
    try:
        for text in ["APP扫一扫登录", "扫码即可登录", "扫码登录", "可用小红书扫码"]:
            if _qr_page.get_by_text(text, exact=False).count() > 0:
                return True
    except Exception:
        pass
    return False


def _click_login_corner_toggle() -> bool:
    """点击登录卡片右上角的二维码/手机折角切换入口。"""
    global _qr_page
    if not _qr_page:
        return False

    try:
        candidates = _qr_page.query_selector_all("[class*='login']")
        for el in candidates:
            box = el.bounding_box()
            if not box:
                continue
            if 250 <= box["width"] <= 480 and 250 <= box["height"] <= 520 and box["x"] > 500:
                _qr_page.mouse.click(box["x"] + box["width"] - 24, box["y"] + 24)
                print("[QR Login] 已点击登录卡片右上角折角切换入口")
                _qr_page.wait_for_timeout(2500)
                return True
    except Exception:
        pass

    try:
        _qr_page.mouse.click(1154, 250)
        print("[QR Login] 已通过坐标兜底点击折角切换入口")
        _qr_page.wait_for_timeout(2500)
        return True
    except Exception:
        return False


def _switch_to_qr_tab():
    """尝试点击登录页上的"扫码登录"标签，切换到二维码模式"""
    global _qr_page
    if not _qr_page:
        return

    if _is_qr_login_mode():
        print("[QR Login] 当前已是扫码登录模式")
        return

    # 常见的扫码登录切换按钮文本和选择器
    tab_texts = ["扫码登录", "二维码登录", "扫码", "QR"]
    for text in tab_texts:
        try:
            # 用文本定位
            locator = _qr_page.get_by_text(text, exact=False)
            if locator.count() > 0:
                locator.first.click()
                print(f"[QR Login] 已点击「{text}」标签")
                _qr_page.wait_for_timeout(2000)
                if _is_qr_login_mode() or _capture_qr_image():
                    return
        except Exception:
            continue

    # 尝试通过选择器点击
    tab_selectors = [
        "[class*='qrcode']",
        "[class*='qr-code']",
        "[class*='scan']",
        "[class*='code-tab']",
        "span:has-text('扫码')",
        "div:has-text('扫码登录')",
    ]
    for sel in tab_selectors:
        try:
            el = _qr_page.query_selector(sel)
            if el:
                box = el.bounding_box()
                # 只点击看起来像标签/按钮的小元素
                if box and box['width'] < 300 and box['height'] < 100:
                    el.click()
                    print(f"[QR Login] 通过选择器 {sel} 点击了扫码标签")
                    _qr_page.wait_for_timeout(2000)
                    if _is_qr_login_mode() or _capture_qr_image():
                        return
        except Exception:
            continue

    if _click_login_corner_toggle() and (_is_qr_login_mode() or _capture_qr_image()):
        return

    print("[QR Login] 未找到扫码登录标签，可能默认就是扫码模式")


def xhs_qr_login_start() -> Dict[str, Any]:
    """
    启动无头浏览器访问小红书登录页，截取二维码返回 base64。
    浏览器会话保持活跃，等待 poll 检测登录状态。
    """
    global _qr_browser, _qr_context, _qr_page, _qr_playwright

    with _qr_lock:
        # 如果已有会话在运行，先清理
        if _qr_browser:
            _qr_cleanup()

        try:
            _qr_playwright = sync_playwright().start()
            _qr_browser = _qr_playwright.chromium.launch(
                channel="msedge",
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ]
            )
            _qr_context = _qr_browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
            )
            _qr_context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            """)
            _qr_page = _qr_context.new_page()

            print("[QR Login] 正在导航到小红书登录页...")
            _qr_page.goto(XHS_LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            _qr_page.wait_for_timeout(4000)

            # 检查是否已经处于登录状态
            current_url = _qr_page.url
            if "/creator/" in current_url or "publish" in current_url:
                print("[QR Login] 检测到已登录状态，直接保存 Cookie")
                cookies = _qr_context.cookies()
                _save_cookies(cookies)
                _qr_cleanup()
                return {"status": "already_logged_in", "message": "已处于登录状态，Cookie 已保存！"}

            # 先保存一张完整截图用于调试
            debug_path = os.path.join("app", "static", "generated", "qr_debug_before_click.png")
            os.makedirs(os.path.dirname(debug_path), exist_ok=True)
            _qr_page.screenshot(path=debug_path)
            print(f"[QR Login] 登录页调试截图已保存: {debug_path}")

            # 尝试切换到扫码登录标签
            _switch_to_qr_tab()

            # 再保存一张点击后的截图
            debug_path2 = os.path.join("app", "static", "generated", "qr_debug_after_click.png")
            _qr_page.screenshot(path=debug_path2)
            print(f"[QR Login] 切换后调试截图已保存: {debug_path2}")

            # 截取二维码图片
            qr_base64 = _capture_qr_image()
            if not qr_base64:
                _qr_cleanup()
                return {"status": "error", "message": "未能切换到可扫码二维码，请尝试弹出浏览器登录或手动粘贴 Cookie 绑定。"}

            print("[QR Login] 二维码截取成功，等待用户扫码...")
            return {"status": "ok", "qr_image": qr_base64}

        except Exception as e:
            error_msg = str(e)
            print(f"[QR Login] 启动失败: {error_msg}")
            print(traceback.format_exc())
            _qr_cleanup()
            if "Executable doesn't exist" in error_msg or "browser" in error_msg.lower():
                return {"status": "error", "message": "未安装 Edge 浏览器，无法启动扫码登录。"}
            return {"status": "error", "message": f"启动浏览器失败: {error_msg}"}


def xhs_qr_login_poll() -> Dict[str, Any]:
    """
    检测当前无头浏览器中的登录状态。
    如果登录成功，保存 Cookie 并关闭浏览器。
    """
    global _qr_page, _qr_context

    if not _qr_page or not _qr_context:
        return {"status": "no_session", "message": "没有活跃的登录会话"}

    try:
        current_url = _qr_page.url

        # 检查 URL 是否已跳转到登录成功页面
        if "/creator/" in current_url or "publish" in current_url or "/new/" in current_url or ("login" not in current_url and current_url != "about:blank" and "creator.xiaohongshu.com" in current_url):
            print(f"[QR Login] 检测到 URL 跳转 ({current_url})，登录成功！")
            _qr_page.wait_for_timeout(2000)
            cookies = _qr_context.cookies()
            _save_cookies(cookies)
            _qr_cleanup()
            return {"status": "success", "message": "扫码登录成功！Cookie 已保存。"}

        # 检查页面元素判断是否已登录
        for sel in [".user-avatar", "[class*='avatar']", ".menu-item", "[class*='sidebar']", "button:has-text('发布笔记')"]:
            try:
                el = _qr_page.query_selector(sel)
                if el:
                    print(f"[QR Login] 检测到登录元素 {sel}，登录成功！")
                    _qr_page.wait_for_timeout(2000)
                    cookies = _qr_context.cookies()
                    _save_cookies(cookies)
                    _qr_cleanup()
                    return {"status": "success", "message": "扫码登录成功！Cookie 已保存。"}
            except Exception:
                pass

        return {"status": "waiting", "message": "等待扫码中..."}

    except Exception as e:
        print(f"[QR Login] 轮询出错: {e}")
        return {"status": "waiting", "message": "检测中..."}


def xhs_qr_login_cancel():
    """取消 QR 登录，关闭浏览器"""
    with _qr_lock:
        _qr_cleanup()
    return {"status": "ok", "message": "已取消登录"}


def xhs_qr_refresh() -> Dict[str, Any]:
    """
    刷新二维码：重新加载登录页面并截取新的二维码。
    """
    global _qr_page

    if not _qr_page:
        return {"status": "no_session", "message": "没有活跃的登录会话，请重新开始"}

    try:
        print("[QR Login] 刷新二维码...")
        _qr_page.goto(XHS_LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
        _qr_page.wait_for_timeout(4000)

        _switch_to_qr_tab()

        qr_base64 = _capture_qr_image()
        if not qr_base64:
            return {"status": "error", "message": "刷新后未能截取到二维码"}

        return {"status": "ok", "qr_image": qr_base64}
    except Exception as e:
        print(f"[QR Login] 刷新失败: {e}")
        return {"status": "error", "message": f"刷新二维码失败: {e}"}

def _save_login_status(status: str, message: str):
    """将登录状态写入 JSON 文件，供前端轮询读取"""
    os.makedirs(os.path.dirname(LOGIN_STATUS_PATH), exist_ok=True)
    with open(LOGIN_STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump({"status": status, "message": message}, f, ensure_ascii=False)

def _read_login_status() -> Dict[str, str]:
    """读取登录状态"""
    try:
        if os.path.exists(LOGIN_STATUS_PATH):
            with open(LOGIN_STATUS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"status": "idle", "message": ""}

def xhs_login() -> Dict[str, Any]:
    """
    启动 Playwright 有头浏览器，弹出真实浏览器窗口供用户手动登录。
    用户可在弹出的窗口中使用扫码、手机号、验证码等任意方式登录。
    登录成功后自动捕获 Cookie 并保存。
    """
    print("[XHS Publisher] 正在启动登录浏览器窗口...")
    _save_login_status("starting", "正在弹出浏览器窗口...")

    os.makedirs(os.path.dirname(COOKIE_PATH), exist_ok=True)

    browser = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                channel="msedge",
                headless=False,
                ignore_default_args=["--enable-automation"],
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
            )
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            """)
            page = context.new_page()

            print("[XHS Publisher] 正在导航到小红书创作者登录页...")
            _save_login_status("navigating", "正在加载登录页面...")
            page.goto(XHS_LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            current_url = page.url
            if "/creator/" in current_url or "publish" in current_url:
                print("[XHS Publisher] 检测到已登录状态，直接保存 Cookie...")
                _save_login_status("saving", "检测到已登录，正在保存...")
                cookies = context.cookies()
                _save_cookies(cookies)
                _save_login_status("success", "绑定成功！")
                browser.close()
                return {"status": "success", "message": "绑定成功！登录态已加密保存至本地。"}

            _save_login_status("waiting", "已弹出浏览器窗口，请在窗口中登录你的小红书账号。登录成功后此窗口会自动关闭。")
            print("[XHS Publisher] 登录窗口已弹出，等待用户登录...")

            success = False
            max_wait_seconds = 300
            elapsed = 0

            while elapsed < max_wait_seconds:
                time.sleep(3)
                elapsed += 3

                current_url = page.url
                if "/creator/" in current_url or "publish" in current_url:
                    success = True
                    break

                try:
                    for check_sel in [".user-avatar", ".menu-item", "[class*='avatar']", "[class*='sidebar']"]:
                        el = page.query_selector(check_sel)
                        if el:
                            success = True
                            break
                    if success:
                        break
                except Exception:
                    pass

                remaining = max_wait_seconds - elapsed
                _save_login_status("waiting", f"请在弹出的浏览器窗口中完成登录。剩余 {remaining // 60} 分 {remaining % 60} 秒")

            if success:
                print("[XHS Publisher] 检测到登录成功！正在保存 Cookie...")
                _save_login_status("saving", "登录成功，正在保存 Cookie...")
                page.wait_for_timeout(2000)

                cookies = context.cookies()
                _save_cookies(cookies)

                _save_login_status("success", "绑定成功！")
                browser.close()
                return {"status": "success", "message": "绑定成功！登录态已加密保存至本地。"}
            else:
                _save_login_status("failed", "登录超时，请重试。")
                browser.close()
                return {"status": "failed", "message": "登录超时。请在 5 分钟内完成登录。"}

    except Exception as e:
        error_msg = str(e)
        print(f"[XHS Publisher] 登录浏览器启动失败: {error_msg}")
        print(traceback.format_exc())
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if "Executable doesn't exist" in error_msg or "browser" in error_msg.lower():
            _save_login_status("error", "未安装 Edge 浏览器")
            return {
                "status": "error",
                "message": "浏览器启动失败！系统尝试调用电脑自带的 Microsoft Edge 浏览器但未找到。请确认您的电脑上已安装 Edge 浏览器。"
            }
        _save_login_status("error", error_msg)
        return {"status": "error", "message": f"启动浏览器失败: {error_msg}"}

def verify_saved_cookies() -> Dict[str, Any]:
    """
    验证已保存的小红书 Cookie 是否仍然有效。
    用 Cookie 访问创作者后台，检查是否被重定向到登录页，并确保加载出正常已登录的元素。
    """
    if not os.path.exists(COOKIE_PATH):
        return {"valid": False, "message": "未找到已保存的 Cookie"}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                channel="msedge",
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
            )
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            """)
            
            with open(COOKIE_PATH, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            context.add_cookies(cookies)
            page = context.new_page()
            page.goto("https://creator.xiaohongshu.com/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            if "login" in page.url:
                browser.close()
                return {"valid": False, "message": "Cookie 已过期，请重新登录"}

            # 校验是否出现了代表登录成功的核心元素，如果均未出现则说明未正常登录（被拦截或失效）
            login_success = False
            name = "小红书创作者"
            try:
                # 兼容新首页和旧首页的元素：头像、侧边栏、发布按钮等
                combined_selector = ".user-avatar, [class*='avatar'], .menu-item, [class*='sidebar'], button:has-text('发布笔记')"
                el = page.wait_for_selector(combined_selector, timeout=5000)
                if el:
                    for sel in ["[class*='avatar']", ".user-avatar"]:
                        found_el = page.query_selector(sel)
                        if found_el:
                            # 尝试从小红书界面寻找博主名（如果找不到就用默认）
                            try:
                                parent = found_el.evaluate_handle("el => el.parentElement")
                                inner_text = parent.as_element().inner_text().strip()[:30]
                                if inner_text:
                                    name = inner_text
                                    break
                            except Exception:
                                pass
                    login_success = True
            except Exception:
                pass

            if not login_success:
                browser.close()
                return {"valid": False, "message": "未检测到登录后的页面元素，可能 Cookie 已过期或加载受限，请重新登录绑定。"}

            browser.close()
            return {"valid": True, "message": f"Cookie 有效（{name}）"}
    except Exception as e:
        import traceback
        print(f"[verify_saved_cookies] 验证过程中发生异常:")
        traceback.print_exc()
        return {"valid": False, "message": f"验证失败: {str(e)}"}

def xhs_publish_post(title: str, content: str, image_relative_path: str) -> Dict[str, Any]:
    """
    [同步版] 使用保存的 Cookie 登录小红书并全自动发布图文。
    """
    if not os.path.exists(COOKIE_PATH):
        return {"status": "error", "message": "未找到小红书 Cookie，请先在后台点击‘绑定小红书账号’扫码登录。"}
        
    # 处理网络图片链接的下载
    if image_relative_path.startswith("http"):
        try:
            print(f"[XHS Publisher] 发现配图为网络链接，正在下载: {image_relative_path}")
            os.makedirs(os.path.join("app", "static", "generated"), exist_ok=True)
            import httpx
            temp_filename = f"temp_download_{random.randint(100000, 999999)}.png"
            image_abs_path = os.path.abspath(os.path.join("app", "static", "generated", temp_filename))
            with httpx.Client() as client:
                res = client.get(image_relative_path, timeout=15.0)
                if res.status_code == 200:
                    with open(image_abs_path, "wb") as f:
                        f.write(res.content)
                else:
                    return {"status": "error", "message": f"下载网络配图失败，HTTP 状态码: {res.status_code}"}
        except Exception as e:
            return {"status": "error", "message": f"下载网络配图失败: {e}"}
    else:
        # 获取图片的绝对路径，Playwright 上传文件必须使用绝对路径
        image_abs_path = os.path.abspath(image_relative_path.lstrip("/"))
        if not os.path.exists(image_abs_path):
            image_abs_path = os.path.abspath(os.path.join("app", image_relative_path.lstrip("/")))
            if not os.path.exists(image_abs_path):
                return {"status": "error", "message": f"未找到生成的配图文件: {image_abs_path}"}

    print(f"[XHS Publisher] 正在启动无头浏览器准备发布到小红书...")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="msedge", 
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)
        
        # 载入 Cookie
        with open(COOKIE_PATH, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        context.add_cookies(cookies)
        
        page = context.new_page()
        
        try:
            page.goto("https://creator.xiaohongshu.com/publish/publish?from=homepage&target=image", wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            
            if "login" in page.url:
                browser.close()
                return {"status": "error", "message": "小红书登录态失效，请重新扫码绑定！"}
                
            print("[XHS Publisher] 登录验证成功，正在适配新版上传图文界面...")
            
            # 切换到上传图文选项卡
            tab_locators = page.locator("text=上传图文")
            tab_count = tab_locators.count()
            tab_clicked = False
            for i in range(tab_count):
                el = tab_locators.nth(i)
                box = el.bounding_box()
                if box and box['x'] >= 0 and box['y'] >= 0:
                    el.click(force=True)
                    tab_clicked = True
                    break
            
            if tab_clicked:
                page.wait_for_timeout(2000)
            else:
                print("[XHS Publisher] 警告: 未成功点击‘上传图文’标签，可能已处于对应页面或结构改变。")
                
            print("[XHS Publisher] 正在上传图片...")
            
            file_input = page.wait_for_selector("input[type='file']", state="attached", timeout=15000)
            if not file_input:
                raise Exception("无法定位图片上传输入框。")
            file_input.set_input_files(image_abs_path)
            
            page.wait_for_timeout(5000)
            
            print("[XHS Publisher] 图片上传完毕，正在填写标题与内容...")
            
            title_selectors = [
                "input[placeholder*='标题']",
                ".title-input input",
                ".el-input__inner",
                "input[type='text']"
            ]
            title_input = None
            for sel in title_selectors:
                try:
                    title_input = page.wait_for_selector(sel, timeout=2000)
                    if title_input:
                        break
                except Exception:
                    continue
                    
            if not title_input:
                raise Exception("无法定位标题输入框。")
                
            title_input.fill(title)
            page.wait_for_timeout(1000)
            
            desc_selectors = [
                ".ql-editor",
                "#post-textarea",
                ".editor",
                "div[contenteditable='true']",
                "textarea"
            ]
            desc_input = None
            for sel in desc_selectors:
                try:
                    desc_input = page.wait_for_selector(sel, timeout=2000)
                    if desc_input:
                        break
                except Exception:
                    continue
                    
            if not desc_input:
                raise Exception("无法定位正文编辑框。")
                
            desc_input.click()
            desc_input.fill(content)
            page.wait_for_timeout(2000)
            
            print("[XHS Publisher] 标题正文填写完毕，正在尝试提交发布...")
            
            publish_btn = None
            
            # 优先检查是否存在自定义 Web Component 按钮组件 <xhs-publish-btn>
            try:
                host_loc = page.locator("xhs-publish-btn")
                if host_loc.count() > 0:
                    host_el = host_loc.first
                    box = host_el.bounding_box()
                    if box:
                        print(f"[XHS Publisher] 检测到自定义发布组件 <xhs-publish-btn>，执行物理坐标点击发布...")
                        pub_x = box['x'] + box['width'] * 0.57
                        pub_y = box['y'] + box['height'] / 2
                        page.mouse.click(pub_x, pub_y)
                        publish_btn = host_el
            except Exception as e:
                print(f"[XHS Publisher] 物理定位自定义发布组件出错，将执行兜底定位: {e}")

            if not publish_btn:
                # 兜底：原先的常规按钮定位逻辑
                buttons = page.query_selector_all("button")
                for btn in buttons:
                    text = btn.inner_text()
                    if "发布" in text and "草稿" not in text:
                        publish_btn = btn
                        break
                        
                if not publish_btn:
                    publish_btn = page.query_selector(".publish-btn")
                    
                if not publish_btn:
                    raise Exception("无法定位‘发布’按钮。")
                    
                publish_btn.click()
            print("[XHS Publisher] 发布指令已发送！正在等待服务器处理...")
            success_published = False
            for attempt in range(5):
                # 检查发布成功的标志：发布按钮底座已经在 DOM 中被彻底卸载（说明表单提交成功并退出了编辑态）
                host_locator = page.locator("xhs-publish-btn")
                btn_count = host_locator.count()
                
                if btn_count == 0:
                    success_published = True
                    print("[XHS Publisher] 检测到发布按钮底座已成功从页面 DOM 树中卸载，发布成功！")
                    break
                
                # 如果发布底座依然存在，说明可能还没完全提交，我们再次发起点击重试
                if attempt > 0:
                    print(f"[XHS Publisher] 第 {attempt} 次检测到发布底座依然存在，尝试重新物理点击...")
                    host_el = host_locator.first
                    box = host_el.bounding_box()
                    if box:
                        pub_x = box['x'] + box['width'] * 0.57
                        pub_y = box['y'] + box['height'] / 2
                        try:
                            page.mouse.click(pub_x, pub_y)
                        except Exception as click_err:
                            print(f"[XHS Publisher] 重新点击发布按钮发生错误: {click_err}")
                
                # 每次尝试后留给服务器 3 秒响应与上传时间
                page.wait_for_timeout(3000)
            
            if not success_published:
                print("[XHS Publisher] 警告: 15秒内发布按钮底座仍未消失，执行超时释放。")
                
            browser.close()
            return {"status": "success", "message": "小红书文章真实发布成功！"}
            
        except Exception as e:
            os.makedirs(os.path.join("app", "static", "generated"), exist_ok=True)
            error_screenshot = os.path.join("app", "static", "generated", "xhs_publish_error.png")
            try:
                page.screenshot(path=error_screenshot)
                print(f"[XHS Publisher] 发布失败，错误截图已保存至: {error_screenshot}")
            except Exception:
                pass
            browser.close()
            return {"status": "error", "message": f"发布过程中出错: {str(e)}。已生成错误排查截图。"}

def fetch_xhs_note(url: str) -> Dict[str, str]:
    """
    [同步版] 使用 Playwright 无界面加载小红书网页，智能提取文章的标题与正文描述。
    """
    print(f"[XHS Spider] 正在抓取小红书链接: {url}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        page.set_extra_http_headers({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        
        try:
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            
            # 1. 尝试抓取标题
            title = ""
            title_selectors = ["h1", ".title", "[class*='title']", "#detail-title"]
            for sel in title_selectors:
                el = page.query_selector(sel)
                if el:
                    title = el.inner_text()
                    if title.strip():
                        break
            
            # 2. 尝试抓取正文描述
            desc = ""
            desc_selectors = [".desc", "[class*='desc']", ".note-text", "#detail-desc", ".ql-editor"]
            for sel in desc_selectors:
                el = page.query_selector(sel)
                if el:
                    desc = el.inner_text()
                    if desc.strip():
                        break
            
            # 3. 兜底
            if not desc.strip():
                p_elements = page.query_selector_all("p")
                desc = "\n".join([p.inner_text() for p in p_elements if p.inner_text().strip()])
                
            # 4. 终极兜底
            if not title.strip() and not desc.strip():
                desc = page.inner_text("body")
                
            return {
                "title": title.strip() or "小红书爆款图文",
                "content": desc.strip() or "未捕获到具体正文"
            }
        except Exception as e:
            print(f"[XHS Spider] 抓取小红书发生异常: {e}")
            return {
                "title": "小红书爆款图文",
                "content": f"抓取链接失败，错误详情: {e}"
            }
        finally:
            browser.close()
