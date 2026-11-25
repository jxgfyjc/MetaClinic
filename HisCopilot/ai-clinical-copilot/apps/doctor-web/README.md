# Doctor Web — AI Clinical Copilot 医生工作台 (MVP)

最小化前端：Next.js (App Router) + TypeScript + Tailwind。单进程承载 doctor / patient / admin 三套页面（按 instruct #70 关键页面），通过 `NEXT_PUBLIC_API_BASE` 指向后端 FastAPI（默认 http://127.0.0.1:8001）。

> MVP 阶段不引入额外 UI 库；fetch 直连后端，AI 失败时降级展示（instruct #30）。

## 启动
```bash
npm install
npm run dev    # http://localhost:3000
```
.env.local:
```
NEXT_PUBLIC_API_BASE=http://127.0.0.1:8001
```
