"""
Stealth and Anti-Detection Engine for Browser Automation.
Provides enterprise-grade browser fingerprint masking, realistic human interaction emulation
(Bezier curve mouse movement, human typing cadence, natural scrolling, reading dwell times),
and active restriction/challenge detection for LinkedIn and other job portals.
"""
import sys
import time
import math
import random
import re
from pathlib import Path

# Chrome executable paths across platforms
CHROME_PATHS = [
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),  # macOS
    Path("/usr/bin/google-chrome"),                                       # Linux
    Path("/usr/bin/google-chrome-stable"),                                # Linux stable
    Path("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"),   # Windows 64-bit
    Path("C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe"), # Windows 32-bit
]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

LINKEDIN_RESTRICTION_PHRASES = [
    "checkpoint",
    "authwall",
    "challenge",
    "security verification",
    "verify it's you",
    "unusual activity",
    "we've restricted your account",
    "temporarily restricted",
    "account restricted",
    "captcha",
    "you've reached the weekly invitation limit",
    "reached the weekly invitation limit",
    "reached the limit for invitations",
    "weekly invitation limit",
    "invitation limit reached",
    "you're out of free inmails",
    "out of free inmails",
    "easy apply limit",
    "application limit",
    "maximum number of easy apply",
    "reached the limit for easy apply",
    "reached the easy apply limit",
    "try again tomorrow",
    "come back tomorrow",
]


def get_chrome_channel() -> str | None:
    """Returns 'chrome' if official Google Chrome app is installed on the user's system."""
    for p in CHROME_PATHS:
        if p.exists():
            return "chrome"
    return None


def get_launch_kwargs(headless: bool = False, slow_mo: int = 40) -> dict:
    """Builds robust launch arguments for Chromium with anti-detection flags."""
    kwargs = {
        "headless": headless,
        "slow_mo": slow_mo,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-background-networking",
            "--disable-default-apps",
            "--disable-sync",
            "--no-first-run",
            "--window-size=1440,900",
        ],
    }
    chan = get_chrome_channel()
    if chan:
        kwargs["channel"] = chan
    return kwargs


def get_context_options(storage_state: str | Path | None = None, user_agent: str | None = None) -> dict:
    """Returns realistic browser context options mirroring genuine macOS Chrome."""
    options = {
        "user_agent": user_agent or DEFAULT_USER_AGENT,
        "viewport": {"width": 1440, "height": 900},
        "locale": "en-US",
        "timezone_id": "Asia/Kolkata",
        "device_scale_factor": 2,
        "has_touch": False,
        "is_mobile": False,
        "permissions": ["geolocation", "notifications"],
    }
    if storage_state:
        st_path = Path(storage_state)
        if st_path.exists():
            options["storage_state"] = str(st_path)
    return options


def apply_stealth(context_or_page):
    """
    Applies deep JavaScript stealth overrides to mask automation indicators,
    re-establish valid navigator plugins, emulate window.chrome runtime, and spoof WebGL.
    """
    stealth_script = """
    (() => {
        // 1. Remove navigator.webdriver & clean prototype
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
            configurable: true
        });
        try {
            delete Object.getPrototypeOf(navigator).webdriver;
        } catch (e) {}

        // 2. Mock authentic window.chrome object
        window.chrome = {
            app: {
                isInstalled: false,
                InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
                RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' }
            },
            runtime: {
                OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' },
                OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic', PROFILE_LOG_OUT: 'profile_log_out' },
                PlatformArch: { ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
                PlatformNaclArch: { ARM: 'arm', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
                PlatformOs: { ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' },
                RequestUpdateCheckStatus: { NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available' }
            },
            csi: () => {},
            loadTimes: () => ({
                commitLoadTime: Date.now() / 1000,
                connectionInfo: 'http/1.1',
                finishDocumentLoadTime: Date.now() / 1000 + 0.1,
                finishLoadTime: Date.now() / 1000 + 0.2,
                firstPaintAfterLoadTime: 0,
                firstPaintTime: Date.now() / 1000 + 0.05,
                navigationType: 'Other',
                npnNegotiatedProtocol: 'h2',
                requestTime: Date.now() / 1000 - 0.5,
                startLoadTime: Date.now() / 1000 - 0.4,
                wasAlternateProtocolAvailable: false,
                wasFetchedViaSpdy: true,
                wasNpnNegotiated: true
            })
        };

        // 3. Realistic navigator plugins array
        const mockPlugins = [
            {
                name: 'PDF Viewer',
                filename: 'internal-pdf-viewer',
                description: 'Portable Document Format',
                mimeTypes: [{ type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' }]
            },
            {
                name: 'Chrome PDF Viewer',
                filename: 'internal-pdf-viewer',
                description: 'Portable Document Format',
                mimeTypes: [{ type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' }]
            },
            {
                name: 'Chromium PDF Viewer',
                filename: 'internal-pdf-viewer',
                description: 'Portable Document Format',
                mimeTypes: [{ type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' }]
            },
            {
                name: 'Microsoft Edge PDF Viewer',
                filename: 'internal-pdf-viewer',
                description: 'Portable Document Format',
                mimeTypes: [{ type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' }]
            },
            {
                name: 'WebKit built-in PDF',
                filename: 'internal-pdf-viewer',
                description: 'Portable Document Format',
                mimeTypes: [{ type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' }]
            }
        ];

        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const arr = Object.create(PluginArray.prototype);
                mockPlugins.forEach((p, idx) => {
                    const plugin = Object.create(Plugin.prototype);
                    Object.defineProperties(plugin, {
                        name: { value: p.name },
                        filename: { value: p.filename },
                        description: { value: p.description },
                        length: { value: p.mimeTypes.length }
                    });
                    arr[idx] = plugin;
                    arr[p.name] = plugin;
                });
                Object.defineProperty(arr, 'length', { value: mockPlugins.length });
                return arr;
            }
        });

        // 4. Languages & Hardware Concurrency
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en']
        });
        Object.defineProperty(navigator, 'hardwareConcurrency', {
            get: () => 8
        });
        Object.defineProperty(navigator, 'deviceMemory', {
            get: () => 8
        });

        // 5. Notifications & Permissions spoofing
        const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
        if (originalQuery) {
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission === 'denied' ? 'prompt' : Notification.permission, onchange: null }) :
                    originalQuery(parameters)
            );
        }

        // 6. WebGL Vendor Spoofing (Apple GPU)
        try {
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                // UNMASKED_VENDOR_WEBGL
                if (parameter === 37445) {
                    return 'Apple Inc.';
                }
                // UNMASKED_RENDERER_WEBGL
                if (parameter === 37446) {
                    return 'Apple M1';
                }
                return getParameter.apply(this, arguments);
            };
        } catch (e) {}
    })();
    """
    if hasattr(context_or_page, "add_init_script"):
        context_or_page.add_init_script(stealth_script)
    elif hasattr(context_or_page, "evaluate"):
        try:
            context_or_page.evaluate(stealth_script)
        except Exception:
            pass


def check_linkedin_restrictions(page) -> tuple[bool, str]:
    """
    Scans the current page URL and DOM for restriction signals, checkpoints,
    weekly invitation limits, or anti-bot challenge blocks.
    Returns: (is_restricted, reason_phrase)
    """
    if not page:
        return False, ""
    try:
        current_url = page.url.lower()
        if any(chk in current_url for chk in ["/checkpoint/", "/uas/login", "/authwall", "security-check"]):
            return True, f"LinkedIn security checkpoint detected ({page.url})"

        body_text = page.evaluate("() => document.body ? document.body.innerText.toLowerCase() : ''")
        for phrase in LINKEDIN_RESTRICTION_PHRASES:
            if phrase in body_text:
                return True, f"LinkedIn limit/restriction signal: '{phrase}'"
    except Exception:
        pass
    return False, ""


def _bezier_curve(p0: tuple[float, float], p1: tuple[float, float], p2: tuple[float, float], p3: tuple[float, float], t: float) -> tuple[float, float]:
    """Calculates a point on a cubic Bezier curve."""
    x = (1 - t)**3 * p0[0] + 3 * (1 - t)**2 * t * p1[0] + 3 * (1 - t) * t**2 * p2[0] + t**3 * p3[0]
    y = (1 - t)**3 * p0[1] + 3 * (1 - t)**2 * t * p1[1] + 3 * (1 - t) * t**2 * p2[1] + t**3 * p3[1]
    return x, y


def human_mouse_move(page, target_x: float, target_y: float, steps: int = 12):
    """
    Moves the mouse along a natural curved trajectory with subtle micro-jitter.
    """
    try:
        # If current mouse position is unknown, start near viewport center
        start_x = random.uniform(300, 700)
        start_y = random.uniform(200, 500)

        # Control points for Bezier curve with slight randomized arcs
        ctrl1_x = start_x + (target_x - start_x) * random.uniform(0.2, 0.4) + random.uniform(-40, 40)
        ctrl1_y = start_y + (target_y - start_y) * random.uniform(0.1, 0.3) + random.uniform(-40, 40)
        ctrl2_x = start_x + (target_x - start_x) * random.uniform(0.6, 0.8) + random.uniform(-20, 20)
        ctrl2_y = start_y + (target_y - start_y) * random.uniform(0.7, 0.9) + random.uniform(-20, 20)

        for step in range(1, steps + 1):
            t = step / steps
            x, y = _bezier_curve((start_x, start_y), (ctrl1_x, ctrl1_y), (ctrl2_x, ctrl2_y), (target_x, target_y), t)
            # Add slight jitter
            jitter_x = x + random.uniform(-1.5, 1.5)
            jitter_y = y + random.uniform(-1.5, 1.5)
            page.mouse.move(jitter_x, jitter_y)
            time.sleep(random.uniform(0.008, 0.022))

        # Final settle at target
        page.mouse.move(target_x, target_y)
        time.sleep(random.uniform(0.05, 0.12))
    except Exception:
        pass


def human_move_and_click(page, locator_or_selector, delay_after: float = 1.0) -> bool:
    """
    Locates an element, scrolls into view, moves the mouse along a natural curve,
    hovers briefly, and clicks with human timing.
    """
    try:
        locator = locator_or_selector if hasattr(locator_or_selector, "bounding_box") else page.locator(locator_or_selector).first
        if locator.count() == 0:
            return False

        locator.scroll_into_view_if_needed()
        time.sleep(random.uniform(0.15, 0.35))

        box = locator.bounding_box()
        if box:
            # Target near the center of the element with slight offset
            target_x = box["x"] + box["width"] * random.uniform(0.35, 0.65)
            target_y = box["y"] + box["height"] * random.uniform(0.35, 0.65)

            human_mouse_move(page, target_x, target_y, steps=random.randint(8, 15))
            time.sleep(random.uniform(0.1, 0.25))

            page.mouse.down()
            time.sleep(random.uniform(0.06, 0.14))
            page.mouse.up()
            time.sleep(delay_after + random.uniform(0.2, 0.5))
            return True
        else:
            locator.click()
            time.sleep(delay_after + random.uniform(0.2, 0.5))
            return True
    except Exception:
        try:
            locator.click(force=True)
            time.sleep(delay_after)
            return True
        except Exception:
            return False


def human_type(page, locator_or_selector, text: str, min_delay_ms: int = 35, max_delay_ms: int = 95):
    """
    Types text character by character into a field with variable human cadence
    and natural pauses at spaces and punctuation.
    """
    try:
        locator = locator_or_selector if hasattr(locator_or_selector, "focus") else page.locator(locator_or_selector).first
        locator.click()
        time.sleep(random.uniform(0.1, 0.25))

        # Clear existing text if any
        locator.fill("")
        time.sleep(0.1)

        for char in text:
            page.keyboard.press(char)
            # Randomized keystroke interval
            delay = random.uniform(min_delay_ms, max_delay_ms) / 1000.0
            # Extra pause on word boundaries or punctuation
            if char in " ,.!?-\n":
                delay += random.uniform(0.10, 0.25)
            time.sleep(delay)

        time.sleep(random.uniform(0.2, 0.4))
    except Exception:
        # Fallback to direct fill if typing fails
        try:
            locator.fill(text)
        except Exception:
            pass


def human_scroll(page, distance: int = 400, steps: int = 5):
    """
    Emulates realistic mouse wheel scrolling with variable incremental steps.
    """
    try:
        step_dist = distance / steps
        for _ in range(steps):
            delta_y = step_dist * random.uniform(0.8, 1.2)
            page.mouse.wheel(0, delta_y)
            time.sleep(random.uniform(0.15, 0.35))
        time.sleep(random.uniform(0.3, 0.6))
    except Exception:
        pass


def human_dwell(min_sec: float = 2.0, max_sec: float = 4.5):
    """Emulates human reading / dwell time."""
    delay = random.uniform(min_sec, max_sec)
    time.sleep(delay)
