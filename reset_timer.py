#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import time
import subprocess
import requests
from seleniumbase import SB

LOGIN_URL = "https://justrunmy.app/id/Account/Login"
PANEL_URL = "https://justrunmy.app/panel"
DOMAIN    = "justrunmy.app"

# ============================================================
#  环境变量与全局变量
# ============================================================
EMAIL        = os.environ.get("ACC")
PASSWORD     = os.environ.get("ACC_PWD")
TG_BOT_TOKEN = os.environ.get("TG_TOKEN")
TG_CHAT_ID   = os.environ.get("TG_ID")
COOKIE_DATA  = os.environ.get("COOKIE")
ACC_INDEX    = os.environ.get("ACC_INDEX", "1")
GH_PAT       = os.environ.get("GH_PAT")
GITHUB_REPO  = os.environ.get("GITHUB_REPOSITORY")

if not EMAIL or not PASSWORD:
    print("致命错误：未找到 ACC 或 ACC_PWD 环境变量！")
    print("请检查 GitHub Repository Secrets 是否配置正确（EML_1, PWD_1...）。")
    sys.exit(1)

DYNAMIC_APP_NAME = os.environ.get("APP_NAME", "").strip() or "未知应用"

def configured_app_url() -> str:
    """优先使用 APP_URL / APP_ID，未配置则稍后从控制面板自动点进应用。"""
    app_url = (os.environ.get("APP_URL") or "").strip()
    app_id = (os.environ.get("APP_ID") or "").strip().strip("/")
    if app_url:
        return app_url
    if app_id:
        return f"https://justrunmy.app/panel/application/{app_id}/"
    return ""

# ============================================================
#  Telegram 推送模块 (前缀加 JRM)
# ============================================================
def send_tg_message(status_icon, status_text, time_left):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("未配置 TG_TOKEN 或 TG_ID，跳过 Telegram 推送。")
        return

    local_time = time.gmtime(time.time() + 8 * 3600)
    current_time_str = time.strftime("%Y-%m-%d %H:%M:%S", local_time)

    text = (
        f"【JRM】{DYNAMIC_APP_NAME}\n"
        f"{status_icon} {status_text}\n"
        f"剩余: {time_left}\n"
        f"时间: {current_time_str}"
    )

    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": text}
    
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            print("  Telegram 通知发送成功！")
        else:
            print(f"  Telegram 通知发送失败: {r.text}")
    except Exception as e:
        print(f"  Telegram 通知发送异常: {e}")

# ============================================================
#  GitHub Secrets 自动同步覆盖模块
# ============================================================
def update_github_secret(new_cookie_json: str):
    if not GH_PAT or not GITHUB_REPO:
        print("ℹ️ 未配置 GH_PAT，跳过自动更新 Secret。")
        return

    secret_name = f"COOKIE_{ACC_INDEX}"
    print(f"🔄 正在同步更新 GitHub Secret: {secret_name}...")
    try:
        env_vars = dict(os.environ, GH_TOKEN=GH_PAT)
        cmd = ["gh", "secret", "set", secret_name, "--repo", GITHUB_REPO, "--body", new_cookie_json]
        res = subprocess.run(cmd, env=env_vars, capture_output=True, text=True, timeout=15)
        if res.returncode == 0:
            print(f"✅ 成功将最新 Cookie 同步并覆盖到 Secrets: {secret_name}")
        else:
            print(f"⚠️ 更新 Secret 失败: {res.stderr.strip()}")
    except Exception as e:
        print(f"⚠️ 执行 gh secret set 出现异常: {e}")

def dump_and_sync_cookies(sb):
    try:
        cookies = None
        try:
            cookies = sb.get_cookies()
        except Exception:
            cookies = sb.driver.get_cookies()

        if not cookies:
            return
            
        valid_keys = [".AspNetCore.Identity.Application", "idsrv.session", "_jrnm_clct", ".AspNetCore.Antiforgery.NCGjD_ZE8wU"]
        filtered_cookies = [c for c in cookies if c.get("name") in valid_keys or "AspNetCore" in c.get("name", "")]
        cookie_payload = filtered_cookies if filtered_cookies else cookies
        
        cookie_json = json.dumps(cookie_payload)
        update_github_secret(cookie_json)
    except Exception as e:
        print(f"提取 Cookie 异常: {e}")

# ============================================================
#  页面注入脚本 (Turnstile 辅助)
# ============================================================
_EXPAND_JS = """
(function() {
    var ts = document.querySelector('input[name="cf-turnstile-response"]');
    if (!ts) return 'no-turnstile';
    var el = ts;
    for (var i = 0; i < 20; i++) {
        el = el.parentElement;
        if (!el) break;
        var s = window.getComputedStyle(el);
        if (s.overflow === 'hidden' || s.overflowX === 'hidden' || s.overflowY === 'hidden')
            el.style.overflow = 'visible';
        el.style.minWidth = 'max-content';
    }
    document.querySelectorAll('iframe').forEach(function(f){
        if (f.src && f.src.includes('challenges.cloudflare.com')) {
            f.style.width = '300px'; f.style.height = '65px';
            f.style.minWidth = '300px';
            f.style.visibility = 'visible'; f.style.opacity = '1';
        }
    });
    return 'done';
})()
"""

_EXISTS_JS = """
(function(){
    return document.querySelector('input[name="cf-turnstile-response"]') !== null;
})()
"""

_SOLVED_JS = """
(function(){
    var i = document.querySelector('input[name="cf-turnstile-response"]');
    return !!(i && i.value && i.value.length > 20);
})()
"""

_COORDS_JS = """
(function(){
    var iframes = document.querySelectorAll('iframe');
    for (var i = 0; i < iframes.length; i++) {
        var src = iframes[i].src || '';
        if (src.includes('cloudflare') || src.includes('turnstile') || src.includes('challenges')) {
            var r = iframes[i].getBoundingClientRect();
            if (r.width > 0 && r.height > 0)
                return {cx: Math.round(r.x + 30), cy: Math.round(r.y + r.height / 2)};
        }
    }
    var inp = document.querySelector('input[name="cf-turnstile-response"]');
    if (inp) {
        var p = inp.parentElement;
        for (var j = 0; j < 5; j++) {
            if (!p) break;
            var r = p.getBoundingClientRect();
            if (r.width > 100 && r.height > 30)
                return {cx: Math.round(r.x + 30), cy: Math.round(r.y + r.height / 2)};
            p = p.parentElement;
        }
    }
    return null;
})()
"""

_WININFO_JS = """
(function(){
    return {
        sx: window.screenX || 0,
        sy: window.screenY || 0,
        oh: window.outerHeight,
        ih: window.innerHeight
    };
})()
"""

def js_fill_input(sb, selector: str, text: str):
    safe_text = text.replace('\\', '\\\\').replace('"', '\\"')
    sb.execute_script(f"""
    (function(){{
        var el = document.querySelector('{selector}');
        if (!el) return;
        var nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
        if (nativeInputValueSetter) {{
            nativeInputValueSetter.call(el, "{safe_text}");
        }} else {{
            el.value = "{safe_text}";
        }}
        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
    }})()
    """)

def _activate_window():
    for cls in ["chrome", "chromium", "Chromium", "Chrome", "google-chrome"]:
        try:
            r = subprocess.run(["xdotool", "search", "--onlyvisible", "--class", cls], capture_output=True, text=True, timeout=3)
            wids = [w for w in r.stdout.strip().split("\n") if w.strip()]
            if wids:
                subprocess.run(["xdotool", "windowactivate", "--sync", wids[0]], timeout=3, stderr=subprocess.DEVNULL)
                time.sleep(0.2)
                return
        except Exception:
            pass
    try:
        subprocess.run(["xdotool", "getactivewindow", "windowactivate"], timeout=3, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def _xdotool_click(x: int, y: int):
    _activate_window()
    try:
        subprocess.run(["xdotool", "mousemove", "--sync", str(x), str(y)], timeout=3, stderr=subprocess.DEVNULL)
        time.sleep(0.15)
        subprocess.run(["xdotool", "click", "1"], timeout=2, stderr=subprocess.DEVNULL)
    except Exception:
        os.system(f"xdotool mousemove {x} {y} click 1 2>/dev/null")

def _click_turnstile(sb):
    try:
        coords = sb.execute_script(_COORDS_JS)
    except Exception as e:
        print(f"  获取 Turnstile 坐标失败: {e}")
        return
    if not coords:
        print("  无法定位 Turnstile 坐标")
        return
    try:
        wi = sb.execute_script(_WININFO_JS)
    except Exception:
        wi = {"sx": 0, "sy": 0, "oh": 800, "ih": 768}
        
    bar = wi["oh"] - wi["ih"]
    ax  = coords["cx"] + wi["sx"]
    ay  = coords["cy"] + wi["sy"] + bar
    print(f"  物理级点击 Turnstile ({ax}, {ay})")
    _xdotool_click(ax, ay)

def handle_turnstile(sb) -> bool:
    print("处理 Cloudflare Turnstile 验证...")
    time.sleep(3)
    
    if sb.execute_script(_SOLVED_JS):
        print("  已静默通过")
        return True

    for _ in range(3):
        try: sb.execute_script(_EXPAND_JS)
        except Exception: pass
        time.sleep(0.5)

    for attempt in range(6):
        if sb.execute_script(_SOLVED_JS):
            print(f"  Turnstile 通过（第 {attempt + 1} 次尝试）")
            return True
        try: sb.execute_script(_EXPAND_JS)
        except Exception: pass
        time.sleep(0.3)
        
        _click_turnstile(sb)
        
        for _ in range(8):
            time.sleep(0.5)
            if sb.execute_script(_SOLVED_JS):
                print(f"  Turnstile 通过（第 {attempt + 1} 次尝试）")
                return True
        print(f"  第 {attempt + 1} 次未通过，重试...")

    print("  Turnstile 6 次均失败")
    return False

# ============================================================
#  登录控制模块 (优先 Cookie，回退账密)
# ============================================================
def try_cookie_login(sb) -> bool:
    if not COOKIE_DATA:
        print("未检测到 COOKIE Secret，直接使用账号密码登录。")
        return False

    print("🔑 检测到历史 Cookie，尝试通过 Cookie 快速登录...")
    try:
        sb.open("https://justrunmy.app/robots.txt")
        time.sleep(2)

        raw_cookie = COOKIE_DATA.strip()
        if raw_cookie.startswith("["):
            cookies = json.loads(raw_cookie)
            for c in cookies:
                cookie_dict = {
                    'name': c['name'],
                    'value': c['value'],
                    'domain': c.get('domain', '.justrunmy.app'),
                    'path': c.get('path', '/')
                }
                try: sb.driver.add_cookie(cookie_dict)
                except Exception: pass
        else:
            for item in raw_cookie.split(';'):
                if '=' in item:
                    k, v = item.strip().split('=', 1)
                    try:
                        sb.driver.add_cookie({'name': k, 'value': v, 'domain': '.justrunmy.app', 'path': '/'})
                    except Exception: pass

        target = configured_app_url() or PANEL_URL
        print(f"Cookie 注入完成，验证登录态: {target}")
        sb.open(target)
        time.sleep(6)

        curr_url = sb.get_current_url().lower()
        if "/account/login" not in curr_url and not sb.is_element_visible('input[name="Password"]'):
            print("🎉 Cookie 登录成功！已直达应用管理页。")
            return True
        else:
            print("⚠️ Cookie 已失效，降级为账号密码登录。")
            return False
    except Exception as e:
        print(f"⚠️ Cookie 登录流程异常: {e}，将尝试账号密码登录。")
        return False

def form_login(sb) -> bool:
    print(f"打开登录页面: {LOGIN_URL}")
    sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=5)
    time.sleep(4)

    try:
        sb.wait_for_element('input[name="Email"]', timeout=15)
    except Exception:
        print("页面未加载出登录表单")
        sb.save_screenshot("login_load_fail.png")
        return False

    print("关闭可能的 Cookie 弹窗...")
    try:
        for btn in sb.find_elements("button"):
            if "Accept" in (btn.text or ""):
                btn.click()
                time.sleep(0.5)
                break
    except Exception:
        pass

    print(f"填写邮箱...")
    js_fill_input(sb, 'input[name="Email"]', EMAIL)
    time.sleep(0.3)
    
    print("填写密码...")
    js_fill_input(sb, 'input[name="Password"]', PASSWORD)
    time.sleep(1)

    if sb.execute_script(_EXISTS_JS):
        if not handle_turnstile(sb):
            print("登录界面的 Turnstile 验证失败")
            sb.save_screenshot("login_turnstile_fail.png")
            return False
    else:
        print("未检测到 Turnstile")

    print("点击登录按钮提交表单...")
    try:
        if sb.is_element_visible('button[type="submit"]'):
            sb.click('button[type="submit"]')
        else:
            sb.press_keys('input[name="Password"]', '\n')
    except Exception:
        sb.press_keys('input[name="Password"]', '\n')

    print("等待登录验证与跳转...")
    for _ in range(15):
        time.sleep(1)
        curr_url = sb.get_current_url().lower()
        if "/panel" in curr_url:
            print("登录成功，已进入控制面板！")
            return True

    if sb.is_element_visible('input[name="Password"]'):
        print("登录失败：依然停留在登录页，请检查账号密码或验证码。")
        sb.save_screenshot("login_failed.png")
        return False

    return True

# ============================================================
#  续期操作模块 (多策略容错定位)
# ============================================================
def dump_page_buttons(sb):
    """失败时打印页面按钮，方便对照截图排查点错入口的问题。"""
    try:
        print(f"  当前 URL: {sb.get_current_url()}")
        info = sb.execute_script("""
        (function(){
            var buttons = Array.from(document.querySelectorAll('button, a, [role="button"]'));
            return buttons.slice(0, 40).map(function(btn){
                return {
                    tag: btn.tagName,
                    text: ((btn.innerText || btn.textContent || "") + "").replace(/\\s+/g, " ").trim().slice(0, 80),
                    cls: ((btn.className || "") + "").toString().slice(0, 80)
                };
            });
        })()
        """)
        print(f"  页面按钮抽样: {info}")
    except Exception as e:
        print(f"  无法列出页面按钮: {e}")

def is_dialog_open(sb) -> bool:
    """检查是否已经打开了续期确认弹窗。必须使用 IIFE，UC/CDP 下顶层 return 会 SyntaxError。"""
    try:
        return bool(sb.execute_script("""
        (function(){
            var bodyText = document.body.innerText || "";
            if (bodyText.indexOf("Tired of resetting this timer") !== -1 || bodyText.indexOf("Just Reset") !== -1) {
                return true;
            }
            var nodes = document.querySelectorAll('[role="dialog"], div.fixed, .modal');
            for (var i = 0; i < nodes.length; i++) {
                var t = (nodes[i].innerText || "").toLowerCase();
                if (t.indexOf("just reset") !== -1 || t.indexOf("reset this timer") !== -1) {
                    return true;
                }
            }
            return false;
        })()
        """))
    except Exception:
        return False

def grab_app_name(sb):
    global DYNAMIC_APP_NAME
    for sel in ["h3.font-semibold", "h1", "h2"]:
        try:
            if sb.is_element_visible(sel):
                txt = (sb.get_text(sel) or "").strip()
                if txt:
                    DYNAMIC_APP_NAME = txt
                    print(f"当前应用名称: {DYNAMIC_APP_NAME}")
                    return
        except Exception:
            continue

def open_app_page(sb) -> bool:
    """进入应用详情页：优先 APP_URL/APP_ID，否则在 /panel 点击应用卡片。"""
    global DYNAMIC_APP_NAME
    target = configured_app_url()
    if target:
        print(f"进入指定应用详情页: {target}")
        sb.open(target)
        time.sleep(6)
        curr = (sb.get_current_url() or "").lower()
        if "/account/login" in curr:
            print("打开应用页后被踢回登录页，登录态无效")
            return False
        if "/application/" in curr:
            grab_app_name(sb)
            return True
        print("指定地址未直接进入详情页，改为从控制面板点击应用卡片...")

    print(f"进入控制面板: {PANEL_URL}")
    sb.open(PANEL_URL)
    time.sleep(5)

    wanted = (os.environ.get("APP_NAME") or "").strip().lower()
    click_card_js = """
    (function(){
        var wanted = %s;
        var hs = document.querySelectorAll('h3.font-semibold');
        for (var i = 0; i < hs.length; i++) {
            var t = (hs[i].innerText || "").replace(/\\s+/g, " ").trim();
            if (!t) continue;
            if (!wanted || t.toLowerCase().indexOf(wanted) !== -1) {
                hs[i].click();
                return t;
            }
        }
        if (hs.length) {
            var fallback = (hs[0].innerText || "").replace(/\\s+/g, " ").trim();
            hs[0].click();
            return fallback;
        }
        return null;
    })()
    """ % (json.dumps(wanted))

    retry_count = 3
    for attempt in range(1, retry_count + 1):
        try:
            sb.wait_for_element("h3.font-semibold", timeout=15)
            name = sb.execute_script(click_card_js)
            if not name:
                raise Exception("页面上没有应用卡片")
            DYNAMIC_APP_NAME = name
            print(f"成功抓取到应用名称: {DYNAMIC_APP_NAME}")
            time.sleep(4)
            print(f"成功进入应用详情页: {sb.get_current_url()}")
            return True
        except Exception as e:
            if attempt < retry_count:
                print(f"第 {attempt} 次尝试获取应用卡片失败: {e}，刷新页面重试...")
                sb.refresh()
                time.sleep(5)
            else:
                print(f"无法定位应用卡片: {e}")
    return False

def find_and_click_reset_entry(sb) -> bool:
    """定位并点击主界面的 Reset timer 按钮，必须等到续期弹窗真正出现才算成功。"""
    print("正在定位并打开续期弹窗...")

    if is_dialog_open(sb):
        print("✅ 检测到续期弹窗已处于开启状态，直接进入确认步骤。")
        return True

    js_find_and_click = """
    (function() {
        function vis(el) {
            var r = el.getBoundingClientRect();
            return r.width >= 5 && r.height >= 5;
        }
        function norm(s) {
            return ((s || "") + "").toLowerCase().replace(/\\s+/g, " ").trim();
        }
        var buttons = Array.from(document.querySelectorAll('button, a, div[role="button"]'));

        for (var i = 0; i < buttons.length; i++) {
            var btn = buttons[i];
            if (!vis(btn)) continue;
            var text = norm(btn.innerText || btn.textContent);
            var aria = norm(btn.getAttribute("aria-label"));
            var title = norm(btn.getAttribute("title"));
            if (text === "just reset" || text === "add credits") continue;
            if (text.indexOf("reset timer") !== -1 || aria.indexOf("reset timer") !== -1 || title.indexOf("reset timer") !== -1) {
                btn.click();
                return "text_reset_match: " + text;
            }
        }

        for (var i = 0; i < buttons.length; i++) {
            var btn = buttons[i];
            if (!vis(btn)) continue;
            var text = norm(btn.innerText || btn.textContent);
            var aria = norm(btn.getAttribute("aria-label"));
            var title = norm(btn.getAttribute("title"));
            if (text === "just reset" || text === "add credits") continue;
            if (text.indexOf("reset") !== -1 || aria.indexOf("reset") !== -1 || title.indexOf("reset") !== -1) {
                btn.click();
                return "text_reset_match: " + text;
            }
        }

        for (var i = 0; i < buttons.length; i++) {
            var btn = buttons[i];
            if (!vis(btn)) continue;
            if (btn.querySelector(".bi-arrow-clockwise, .bi-arrow-repeat, [class*='arrow-clockwise']")) {
                btn.click();
                return "icon_clockwise_match";
            }
        }
        return null;
    })()
    """

    selectors = [
        'button:contains("Reset Timer")',
        'button:contains("Reset timer")',
        'button:contains("reset timer")',
        'button[aria-label*="Reset" i]',
        'button[title*="Reset" i]',
        '//button[contains(., "Reset Timer") or contains(., "Reset timer")]',
        '//button[.//i[contains(@class, "bi-arrow-clockwise")]]',
        'button:contains("Reset")',
    ]

    start_time = time.time()
    max_wait = 25
    failed_sels = set()
    while time.time() - start_time < max_wait:
        if is_dialog_open(sb):
            print("✅ 弹窗已展开！")
            return True

        for sel in selectors:
            if sel in failed_sels:
                continue
            try:
                if sb.is_element_visible(sel):
                    sb.click(sel)
                    print(f"✅ 成功点击 Reset 按钮 (选择器: {sel})")
                    time.sleep(2)
                    if is_dialog_open(sb):
                        print("✅ 弹窗已展开！")
                        return True
                    print("  点击后未检测到续期弹窗，可能点到了其它按钮，跳过该选择器")
                    failed_sels.add(sel)
            except Exception:
                continue

        try:
            res = sb.execute_script(js_find_and_click)
            if res:
                print(f"✅ 成功触发 Reset 入口按钮 (方式: {res})")
                time.sleep(2)
                if is_dialog_open(sb):
                    print("✅ 弹窗已展开！")
                    return True
                print("  JS 点击后未检测到续期弹窗，继续等待页面加载...")
                if "icon_clockwise_match" in str(res):
                    print("  图标匹配未打开弹窗，避免反复误点，等待文本按钮出现")
                    js_find_and_click = "(function(){ return null; })()"
        except Exception as e:
            print(f"  JS 查找入口异常: {e}")

        time.sleep(1)

    print("等待超时，续期弹窗仍未打开")
    dump_page_buttons(sb)
    return False

def click_just_reset_button(sb) -> bool:
    """定位并点击弹窗内的 Just Reset 按钮。JS 必须包在 IIFE 里，否则 CDP 会报 Illegal return statement。"""
    print("正在定位并点击 Just Reset 确认按钮...")
    js_click = """
    (function(){
        var buttons = Array.from(document.querySelectorAll('button, a, [role="button"]'));
        for (var i = 0; i < buttons.length; i++) {
            var btn = buttons[i];
            var txt = ((btn.innerText || btn.textContent || "") + "").replace(/\\s+/g, " ").trim();
            if (txt === "Just Reset" || txt.toLowerCase().indexOf("just reset") !== -1) {
                btn.click();
                return true;
            }
        }
        return false;
    })()
    """
    selectors = [
        'button:contains("Just Reset")',
        '//button[contains(translate(text(), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "just reset")]',
        'div.fixed button:contains("Just Reset")',
        'div[role="dialog"] button:contains("Just Reset")',
        'button.border-slate-200',
        '//button[contains(., "Just Reset")]',
    ]

    js_error_printed = False
    deadline = time.time() + 12
    while time.time() < deadline:
        try:
            clicked = sb.execute_script(js_click)
            if clicked:
                print("✅ 通过 JS 成功点击 Just Reset 按钮！")
                return True
        except Exception as e:
            if not js_error_printed:
                print(f"JS 尝试点击异常: {e}")
                js_error_printed = True

        for sel in selectors:
            try:
                if sb.is_element_visible(sel):
                    sb.click(sel)
                    print(f"✅ 成功点击确认按钮 (选择器: {sel})")
                    return True
            except Exception:
                continue
        time.sleep(1)

    dump_page_buttons(sb)
    return False

def renew(sb) -> bool:
    global DYNAMIC_APP_NAME
    print("\n" + "=" * 50)
    print("   开始自动续期流程")
    print("=" * 50)

    if not open_app_page(sb):
        print("无法进入应用详情页")
        sb.save_screenshot("renew_app_not_found.png")
        send_tg_message("❌", "续期失败(找不到应用)", "未知")
        return False

    if not find_and_click_reset_entry(sb):
        print("无法定位到 Reset timer 按钮，或点击后弹窗未打开")
        sb.save_screenshot("renew_reset_btn_not_found.png")
        send_tg_message("❌", "续期失败(找不到入口按钮)", "未知")
        return False

    print("检查续期弹窗与人机验证...")
    turnstile_found = False
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            if sb.execute_script(_EXISTS_JS):
                turnstile_found = True
                break
        except Exception:
            pass
        time.sleep(0.5)

    if turnstile_found:
        print("检测到弹窗内包含 Cloudflare Turnstile 验证框，开始验证...")
        if not handle_turnstile(sb):
            print("弹窗内的 Turnstile 验证失败")
            sb.save_screenshot("renew_turnstile_fail.png")
            send_tg_message("❌", "续期失败(弹窗人机验证未通过)", "未知")
            return False
        print("✅ 弹窗内 CF 验证已顺利通过！")
    else:
        print("弹窗内未检测到 Turnstile 验证，直接继续...")
        if not is_dialog_open(sb):
            print("续期弹窗未打开，停止点击确认按钮")
            dump_page_buttons(sb)
            sb.save_screenshot("renew_dialog_missing.png")
            send_tg_message("❌", "续期失败(弹窗未打开)", "未知")
            return False

    time.sleep(1)
    if not click_just_reset_button(sb):
        print("无法点击 Just Reset 确认按钮")
        sb.save_screenshot("renew_just_reset_not_found.png")
        send_tg_message("❌", "续期失败(无法点击确认按钮)", "未知")
        return False

    print("提交续期请求，等待服务器处理...")
    time.sleep(6)

    print("验证续期结果与剩余时间...")
    try:
        sb.refresh()
        time.sleep(5)

        dump_and_sync_cookies(sb)

        timer_text = "已提交重置"
        for sel in ["span.font-mono", "section div"]:
            if sb.is_element_visible(sel):
                txt = sb.get_text(sel)
                if "day" in txt or "hour" in txt or ":" in txt or "until" in txt:
                    timer_text = txt
                    break

        print(f"当前应用剩余时间: {timer_text}")
        sb.save_screenshot("renew_success.png")
        send_tg_message("✅", "续期完成", timer_text)
        return True
    except Exception as e:
        print(f"读取状态异常: {e}")
        sb.save_screenshot("renew_timer_read_fail.png")
        send_tg_message("⚠️", "续期已执行(状态读取异常)", "未知")
        return True

def main():
    print("=" * 50)
    print("   JustRunMy.app 自动登录与续期脚本")
    print("=" * 50)

    proxy_url_env = os.environ.get("PROXY_URL", "").strip()
    sb_kwargs = {"uc": True, "test": True, "headless": False}

    if proxy_url_env:
        local_proxy = "http://127.0.0.1:8080"
        print(f"检测到代理配置，挂载本地通道: {local_proxy}")
        sb_kwargs["proxy"] = local_proxy

    success = False
    with SB(**sb_kwargs) as sb:
        print("浏览器已启动")
        try:
            sb.open("https://api.ipify.org/?format=json")
            print(f"当前出口 IP: {sb.get_text('body')}")
        except Exception:
            pass

        if try_cookie_login(sb) or form_login(sb):
            success = bool(renew(sb))
        else:
            print("\n登录环节失败，终止后续续期操作。")
            send_tg_message("❌", "登录失败", "未知")

    if not success:
        sys.exit(1)

if __name__ == "__main__":
    main()
