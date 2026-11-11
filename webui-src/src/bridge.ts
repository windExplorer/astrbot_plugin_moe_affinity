// AstrBot 插件 Page 的官方 bridge 访问层。
//
// ⚠️ 插件页被塞进 <iframe sandbox="allow-scripts allow-forms allow-downloads">，
//    没有 allow-same-origin —— 访问 localStorage / cookie 会抛 SecurityError 导致白屏。
//    所以：持久化一律走 storageGet/storageSet（内存兜底），主题读 bridge 下发的 context。

export interface BridgeContext {
  pluginName?: string;
  displayName?: string;
  pageName?: string;
  pageTitle?: string;
  locale?: string;
  isDark?: boolean;
}

export interface AstrBotPageBridge {
  ready?(): Promise<BridgeContext>;
  getContext?(): BridgeContext | null;
  onContext?(handler: (ctx: BridgeContext) => void): () => void;
  apiGet(endpoint: string, params?: Record<string, any>): Promise<any>;
  apiPost(endpoint: string, body?: Record<string, any>): Promise<any>;
}

export function getBridge(): AstrBotPageBridge | null {
  const w = window as any;
  if (w.AstrBotPluginPage) return w.AstrBotPluginPage as AstrBotPageBridge;
  try {
    if (w.parent && w.parent !== w && w.parent.AstrBotPluginPage) {
      return w.parent.AstrBotPluginPage as AstrBotPageBridge;
    }
  } catch {
    return null;
  }
  return null;
}

function isUsable(b: AstrBotPageBridge | null | undefined): b is AstrBotPageBridge {
  return Boolean(b && typeof b.apiGet === "function" && typeof b.apiPost === "function");
}

export function getContext(): BridgeContext | null {
  try {
    return getBridge()?.getContext?.() || null;
  } catch {
    return null;
  }
}

let _ready: Promise<AstrBotPageBridge> | null = null;

/** 等桥接真正握手完成（父页面回过 context）再发请求，否则首个请求会被静默丢弃。 */
export function whenBridgeReady(timeoutMs = 3000): Promise<AstrBotPageBridge> {
  if (_ready) return _ready;
  _ready = (async () => {
    const start = Date.now();
    while (true) {
      const b = getBridge();
      if (isUsable(b) && getContext()) return b;
      if (Date.now() - start > timeoutMs) {
        if (isUsable(b)) return b;
        throw new Error("未检测到 AstrBot 插件 Page 桥接，请从 AstrBot 后台的插件页打开本页面");
      }
      await new Promise((r) => setTimeout(r, 100));
    }
  })().catch((e) => {
    _ready = null;
    throw e;
  });
  return _ready;
}

// ---------------------------------------------------------------- 存储
const memory = new Map<string, string>();
let lsUsable: boolean | null = null;

function localStorageUsable(): boolean {
  if (lsUsable === null) {
    try {
      window.localStorage.getItem("__probe__");
      lsUsable = true;
    } catch {
      lsUsable = false;
    }
  }
  return lsUsable;
}

export function storageGet(key: string): string | null {
  if (localStorageUsable()) {
    try {
      const v = window.localStorage.getItem(key);
      if (v !== null) return v;
    } catch {
      /* 落到内存 */
    }
  }
  return memory.has(key) ? (memory.get(key) as string) : null;
}

export function storageSet(key: string, value: string): void {
  memory.set(key, value);
  if (!localStorageUsable()) return;
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* 忽略 */
  }
}
