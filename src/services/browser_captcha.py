"""
浏览器自动化获取 reCAPTCHA token
使用 Playwright 访问页面并执行 reCAPTCHA 验证
"""
import asyncio
import time
import re
import os
import random
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


class BrowserCaptchaService:
    """浏览器自动化获取 reCAPTCHA token（单例模式）"""

    _instance: Optional['BrowserCaptchaService'] = None
    _lock = asyncio.Lock()
    _token_semaphore: asyncio.Semaphore = None  # 并发限制信号量

    def __init__(self, db=None):
        """初始化服务"""
        self.headless = False  # 有头模式
        self.playwright = None
        self.context = None  # 使用 persistent context
        self._initialized = False
        self.website_key = "6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV"
        self.db = db
        # 使用固定目录保存浏览器状态
        self.user_data_dir = os.path.join(os.getcwd(), "browser_data")
        # 并发限制：同时最多2个打码请求
        self.max_concurrent = 2
        # 请求间隔：避免过快请求
        self._last_request_time = 0
        self.min_interval = 1.0  # 最小间隔1秒

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
        """初始化浏览器（启动一次）"""
        if self._initialized:
            return

        try:
            # 获取浏览器专用代理配置
            proxy_url = None
            if self.db:
                captcha_config = await self.db.get_captcha_config()
                if captcha_config.browser_proxy_enabled and captcha_config.browser_proxy_url:
                    proxy_url = captcha_config.browser_proxy_url

            debug_logger.log_info(f"[BrowserCaptcha] 正在启动浏览器... (proxy={proxy_url or 'None'})")
            self.playwright = await async_playwright().start()

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

            self._initialized = True
            debug_logger.log_info(f"[BrowserCaptcha] ✅ 浏览器已启动 (profile={self.user_data_dir})")
        except Exception as e:
            debug_logger.log_error(f"[BrowserCaptcha] ❌ 浏览器启动失败: {str(e)}")
            raise

    async def get_token(self, project_id: str) -> Optional[str]:
        """获取 reCAPTCHA token

        Args:
            project_id: Flow项目ID

        Returns:
            reCAPTCHA token字符串，如果获取失败返回None
        """
        # 检查浏览器是否还活着，如果关闭了则重新初始化
        if not self._initialized or not self.context:
            await self.initialize()

        # 检查 context 是否还有效（用户可能手动关闭了浏览器）
        try:
            # 尝试获取页面列表来检测 context 是否有效
            _ = self.context.pages
        except Exception:
            debug_logger.log_warning("[BrowserCaptcha] 浏览器已关闭，正在重新启动...")
            self._initialized = False
            self.context = None
            await self.initialize()

        # 并发限制：获取信号量
        async with self._token_semaphore:
            # 限流：确保请求间隔
            now = time.time()
            elapsed = now - self._last_request_time
            if elapsed < self.min_interval:
                wait_time = self.min_interval - elapsed + random.uniform(0.1, 0.5)
                debug_logger.log_info(f"[BrowserCaptcha] 限流等待 {wait_time:.2f}s...")
                await asyncio.sleep(wait_time)
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

                # 注入额外的反检测脚本（包含 Canvas/WebGL 指纹伪装）
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
                                // 添加微小噪点
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
                            const gl = thisArg;
                            // 修改渲染器和供应商信息
                            if (param === 37445) { // UNMASKED_VENDOR_WEBGL
                                return 'Google Inc. (Intel)';
                            }
                            if (param === 37446) { // UNMASKED_RENDERER_WEBGL
                                return 'ANGLE (Intel, Intel(R) UHD Graphics 630, OpenGL 4.1)';
                            }
                            return Reflect.apply(target, thisArg, args);
                        }
                    };

                    // 劫持 WebGL getParameter
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

                website_url = f"https://labs.google/fx/tools/flow/project/{project_id}"

                debug_logger.log_info(f"[BrowserCaptcha] 访问页面: {website_url}")

                # 访问页面
                try:
                    await page.goto(website_url, wait_until="domcontentloaded", timeout=30000)
                except Exception as e:
                    debug_logger.log_warning(f"[BrowserCaptcha] 页面加载超时或失败: {str(e)}")

                # 检查并注入 reCAPTCHA v3 脚本
                debug_logger.log_info("[BrowserCaptcha] 检查并加载 reCAPTCHA v3 脚本...")
                script_loaded = await page.evaluate("""
                    () => {
                        if (window.grecaptcha && typeof window.grecaptcha.execute === 'function') {
                            return true;
                        }
                        return false;
                    }
                """)

                if not script_loaded:
                    # 注入脚本
                    debug_logger.log_info("[BrowserCaptcha] 注入 reCAPTCHA v3 脚本...")
                    await page.evaluate(f"""
                        () => {{
                            return new Promise((resolve) => {{
                                const script = document.createElement('script');
                                script.src = 'https://www.google.com/recaptcha/api.js?render={self.website_key}';
                                script.async = true;
                                script.defer = true;
                                script.onload = () => resolve(true);
                                script.onerror = () => resolve(false);
                                document.head.appendChild(script);
                            }});
                        }}
                    """)

                # 等待reCAPTCHA加载和初始化
                debug_logger.log_info("[BrowserCaptcha] 等待reCAPTCHA初始化...")
                for i in range(20):
                    grecaptcha_ready = await page.evaluate("""
                        () => {
                            return window.grecaptcha &&
                                   typeof window.grecaptcha.execute === 'function';
                        }
                    """)
                    if grecaptcha_ready:
                        debug_logger.log_info(f"[BrowserCaptcha] reCAPTCHA 已准备好（等待了 {i*0.5} 秒）")
                        break
                    await asyncio.sleep(0.5)
                else:
                    debug_logger.log_warning("[BrowserCaptcha] reCAPTCHA 初始化超时，继续尝试执行...")

                # 额外等待确保完全初始化
                await page.wait_for_timeout(1000)

                # 执行reCAPTCHA并获取token
                debug_logger.log_info("[BrowserCaptcha] 执行reCAPTCHA验证...")
                token = await page.evaluate("""
                    async (websiteKey) => {
                        try {
                            if (!window.grecaptcha) {
                                console.error('[BrowserCaptcha] window.grecaptcha 不存在');
                                return null;
                            }

                            if (typeof window.grecaptcha.execute !== 'function') {
                                console.error('[BrowserCaptcha] window.grecaptcha.execute 不是函数');
                                return null;
                            }

                            // 确保grecaptcha已准备好
                            await new Promise((resolve, reject) => {
                                const timeout = setTimeout(() => {
                                    reject(new Error('reCAPTCHA加载超时'));
                                }, 15000);

                                if (window.grecaptcha && window.grecaptcha.ready) {
                                    window.grecaptcha.ready(() => {
                                        clearTimeout(timeout);
                                        resolve();
                                    });
                                } else {
                                    clearTimeout(timeout);
                                    resolve();
                                }
                            });

                            // 执行reCAPTCHA v3
                            const token = await window.grecaptcha.execute(websiteKey, {
                                action: 'FLOW_GENERATION'
                            });

                            return token;
                        } catch (error) {
                            console.error('[BrowserCaptcha] reCAPTCHA执行错误:', error);
                            return null;
                        }
                    }
                """, self.website_key)

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
                # 关闭标签页（释放资源，支持并发）
                if page:
                    try:
                        await page.close()
                    except:
                        pass

    async def close(self):
        """关闭浏览器"""
        try:
            if self.context:
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
