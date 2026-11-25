1.Hydration failed because the server rendered HTML didn't match the client. As a result this tree will be regenerated on the client. This can happen if a SSR-ed Client Component used

- A server/client branch `if (typeof window !== 'undefined')`.
- Variable input such as `Date.now()` or `Math.random()` which changes each time it's called.
- Date formatting in a user's locale which doesn't match the server.
- External changing data without sending a snapshot of it along with the HTML.
- Invalid HTML tag nesting.

It can also happen if the client has a browser extension installed which messes with the HTML before React loaded.

See more info here: https://nextjs.org/docs/messages/react-hydration-error

这不是服务端故障，而是你前端应用（大概率是 Next.js）的代码问题，核心原因是服务端渲染（SSR）生成的 HTML 与浏览器端（客户端）首次渲染的结果不一致，导致 React 在“接管”（hydration）页面时对不上号

2.ASR 不可用: 503: ASR server error: 503;OCR 不可用（500）
手机端尝试用语音显示"麦克风无法访问: undefined is not an object (evaluating 'navigator.mediaDevices.getUserMedia')"

Error: Hydration failed because the server rendered HTML didn't match the client. As a result this tree will be regenerated on the client. This can happen if a SSR-ed Client Component used:

- A server/client branch `if (typeof window !== 'undefined')`.
- Variable input such as `Date.now()` or `Math.random()` which changes each time it's called.
- Date formatting in a user's locale which doesn't match the server.
- External changing data without sending a snapshot of it along with the HTML.
- Invalid HTML tag nesting.

It can also happen if the client has a browser extension installed which messes with the HTML before React loaded.

https://react.dev/link/hydration-mismatch

  ...
    <LoadingBoundary loading={null}>
      <HTTPAccessFallbackBoundary notFound={undefined} forbidden={undefined} unauthorized={undefined}>
        <RedirectBoundary>
          <RedirectErrorBoundary router={{...}}>
            <InnerLayoutRouter parallelRouterKey="children" url="/encounter..." tree={[...]} childNodes={Map} ...>
              <ClientPageRoot Component={function NewEncounterPage} searchParams={{}} params={{}}>
                <NewEncounterPage params={Promise} searchParams={Promise}>
                  <div className="space-y-4">
                    <div>
                    <div>
                    <div className="card space...">
                      <h2>
                      <div>
                        <label>
                        <textarea>
                        <div className="mt-1">
                          <VoiceRecorder onTranscribe={function onTranscribe}>
                            <div className="inline-fle...">
+                             <button
+                               type="button"
+                               onClick={function VoiceRecorder.useCallback[startRecording]}
+                               disabled={false}
+                               className="btn text-xs btn-ghost"
+                             >
-                             <label className="btn btn-ghost text-xs cursor-pointer">
                      ...
                    ...
              ...

    at throwOnHydrationMismatch (webpack-internal:///(app-pages-browser)/./node_modules/next/dist/compiled/react-dom/cjs/react-dom-client.development.js:4129:11)
    at beginWork (webpack-internal:///(app-pages-browser)/./node_modules/next/dist/compiled/react-dom/cjs/react-dom-client.development.js:9935:17)
    at runWithFiberInDEV (webpack-internal:///(app-pages-browser)/./node_modules/next/dist/compiled/react-dom/cjs/react-dom-client.development.js:544:16)
    at performUnitOfWork (webpack-internal:///(app-pages-browser)/./node_modules/next/dist/compiled/react-dom/cjs/react-dom-client.development.js:15064:22)
    at workLoopConcurrent (webpack-internal:///(app-pages-browser)/./node_modules/next/dist/compiled/react-dom/cjs/react-dom-client.development.js:15058:9)
    at renderRootConcurrent (webpack-internal:///(app-pages-browser)/./node_modules/next/dist/compiled/react-dom/cjs/react-dom-client.development.js:15033:15)
    at performWorkOnRoot (webpack-internal:///(app-pages-browser)/./node_modules/next/dist/compiled/react-dom/cjs/react-dom-client.development.js:14350:13)
    at performWorkOnRootViaSchedulerTask (webpack-internal:///(app-pages-browser)/./node_modules/next/dist/compiled/react-dom/cjs/react-dom-client.development.js:15955:7)
    at MessagePort.performWorkUntilDeadline (webpack-internal:///(app-pages-browser)/./node_modules/next/dist/compiled/scheduler/cjs/scheduler.development.js:44:48)