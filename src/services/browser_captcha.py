"""
浏览器自动化获取 reCAPTCHA token
使用 Playwright 访问页面并执行 reCAPTCHA 验证
"""
import asyncio
import time
import re
import os
import random
import subprocess
import socket
from typing import Optional, Dict
from playwright.async_api import async_playwright # type: ignore

from ..core.logger import debug_logger


# 尝试导入 stealth 模块
try:
    from playwright_stealth import Stealth  # type: ignore
    STEALTH_AVAILABLE = True
except ImportError:
    STEALTH_AVAILABLE = False
    debug_logger.log_warning("[BrowserCaptcha] playwright-stealth 未安装，伪装功能不可用。建议: pip install playwright-stealth")


def parse_proxy_url(proxy_url: str) -> Optional[Dict[str, str]]:
    """解析代理URL，分离协议、主机、端口、认证信息

    Args:
        proxy_url: 代理URL，格式：protocol://[username:password@]host:port

    Returns:
        代理配置字典，包含server、username、password（如果有认证）
    """
    proxy_pattern = r'^(socks5|http|https)://(?:([^:]+):([^@]+)@)?([^:]+):(\d+)$'
    match = re.match(proxy_pattern, proxy_url)

    if match:
        protocol, username, password, host, port = match.groups()
        proxy_config = {'server': f'{protocol}://{host}:{port}'}

        if username and password:
            proxy_config['username'] = username
            proxy_config['password'] = password

        return proxy_config
    return None


def validate_browser_proxy_url(proxy_url: str) -> tuple[bool, str]:
    """验证浏览器代理URL格式（仅支持HTTP和无认证SOCKS5）

    Args:
        proxy_url: 代理URL

    Returns:
        (是否有效, 错误信息)
    """
    if not proxy_url or not proxy_url.strip():
        return True, ""  # 空URL视为有效（不使用代理）

    proxy_url = proxy_url.strip()
    parsed = parse_proxy_url(proxy_url)

    if not parsed:
        return False, "代理URL格式错误，正确格式：http://host:port 或 socks5://host:port"

    # 检查是否有认证信息
    has_auth = 'username' in parsed

    # 获取协议
    protocol = parsed['server'].split('://')[0]

    # SOCKS5不支持认证
    if protocol == 'socks5' and has_auth:
        return False, "浏览器不支持带认证的SOCKS5代理，请使用HTTP代理或移除SOCKS5认证"

    # HTTP/HTTPS支持认证
    if protocol in ['http', 'https']:
        return True, ""

    # SOCKS5无认证支持
    if protocol == 'socks5' and not has_auth:
        return True, ""

    return False, f"不支持的代理协议：{protocol}"


def check_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """检查端口是否开放

    Args:
        host: 主机地址
        port: 端口号
        timeout: 超时时间（秒）

    Returns:
        端口是否开放
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


async def wait_for_port(host: str, port: int, max_wait: float = 30.0) -> bool:
    """等待端口就绪

    Args:
        host: 主机地址
        port: 端口号
        max_wait: 最大等待时间（秒）

    Returns:
        端口是否就绪
    """
    start_time = time.time()
    while time.time() - start_time < max_wait:
        if check_port_open(host, port):
            return True
        await asyncio.sleep(0.5)
    return False


def start_chrome_debug(port: int, user_data_dir: str, proxy_url: str = None) -> Optional[subprocess.Popen]:
    """启动 Chrome 远程调试模式

    Args:
        port: 调试端口
        user_data_dir: 用户数据目录
        proxy_url: 代理地址 (如: http://host:port 或 socks5://host:port)

    Returns:
        Chrome 进程对象，如果启动失败返回 None
    """
    # macOS Chrome 路径
    chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

    # 检查 Chrome 是否存在
    if not os.path.exists(chrome_path):
        debug_logger.log_error(f"[BrowserCaptcha] Chrome 不存在: {chrome_path}")
        return None

    try:
        # 确保用户数据目录存在
        os.makedirs(user_data_dir, exist_ok=True)

        # 构建启动参数
        chrome_args = [
            chrome_path,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data_dir}",
        ]

        # 添加代理参数
        if proxy_url:
            chrome_args.append(f"--proxy-server={proxy_url}")
            debug_logger.log_info(f"[BrowserCaptcha] Chrome 代理: {proxy_url}")

        # 启动 Chrome
        process = subprocess.Popen(
            chrome_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True  # 脱离父进程，避免随父进程退出
        )

        debug_logger.log_info(f"[BrowserCaptcha] Chrome 进程已启动 (PID: {process.pid}, 端口: {port})")
        return process
    except Exception as e:
        debug_logger.log_error(f"[BrowserCaptcha] 启动 Chrome 失败: {e}")
        return None


class BrowserCaptchaService:
    """浏览器自动化获取 reCAPTCHA token（单例模式）"""

    _instance: Optional['BrowserCaptchaService'] = None
    _lock = asyncio.Lock()
    _token_semaphore: asyncio.Semaphore = None  # 并发限制信号量

    def __init__(self, db=None):
        """初始化服务"""
        self.headless = False  # 有头模式
        self.playwright = None
        self.browser = None  # CDP 模式下使用
        self.context = None  # 使用 persistent context 或 CDP context
        self._initialized = False
        self.website_key = "6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV"
        self.db = db
        # 使用固定目录保存浏览器状态
        self.user_data_dir = os.path.join(os.getcwd(), "browser_data")
        # 并发限制：同时最多1个打码请求（避免同时打码导致失败）
        self.max_concurrent = 1
        # 请求间隔：避免过快请求
        self._last_request_time = 0
        self.min_interval = 2.0  # 最小间隔2秒（防止频率限制）
        self.max_random_delay = 0  # 不需要额外随机延迟
        # CDP 配置（从数据库读取后保存）
        self.cdp_enabled = False
        self.cdp_endpoint = None
        self.cdp_port = None
        self.cdp_user_data_dir = None
        self.chrome_process = None  # Chrome 进程对象

    @classmethod
    async def get_instance(cls, db=None) -> 'BrowserCaptchaService':
        """获取单例实例"""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(db)
                    cls._token_semaphore = asyncio.Semaphore(cls._instance.max_concurrent)
                    await cls._instance.initialize()
        return cls._instance

    async def initialize(self):
        """初始化浏览器（启动一次或连接CDP）"""
        if self._initialized:
            return

        try:
            # 获取浏览器专用配置
            proxy_url = None
            use_cdp = False
            cdp_endpoint = None

            if self.db:
                captcha_config = await self.db.get_captcha_config()
                # 代理配置
                if captcha_config.browser_proxy_enabled and captcha_config.browser_proxy_url:
                    proxy_url = captcha_config.browser_proxy_url
                # CDP 配置
                use_cdp = captcha_config.browser_use_cdp
                cdp_endpoint = captcha_config.browser_cdp_endpoint

                # 保存 CDP 配置（用于后续自动重启）
                if use_cdp and cdp_endpoint:
                    self.cdp_enabled = True
                    self.cdp_endpoint = cdp_endpoint
                    self.cdp_proxy_url = proxy_url  # 保存代理配置
                    # 解析端点获取端口
                    try:
                        import re
                        match = re.search(r':(\d+)/?$', cdp_endpoint)
                        if match:
                            self.cdp_port = int(match.group(1))
                            # 设置用户数据目录
                            self.cdp_user_data_dir = os.path.expanduser("~/chrome-for-captcha")
                    except Exception as e:
                        debug_logger.log_warning(f"[BrowserCaptcha] 解析 CDP 端口失败: {e}")

            self.playwright = await async_playwright().start()

            # CDP 连接模式：连接到已运行的 Chrome
            if use_cdp and cdp_endpoint:
                debug_logger.log_info(f"[BrowserCaptcha] 连接到 CDP 端点: {cdp_endpoint}")

                # 尝试连接，如果失败则自动启动 Chrome
                connected = False
                for attempt in range(2):  # 最多尝试2次
                    try:
                        # 连接到远程调试端口的 Chrome
                        self.browser = await self.playwright.chromium.connect_over_cdp(cdp_endpoint)
                        # 获取默认的 browser context（用户真实的浏览器环境）
                        contexts = self.browser.contexts
                        if contexts:
                            self.context = contexts[0]
                            debug_logger.log_info(f"[BrowserCaptcha] ✅ 已连接到 CDP (已有 {len(contexts)} 个上下文)")
                        else:
                            # 没有现有 context，创建一个新的
                            self.context = await self.browser.new_context()
                            debug_logger.log_info("[BrowserCaptcha] ✅ 已连接到 CDP (创建新上下文)")

                        self._initialized = True
                        connected = True
                        return
                    except Exception as e:
                        debug_logger.log_warning(f"[BrowserCaptcha] CDP 连接失败 (尝试 {attempt+1}/2): {e}")

                        # 第一次失败：尝试自动启动 Chrome
                        if attempt == 0 and self.cdp_port and self.cdp_user_data_dir:
                            # 检查端口是否开放
                            if not check_port_open("localhost", self.cdp_port, timeout=2.0):
                                debug_logger.log_info(f"[BrowserCaptcha] 端口 {self.cdp_port} 未开放，正在启动 Chrome...")

                                # 启动 Chrome（带代理）
                                self.chrome_process = start_chrome_debug(
                                    self.cdp_port,
                                    self.cdp_user_data_dir,
                                    getattr(self, 'cdp_proxy_url', None)
                                )

                                if self.chrome_process:
                                    # 等待端口就绪
                                    debug_logger.log_info(f"[BrowserCaptcha] 等待 Chrome 启动...")
                                    port_ready = await wait_for_port("localhost", self.cdp_port, max_wait=30.0)

                                    if port_ready:
                                        debug_logger.log_info(f"[BrowserCaptcha] Chrome 已就绪，重新连接...")
                                        await asyncio.sleep(2)  # 额外等待2秒确保完全就绪
                                        continue  # 重新尝试连接
                                    else:
                                        debug_logger.log_error("[BrowserCaptcha] Chrome 启动超时")
                                else:
                                    debug_logger.log_error("[BrowserCaptcha] Chrome 启动失败")
                            else:
                                debug_logger.log_warning(f"[BrowserCaptcha] 端口 {self.cdp_port} 已开放，但连接失败")

                        # 第二次还是失败，放弃
                        if attempt == 1:
                            debug_logger.log_error(f"[BrowserCaptcha] CDP 连接失败，降级到普通启动模式")
                            use_cdp = False
                            break

            # 普通模式：Playwright 启动浏览器（已不推荐，CDP 更可靠）
            if not use_cdp:
                debug_logger.log_warning("[BrowserCaptcha] ⚠️ 使用普通模式，reCAPTCHA 通过率可能较低，建议使用 CDP 模式")
                debug_logger.log_info(f"[BrowserCaptcha] 正在启动浏览器... (proxy={proxy_url or 'None'})")

                # 配置浏览器启动参数 - 强化反检测
                launch_options = {
                    'headless': self.headless,
                    'channel': 'chrome',  # 使用系统 Chrome
                    'args': [
                        '--disable-blink-features=AutomationControlled',
                        '--disable-dev-shm-usage',
                        '--disable-infobars',
                        '--disable-background-timer-throttling',
                        '--disable-backgrounding-occluded-windows',
                        '--disable-renderer-backgrounding',
                        '--disable-features=IsolateOrigins,site-per-process',
                        '--disable-web-security',
                        '--disable-features=TranslateUI',
                        '--lang=en-US',
                    ],
                    'ignore_default_args': ['--enable-automation'],  # 移除自动化标志
                }

                # 如果有代理，解析并添加代理配置
                if proxy_url:
                    proxy_config = parse_proxy_url(proxy_url)
                    if proxy_config:
                        launch_options['proxy'] = proxy_config
                        auth_info = "auth=yes" if 'username' in proxy_config else "auth=no"
                        debug_logger.log_info(f"[BrowserCaptcha] 代理配置: {proxy_config['server']} ({auth_info})")
                    else:
                        debug_logger.log_warning(f"[BrowserCaptcha] 代理URL格式错误: {proxy_url}")

                # 使用 persistent context 加载真实 Chrome profile
                self.context = await self.playwright.chromium.launch_persistent_context(
                    self.user_data_dir,
                    **launch_options
                )
                self.browser = None  # persistent context 模式不需要单独的 browser 对象

                self._initialized = True
                debug_logger.log_info(f"[BrowserCaptcha] ✅ 浏览器已启动 (profile={self.user_data_dir})")
        except Exception as e:
            debug_logger.log_error(f"[BrowserCaptcha] ❌ 浏览器启动失败: {str(e)}")
            raise

    async def get_token(self, project_id: str, action: str = "IMAGE_GENERATION") -> Optional[str]:
        """获取 reCAPTCHA token（常驻页面模式）

        Args:
            project_id: Flow项目ID
            action: reCAPTCHA action 类型

        Returns:
            reCAPTCHA token字符串，如果获取失败返回None
        """
        # 检查浏览器是否还活着，如果关闭了则重新初始化
        if not self._initialized or not self.context:
            await self.initialize()

        # 检查 context 是否还有效（用户可能手动关闭了浏览器）
        try:
            _ = self.context.pages
        except Exception:
            debug_logger.log_warning("[BrowserCaptcha] 浏览器已关闭，正在重新启动...")
            self._initialized = False
            self.context = None
            await self.initialize()

        # 并发限制：获取信号量
        async with self._token_semaphore:
            # 限流：确保请求间隔 + 随机延迟（模拟人类行为）
            now = time.time()
            elapsed = now - self._last_request_time
            if elapsed < self.min_interval:
                wait_time = self.min_interval - elapsed
            else:
                wait_time = 0
            # 添加随机延迟（0-3秒）
            random_delay = random.uniform(0, self.max_random_delay)
            total_wait = wait_time + random_delay
            if total_wait > 0:
                debug_logger.log_info(f"[BrowserCaptcha] 限流等待 {total_wait:.2f}s (基础: {wait_time:.2f}s + 随机: {random_delay:.2f}s)...")
                await asyncio.sleep(total_wait)
            self._last_request_time = time.time()

            start_time = time.time()

            page = None
            try:
                # 直接在 persistent context 中创建新标签页（复用登录状态）
                page = await self.context.new_page()

                # 应用 stealth 伪装（如果可用）
                if STEALTH_AVAILABLE:
                    stealth = Stealth()
                    await stealth.apply_stealth_async(page)

                # 注入反检测脚本
                await self._inject_stealth_scripts(page)

                # 导航到目标页面
                website_url = f"https://labs.google/fx/tools/flow/project/{project_id}"
                debug_logger.log_info(f"[BrowserCaptcha] 导航到: {website_url}")

                try:
                    await page.goto(website_url, wait_until="domcontentloaded", timeout=30000)
                except Exception as e:
                    debug_logger.log_warning(f"[BrowserCaptcha] 页面加载超时或失败: {str(e)}")

                # 等待 reCAPTCHA 初始化
                await self._ensure_recaptcha_ready(page)

                # 执行 reCAPTCHA 获取 token
                token = await self._execute_recaptcha(page, action)

                duration_ms = (time.time() - start_time) * 1000

                if token:
                    debug_logger.log_info(f"[BrowserCaptcha] ✅ Token获取成功（耗时 {duration_ms:.0f}ms）")
                    return token
                else:
                    debug_logger.log_error("[BrowserCaptcha] Token获取失败（返回null）")
                    return None

            except Exception as e:
                debug_logger.log_error(f"[BrowserCaptcha] 获取token异常: {str(e)}")
                return None
            finally:
                # 关闭标签页（释放资源）
                if page:
                    try:
                        await page.close()
                    except Exception:
                        pass

    async def _inject_stealth_scripts(self, page):
        """注入反检测脚本"""
        await page.add_init_script("""
            // 隐藏 webdriver 属性
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

            // 修改 plugins 数量
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });

            // 修改 languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });

            // 隐藏自动化相关属性
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;

            // 伪造 chrome 对象
            window.chrome = {
                runtime: {},
                loadTimes: function() {},
                csi: function() {},
                app: {}
            };

            // 修改 permissions
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );

            // ===== Canvas 指纹伪装 =====
            const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
            HTMLCanvasElement.prototype.toDataURL = function(type) {
                if (type === 'image/png' || type === undefined) {
                    const ctx = this.getContext('2d');
                    if (ctx) {
                        const imageData = ctx.getImageData(0, 0, this.width, this.height);
                        for (let i = 0; i < imageData.data.length; i += 4) {
                            imageData.data[i] ^= (Math.random() * 2) | 0;
                        }
                        ctx.putImageData(imageData, 0, 0);
                    }
                }
                return originalToDataURL.apply(this, arguments);
            };

            // ===== WebGL 指纹伪装 =====
            const getParameterProxyHandler = {
                apply: function(target, thisArg, args) {
                    const param = args[0];
                    if (param === 37445) return 'Google Inc. (Intel)';
                    if (param === 37446) return 'ANGLE (Intel, Intel(R) UHD Graphics 630, OpenGL 4.1)';
                    return Reflect.apply(target, thisArg, args);
                }
            };

            const originalGetParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = new Proxy(originalGetParameter, getParameterProxyHandler);

            if (typeof WebGL2RenderingContext !== 'undefined') {
                const originalGetParameter2 = WebGL2RenderingContext.prototype.getParameter;
                WebGL2RenderingContext.prototype.getParameter = new Proxy(originalGetParameter2, getParameterProxyHandler);
            }

            // ===== AudioContext 指纹伪装 =====
            const originalCreateAnalyser = AudioContext.prototype.createAnalyser;
            AudioContext.prototype.createAnalyser = function() {
                const analyser = originalCreateAnalyser.apply(this, arguments);
                const originalGetFloatFrequencyData = analyser.getFloatFrequencyData.bind(analyser);
                analyser.getFloatFrequencyData = function(array) {
                    originalGetFloatFrequencyData(array);
                    for (let i = 0; i < array.length; i++) {
                        array[i] += Math.random() * 0.0001;
                    }
                };
                return analyser;
            };
        """)

    async def _ensure_recaptcha_ready(self, page):
        """确保 reCAPTCHA 已加载并初始化"""
        # 检查并注入 reCAPTCHA 脚本
        script_loaded = await page.evaluate("""
            () => {
                if (window.grecaptcha && window.grecaptcha.enterprise &&
                    typeof window.grecaptcha.enterprise.execute === 'function') {
                    return 'enterprise';
                }
                if (window.grecaptcha && typeof window.grecaptcha.execute === 'function') {
                    return 'v3';
                }
                return null;
            }
        """)

        if not script_loaded:
            debug_logger.log_info("[BrowserCaptcha] 注入 reCAPTCHA Enterprise 脚本...")
            await page.evaluate(f"""
                () => {{
                    return new Promise((resolve) => {{
                        const script = document.createElement('script');
                        script.src = 'https://www.google.com/recaptcha/enterprise.js?render={self.website_key}';
                        script.async = true;
                        script.defer = true;
                        script.onload = () => resolve(true);
                        script.onerror = () => resolve(false);
                        document.head.appendChild(script);
                    }});
                }}
            """)

        # 等待 reCAPTCHA 初始化
        debug_logger.log_info("[BrowserCaptcha] 等待 reCAPTCHA 初始化...")
        for i in range(20):
            check_result = await page.evaluate("""
                () => {
                    if (window.grecaptcha && window.grecaptcha.enterprise &&
                        typeof window.grecaptcha.enterprise.execute === 'function') {
                        return 'enterprise';
                    }
                    if (window.grecaptcha && typeof window.grecaptcha.execute === 'function') {
                        return 'v3';
                    }
                    return null;
                }
            """)
            if check_result:
                debug_logger.log_info(f"[BrowserCaptcha] reCAPTCHA {check_result} 已就绪（等待了 {i*0.5}s）")
                break
            await asyncio.sleep(0.5)

        # 额外等待确保完全初始化
        await page.wait_for_timeout(1000)

    async def _execute_recaptcha(self, page, action: str) -> Optional[str]:
        """执行 reCAPTCHA 获取 token"""
        debug_logger.log_info(f"[BrowserCaptcha] 执行 reCAPTCHA（action: {action}）...")

        token = await page.evaluate("""
            async (args) => {
                try {
                    const { websiteKey, action } = args;

                    if (!window.grecaptcha) {
                        console.error('[BrowserCaptcha] window.grecaptcha 不存在');
                        return null;
                    }

                    // 企业版
                    if (window.grecaptcha.enterprise && typeof window.grecaptcha.enterprise.execute === 'function') {
                        await new Promise((resolve, reject) => {
                            const timeout = setTimeout(() => reject(new Error('timeout')), 15000);
                            if (window.grecaptcha.enterprise.ready) {
                                window.grecaptcha.enterprise.ready(() => {
                                    clearTimeout(timeout);
                                    resolve();
                                });
                            } else {
                                clearTimeout(timeout);
                                resolve();
                            }
                        });

                        const token = await window.grecaptcha.enterprise.execute(websiteKey, { action: action });
                        return token;
                    }

                    // 普通 v3
                    if (typeof window.grecaptcha.execute === 'function') {
                        await new Promise((resolve, reject) => {
                            const timeout = setTimeout(() => reject(new Error('timeout')), 15000);
                            if (window.grecaptcha.ready) {
                                window.grecaptcha.ready(() => {
                                    clearTimeout(timeout);
                                    resolve();
                                });
                            } else {
                                clearTimeout(timeout);
                                resolve();
                            }
                        });

                        const token = await window.grecaptcha.execute(websiteKey, { action: action });
                        return token;
                    }

                    return null;
                } catch (e) {
                    console.error('[BrowserCaptcha] execute error:', e);
                    return null;
                }
            }
        """, {"websiteKey": self.website_key, "action": action})

        return token

    async def close(self):
        """关闭浏览器或断开CDP连接"""
        try:
            # CDP 模式：断开连接
            if self.browser:
                try:
                    await self.browser.close()
                    debug_logger.log_info("[BrowserCaptcha] CDP 连接已断开")
                except Exception as e:
                    if "Connection closed" not in str(e):
                        debug_logger.log_warning(f"[BrowserCaptcha] 断开CDP连接时出现异常: {str(e)}")
                finally:
                    self.browser = None
                    self.context = None

                # 如果是我们启动的 Chrome 进程，终止它
                if self.chrome_process:
                    try:
                        self.chrome_process.terminate()
                        debug_logger.log_info(f"[BrowserCaptcha] Chrome 进程已终止 (PID: {self.chrome_process.pid})")
                    except Exception as e:
                        debug_logger.log_warning(f"[BrowserCaptcha] 终止 Chrome 进程失败: {e}")
                    finally:
                        self.chrome_process = None

            # 普通模式：关闭 persistent context
            elif self.context:
                try:
                    await self.context.close()
                except Exception as e:
                    # 忽略连接关闭错误（正常关闭场景）
                    if "Connection closed" not in str(e):
                        debug_logger.log_warning(f"[BrowserCaptcha] 关闭浏览器时出现异常: {str(e)}")
                finally:
                    self.context = None

            if self.playwright:
                try:
                    await self.playwright.stop()
                except Exception:
                    pass  # 静默处理 playwright 停止异常
                finally:
                    self.playwright = None

            self._initialized = False
            debug_logger.log_info("[BrowserCaptcha] 浏览器已关闭")
        except Exception as e:
            debug_logger.log_error(f"[BrowserCaptcha] 关闭浏览器异常: {str(e)}")
