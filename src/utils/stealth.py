"""
Stealth utilities — injects browser scripts to reduce bot-detection signals
in Playwright browser contexts.
"""

import logging
from playwright.async_api import BrowserContext

log = logging.getLogger(__name__)


async def apply_stealth_scripts(context: BrowserContext) -> None:
    """
    Inject stealth patches into every new page opened in this context.
    Covers the most common automation-detection fingerprints.
    """
    await context.add_init_script(_STEALTH_JS)


# JavaScript injected before page scripts run
_STEALTH_JS = """
// 1. Remove webdriver flag
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// 2. Spoof plugins (real Chrome has plugins)
Object.defineProperty(navigator, 'plugins', {
  get: () => {
    const arr = [
      { name: 'Chrome PDF Plugin',      filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'Chrome PDF Viewer',       filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
      { name: 'Native Client',           filename: 'internal-nacl-plugin',  description: '' },
    ];
    arr.__proto__ = PluginArray.prototype;
    return arr;
  },
});

// 3. Spoof languages
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en', 'de'] });

// 4. Spoof platform
Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });

// 5. Override permissions query (headless Chrome fails on this)
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) =>
  parameters.name === 'notifications'
    ? Promise.resolve({ state: Notification.permission })
    : originalQuery(parameters);

// 6. Spoof chrome runtime object (headless doesn't have it)
window.chrome = {
  runtime: {
    PlatformOs: { MAC: 'mac', WIN: 'win', ANDROID: 'android', CROS: 'cros', LINUX: 'linux', OPENBSD: 'openbsd' },
    PlatformArch: { ARM: 'arm', ARM64: 'arm64', X86_32: 'x86-32', X86_64: 'x86-64', MIPS: 'mips', MIPS64: 'mips64' },
    PlatformNaclArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64', MIPS: 'mips', MIPS64: 'mips64' },
    RequestUpdateCheckStatus: { THROTTLED: 'throttled', NO_UPDATE: 'no_update', UPDATE_AVAILABLE: 'update_available' },
    OnInstalledReason: { INSTALL: 'install', UPDATE: 'update', CHROME_UPDATE: 'chrome_update', SHARED_MODULE_UPDATE: 'shared_module_update' },
    OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
  },
};

// 7. Remove automation-related properties from document
delete document.__proto__.webdriver;

// 8. Spoof screen dimensions realistically
Object.defineProperty(screen, 'width',     { get: () => 1366 });
Object.defineProperty(screen, 'height',    { get: () => 768 });
Object.defineProperty(screen, 'availWidth', { get: () => 1366 });
Object.defineProperty(screen, 'availHeight', { get: () => 728 });

// 9. Mask headless in user-agent client hints
Object.defineProperty(navigator, 'userAgentData', {
  get: () => ({
    brands: [
      { brand: 'Chromium', version: '124' },
      { brand: 'Google Chrome', version: '124' },
      { brand: 'Not-A.Brand', version: '99' },
    ],
    mobile: false,
    platform: 'Windows',
    getHighEntropyValues: () => Promise.resolve({}),
  }),
});
"""
