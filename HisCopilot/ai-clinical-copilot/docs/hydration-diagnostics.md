# Hydration 诊断报告

> 日期：2026-10-14
> 目的：排查 DOM 中出现的 `_gcrremoteframetoken` 等异常属性，定位并修复服务端与客户端 Hydration 不一致问题。

---

## 1. 发现的异常属性/样式

### 1.1 `_gcrremoteframetoken`

| 项目 | 结论 |
|------|------|
| **属性名** | `_gcrremoteframetoken` |
| **来源** | **浏览器扩展/Chromium 内置功能注入**（Google Chrome Remote 或翻译功能），非项目代码 |
| **在服务端 HTML 中** | ❌ 不存在（0 次出现） |
| **在 https-proxy 转发后** | ❌ 不存在（0 次出现） |
| **在代码库中** | ❌ 不存在（全量 grep 无匹配） |
| **影响** | 该属性由浏览器在 Hydration **之后**注入到 DOM 元素上，不影响 React Hydration 过程本身。React 不会对 `data-*` 或下划线前缀的未知属性报 Hydration 警告（除非它们出现在 SSR HTML 中）。 |

**验证方法：**
```bash
# 服务端原始 HTML — 确认不含该属性
for route in "/" "/intake" "/encounters" "/patients" "/pharmacy/herbs"; do
  curl -s "http://localhost:3000$route" | grep -c '_gcrremoteframetoken'
done
# 结果：全部 0

# 通过 HTTPS 代理（3443）的 HTML
curl -sk https://127.0.0.1:3443/ | grep -c '_gcrremoteframetoken'
# 结果：0
```

**结论：`_gcrremoteframetoken` 是浏览器扩展注入的客户端属性，不在服务端 HTML 中，不构成 Hydration 不匹配。无需在代码中处理。**

### 1.2 其他扩展注入属性检查

检查了常见浏览器扩展注入的属性，全部在服务端 HTML 中不存在：

| 属性 | 服务端 HTML | 来源 |
|------|------------|------|
| `data-grammarly` | ❌ 不存在 | Grammarly 扩展 |
| `data-latrek` | ❌ 不存在 | 分析扩展 |
| `data-scrape` | ❌ 不存在 | 抓取扩展 |
| `data-cfhash` | ❌ 不存在 | Cloudflare |
| `data-cke` | ❌ 不存在 | CKEditor |
| `data-aid` | ❌ 不存在 | AI 助手扩展 |
| `cwil-timer` | ❌ 不存在 | 性能扩展 |

---

## 2. 项目自身的 Hydration 风险（已修复）

以下是在排查中发现的项目代码中可能导致 SSR/客户端渲染不一致的问题，均已修复：

### 2.1 VoiceRecorder `navigator.mediaDevices` 渲染时读取 ✅ 已修复

| 项目 | 说明 |
|------|------|
| **文件** | `components/VoiceRecorder.tsx` |
| **问题** | `micAvailable = typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia` 在渲染时直接读取 `navigator`。SSR 时 `navigator` 不存在 → `micAvailable=false` → 渲染文件上传 UI；客户端 HTTPS 时 → `micAvailable=true` → 渲染麦克风按钮。SSR 和首次客户端渲染输出不同的 DOM 树。 |
| **修复** | 新增 `mounted` guard：`const [mounted, setMounted] = useState(false)` + `useEffect(() => setMounted(true), [])`。`micAvailable = mounted && typeof navigator !== ...`。mount 前渲染稳定占位符 `🎤 语音录入…`。 |
| **状态** | ✅ 已修复，TypeScript 编译通过 |

### 2.2 iOS `format-detection` 自动链接化 ✅ 已修复

| 项目 | 说明 |
|------|------|
| **文件** | `app/layout.tsx` |
| **问题** | iOS Safari 自动将页面中的电话号码、邮箱、日期、地址识别为 `<a>` 链接，篡改文本节点，导致 SSR HTML 与客户端 DOM 不一致。患者手机号、日期等字段尤易受影响。 |
| **修复** | 在 `metadata` 中添加 `formatDetection: { telephone: false, date: false, email: false, address: false }`。 |
| **验证** | 渲染的 HTML 确认包含 `<meta name="format-detection" content="telephone=no, date=no, address=no, email=no"/>` |
| **状态** | ✅ 已修复 |

### 2.3 viewport meta 标签 ✅ 已确认/显式导出

| 项目 | 说明 |
|------|------|
| **文件** | `app/layout.tsx` |
| **说明** | Next.js 15 会自动注入默认 viewport，但显式导出 `export const viewport: Viewport = { width: "device-width", initialScale: 1 }` 确保移动端按设备宽度渲染。 |
| **验证** | 渲染的 HTML 确认包含 `<meta name="viewport" content="width=device-width, initial-scale=1"/>` |
| **状态** | ✅ 已确认 |

### 2.4 移动端侧边栏无汉堡菜单按钮 ✅ 已修复（非 hydration 问题，但导致移动端不可用）

| 项目 | 说明 |
|------|------|
| **文件** | `components/Sidebar.tsx` |
| **问题** | 侧边栏在 `<lg` 屏幕宽度时用 `-translate-x-full` 隐藏，但代码中没有任何 `setOpen(true)` 调用——即没有打开侧边栏的按钮。移动端用户无法导航。 |
| **修复** | 新增汉堡菜单按钮：`lg:hidden`，点击 `setOpen(true)`，固定在左上角 `z-50`。 |
| **状态** | ✅ 已修复 |

---

## 3. 已确认无问题的项目

以下经过检查，确认不会导致 Hydration 不一致：

| 检查项 | 结论 |
|--------|------|
| **AppShell mounted guard** | ✅ `isPatientIntake = mounted && pathname?.startsWith("/intake")`，mount 前渲染医生端 shell（与 SSR 一致） |
| **Sidebar mounted guard** | ✅ `activePath = mounted ? pathname : ""`，mount 前不高亮（与 SSR 一致） |
| **Breadcrumb mounted guard** | ✅ `parts = mounted && pathname ? ... : []`，mount 前渲染空面包屑（与 SSR 一致） |
| **QRCodeImage** | ✅ `dataUrl` 通过 `useEffect` 生成（QRCode.toDataURL），mount 前渲染空占位，无 SSR 不一致 |
| **Date.now()/Math.random()** | ✅ 渲染时未使用（全量 grep 无匹配） |
| **localStorage/sessionStorage** | ✅ 渲染时未使用（全量 grep 无匹配） |
| **dangerouslySetInnerHTML** | ✅ 未使用 |
| **CSS-in-JS (styled-components/emotion)** | ✅ 未使用，纯 Tailwind |
| **第三方脚本/analytics** | ✅ 未引入（package.json 仅 5 个依赖，无分析/追踪包） |
| **next/script 标签** | ✅ 未使用 |
| **middleware.ts** | ✅ 不存在 |
| **suppressHydrationWarning** | ✅ 未使用（未掩盖任何问题） |
| **https-proxy.js** | ✅ 纯 pipe 转发，不修改响应体（`proxyRes.pipe(res, { end: true })`） |
| **内联 style** | ✅ 仅 3 处：QRCodeImage（useEffect 后渲染）、VoiceRecorder audioLevel meter（mounted guard 内）、QRCodeImage borderRadius（静态值）——均安全 |
| **Server Components** | ✅ `app/page.tsx`、`app/encounters/[eid]/page.tsx` 等通过 `apiGet` 服务端获取数据并序列化为 props，不读 `window`/`navigator` |
| **API_BASE SSR/CSR 差异** | ✅ `_isServer ? "http://127.0.0.1:8001" : ""`——服务端用绝对 URL，客户端用相对路径。由于调用方分别是 Server Component（SSR fetch）和 Client Component（浏览器 fetch），不产生 hydration 不一致 |
| **html/body 标签** | ✅ `<html lang="zh-CN">` + `<body class="min-h-screen antialiased">`——SSR 和客户端一致，无异常属性 |

---

## 4. 修复方式总结

| 修复 | 文件 | 方法 |
|------|------|------|
| VoiceRecorder hydration guard | `components/VoiceRecorder.tsx` | `mounted` guard + 稳定占位符（useEffect 延迟到客户端再渲染 mic/upload UI） |
| iOS format-detection | `app/layout.tsx` | `metadata.formatDetection` 禁用电话/日期/邮箱/地址自动链接化 |
| viewport 显式导出 | `app/layout.tsx` | `export const viewport: Viewport` |
| 移动端汉堡菜单 | `components/Sidebar.tsx` | 新增 `lg:hidden` 按钮，`setOpen(true)` 打开侧边栏抽屉 |

所有修复均遵循"用 useEffect 延迟到客户端再应用"的原则，不使用 `suppressHydrationWarning` 掩盖问题，不破坏输入框/语音录制/上传/按钮交互。

---

## 5. 验证结果

### 5.1 服务端原始 HTML 检查

```
<html lang="zh-CN">                        ← 干净，无异常属性
<body class="min-h-screen antialiased">    ← 干净，无异常属性
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<meta name="format-detection" content="telephone=no, date=no, address=no, email=no"/>
```

### 5.2 多路由检查

| 路由 | HTTP | `_gcrremoteframetoken` | 其他扩展属性 |
|------|------|------------------------|-------------|
| `/` | 200 | 0 | 无 |
| `/intake` | 200 | 0 | 无 |
| `/encounters` | 200 | 0 | 无 |
| `/patients` | 200 | 0 | 无 |
| `/pharmacy/herbs` | 200 | 0 | 无 |

### 5.3 HTTPS 代理检查

| 检查 | 结果 |
|------|------|
| `<html>` 标签 | `lang="zh-CN"`（无异常属性） |
| `<body>` 标签 | `class="min-h-screen antialiased"`（无异常属性） |
| `_gcrremoteframetoken` | 0 次 |
| 响应体修改 | 无（纯 pipe 转发） |

### 5.4 代码库完整性

| 检查 | 结果 |
|------|------|
| TypeScript 编译 | ✅ 无错误 |
| `suppressHydrationWarning` 使用 | 未使用 |
| 所有 client 组件的浏览器 API 访问 | 均有 `mounted` guard 或在 `useEffect`/回调中 |

---

## 6. 无法确认来源的项目

| 项目 | 说明 |
|------|------|
| **`_gcrremoteframetoken`** | 经验证不在服务端 HTML 中，确认为浏览器扩展/Chromium 内置功能在 Hydration 后注入。**但无法确认具体是哪个扩展**（可能是 Chrome Remote Desktop、Google 翻译、或其他 Chromium 内置远程帧功能）。建议在干净浏览器（无扩展）中测试以确认。 |

---

## 7. 建议

1. **干净浏览器测试**：在无扩展的 Chrome 或 Firefox 中打开应用，确认无 Hydration 警告。原问题环境中的 `_gcrremoteframetoken` 应消失。
2. **移动端测试**：通过 `https://<server-ip>:3443` 在手机上访问（需接受自签名证书），确认汉堡菜单可用、侧边栏可打开、VoiceRecorder 不闪烁。
3. **React DevTools**：在开发环境中使用 React DevTools 的 "Highlight updates when components render" 功能，确认无不必要的重渲染。
