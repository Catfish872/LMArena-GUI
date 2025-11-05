// ==UserScript==
// @name         LMArena API Bridge (Interceptor + Auto-Trigger Edition)
// @namespace    http://tampermonkey.net/
// @version      4.0
// @description  Intercepts fetch requests, and automatically prepares the UI by simulating input to enable the send button, awaiting an external trigger.
// @author       Lianues & Gemini
// @match        https://lmarena.ai/*
// @match        https://*.lmarena.ai/*
// @icon         https://www.google.com/s2/favicons?sz=64&domain=lmarena.ai
// @grant        none
// @run-at       document-start
// ==/UserScript==

(function () {
    'use strict';

    // --- 配置 ---
    const SERVER_URL = "ws://localhost:5102/ws";
    let socket;
    let isCaptureModeActive = false;
    let pendingApiBridgeRequest = null;

    // --- WebSocket 连接逻辑 ---
    function connect() {
        console.log(`[API Bridge] 正在连接到本地服务器: ${SERVER_URL}...`);
        socket = new WebSocket(SERVER_URL);

        socket.onopen = () => {
            console.log("[API Bridge] ✅ 与本地服务器的 WebSocket 连接已建立。");
            document.title = "✅ " + document.title;
        };

        socket.onmessage = (event) => {
            try {
                const message = JSON.parse(event.data);
                console.log(`[API Bridge] ⬇️ 收到指令/请求:`, message);

                if (message.command) {
                    handleCommand(message.command);
                    return;
                }

                // --- 核心修改点：接收到Payload后，自动准备UI ---
                if (message.request_id && message.payload) {
                    pendingApiBridgeRequest = {
                        requestId: message.request_id,
                        payload: message.payload,
                        isProcessing: false
                    };
                    console.log(`[API Bridge] 请求 ${message.request_id.substring(0, 8)} 已暂存，正在激活发送按钮...`);
                    document.title = "▶️ " + document.title;

                    // --- 新增：自动化准备逻辑 ---
                    const inputElement = document.querySelector('textarea');
                    if (!inputElement) {
                        console.error("[API Bridge] 自动化准备失败: 未找到输入框(textarea)。");
                        // (可选)可以向后端发送一个失败信号
                        // socket.send(JSON.stringify({ status: 'error', request_id: message.request_id, reason: 'textarea not found' }));
                        return;
                    }

                    // 步骤 1: 完美模拟输入以激活按钮
                    const triggerContent = "1"; // 使用一个简单的字符来激活按钮
                    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
                    nativeInputValueSetter.call(inputElement, triggerContent);
                    const inputEvent = new Event('input', { bubbles: true });
                    inputElement.dispatchEvent(inputEvent);

                    // 步骤 2: 等待UI更新后，检查按钮状态并发送就绪信号
                    setTimeout(() => {
                        // 寻找发送按钮的选择器，可能需要根据页面结构微调
                        const sendButton = inputElement.closest('div')?.parentElement?.querySelector('button');
                        if (sendButton && !sendButton.disabled) {
                            console.log('[API Bridge] ✅ 发送按钮已激活，向后端发送“准备就绪”信号。');
                            // 步骤 3: 发送回执给后端，通知它可以执行点击了
                            socket.send(JSON.stringify({ status: 'ready_to_click', request_id: message.request_id }));
                        } else {
                             console.error("[API Bridge] 自动化准备失败: 按钮未能被激活。请检查按钮选择器或页面结构。");
                             // (可选)发送失败信号
                        }
                    }, 150); // 给予150ms的UI更新时间
                }
            } catch (error) {
                console.error("[API Bridge] 处理服务器消息时出错:", error);
            }
        };

        socket.onclose = () => {
            console.warn("[API Bridge] 🔌 与本地服务器的连接已断开。将在5秒后尝试重新连接...");
            if (document.title.startsWith("✅ ") || document.title.startsWith("▶️ ")) {
                document.title = document.title.substring(2);
            }
            setTimeout(connect, 5000);
        };

        socket.onerror = (error) => {
            console.error("[API Bridge] ❌ WebSocket 发生错误:", error);
            socket.close();
        };
    }

    function handleCommand(command) {
        console.log(`[API Bridge] 正在执行指令: ${command}`);
        switch (command) {
            case 'refresh':
            case 'reconnect':
                location.reload();
                break;
            case 'activate_id_capture':
                isCaptureModeActive = true;
                document.title = "🎯 " + document.title;
                console.log("[API Bridge] ✅ ID 捕获模式已激活。请在页面上触发一次 'Retry' 或 'Send' 操作。");
                break;
            case 'send_page_source':
                sendPageSource();
                break;
        }
    }

    // --- 核心逻辑：拦截并修改 Fetch 请求 (此部分逻辑保持不变) ---

    const originalFetch = window.fetch;
    window.fetch = async function (...args) {
        const url = args[0] instanceof Request ? args[0].url : String(args[0]);

        // --- ID 捕获逻辑 (保持不变) ---
        if (isCaptureModeActive && !window.isApiBridgeRequest) {
            const retryMatch = url.match(/\/nextjs-api\/stream\/retry-evaluation-session-message\/([a-f0-9-]+)\/messages\/([a-f0-9-]+)/);
             if (retryMatch) {
                const [_, sessionId, messageId] = retryMatch;
                console.log(`[API Bridge Interceptor] 🎯 在激活模式下捕获到ID！SessionID: ${sessionId}`);
                isCaptureModeActive = false;
                if (document.title.startsWith("🎯 ")) document.title = document.title.substring(2);
                sendIdsToUpdater(sessionId, messageId);
            }
        }

        // --- "偷梁换柱" 核心逻辑 (保持不变) ---
        // 当外部的 clicker.py 触发点击后，这个拦截器会捕获到"诱饵"请求并将其替换
        if (url.includes('/nextjs-api/stream/post-to-evaluation/') && pendingApiBridgeRequest && !pendingApiBridgeRequest.isProcessing) {

            pendingApiBridgeRequest.isProcessing = true;
            console.log(`[API Bridge Interceptor] 拦截到目标 fetch 请求！准备为 ${pendingApiBridgeRequest.requestId.substring(0, 8)} 注入载荷。`);

            if (document.title.startsWith("▶️ ")) {
                document.title = document.title.substring(2);
            }

            const request = new Request(args[0], args[1]);
            // 注意：我们丢弃了原始请求的payload，因为它只是一个"诱饵"
            const originalPayload = JSON.parse(await request.text());
            const { payload: bridgePayload } = pendingApiBridgeRequest;

            const newMessages = [];
            let lastMsgIdInChain = null;

            // 构建从后端传来的真实消息历史
            for (const template of bridgePayload.message_templates) {
                const currentMsgId = crypto.randomUUID();
                newMessages.push({
                    id: currentMsgId,
                    role: template.role,
                    content: template.content,
                    experimental_attachments: template.attachments || [],
                    parentMessageIds: lastMsgIdInChain ? [lastMsgIdInChain] : [],
                    participantPosition: template.participantPosition || "a",
                    modelId: template.role === 'assistant' ? bridgePayload.target_model_id : null,
                    evaluationSessionId: bridgePayload.session_id,
                    status: 'success',
                    failureReason: null,
                    reasoning: "",
                    metadata: {},
                    createdAt: new Date().toISOString(),
                    updatedAt: new Date().toISOString(),
                });
                lastMsgIdInChain = currentMsgId;
            }

            if (newMessages.length === 0) {
                 console.error("[API Bridge] 错误：从后端收到的消息模板为空，无法继续。");
                 pendingApiBridgeRequest = null;
                 return originalFetch.apply(this, args); // 放行原始请求
            }

            const finalUserMessage = newMessages[newMessages.length - 1];
            finalUserMessage.status = 'pending';

            const assistantPlaceholderId = crypto.randomUUID();
            newMessages.push({
                id: assistantPlaceholderId,
                role: 'assistant',
                content: '',
                reasoning: '',
                experimental_attachments: [],
                parentMessageIds: [finalUserMessage.id],
                participantPosition: finalUserMessage.participantPosition,
                modelId: bridgePayload.target_model_id,
                evaluationSessionId: bridgePayload.session_id,
                status: 'pending',
                failureReason: null
            });

            // "偷梁换柱": 用我们精心构造的真实数据替换整个payload
            originalPayload.messages = newMessages;
            originalPayload.modelAId = bridgePayload.target_model_id;
            originalPayload.userMessageId = finalUserMessage.id;
            originalPayload.modelAMessageId = assistantPlaceholderId;

            const newArgs = [...args];
            newArgs[1] = { ...newArgs[1], body: JSON.stringify(originalPayload) };

            console.log("[API Bridge Interceptor] 注入完成，最终发送的载荷:", JSON.stringify(originalPayload, null, 2));

            const capturedRequestId = pendingApiBridgeRequest.requestId;
            pendingApiBridgeRequest = null;

            const responsePromise = originalFetch.apply(this, newArgs);

            // 捕获响应并流式回传给后端 (保持不变)
            responsePromise.then(response => {
                // 关键修复：克隆响应对象。
                // 一个流（克隆的）给我们的回传函数，另一个（原始的）留给LMArena页面自己处理。
                // 这样就避免了对同一个流的读取锁定冲突。
                streamResponseBack(response.clone(), capturedRequestId);
            }).catch(error => handleError(error, capturedRequestId));

            return responsePromise;
        }

        return originalFetch.apply(this, args);
    };

    // --- 辅助函数 (保持不变) ---
    async function streamResponseBack(response, requestId) {
        if (!response.ok || !response.body) {
            const errorBody = await response.text();
            throw new Error(`网络响应不正常。状态: ${response.status}. 内容: ${errorBody}`);
        }
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        while (true) {
            const { value, done } = await reader.read();
            if (done) {
                console.log(`[API Bridge] ✅ 请求 ${requestId.substring(0, 8)} 的流已结束。`);
                sendToServer(requestId, "[DONE]");
                break;
            }
            sendToServer(requestId, decoder.decode(value));
        }
    }

    function handleError(error, requestId) {
        console.error(`[API Bridge] ❌ 在为请求 ${requestId.substring(0, 8)} 执行 fetch 时出错:`, error);
        sendToServer(requestId, { error: error.message });
        sendToServer(requestId, "[DONE]");
    }

    function sendToServer(requestId, data) {
        if (socket && socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ request_id: requestId, data: data }));
        } else {
            console.error("[API Bridge] 无法发送数据，WebSocket 连接未打开。");
        }
    }

    async function sendIdsToUpdater(sessionId, messageId) {
        window.isApiBridgeRequest = true;
        try {
            await fetch('http://127.0.0.1:5103/update', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sessionId, messageId })
            });
            console.log(`[API Bridge] ✅ ID 更新成功发送。`);
        } catch (err) {
            console.error('[API Bridge] 发送ID更新时出错:', err.message);
        } finally {
            window.isApiBridgeRequest = false;
        }
    }

    async function sendPageSource() {
        try {
            await fetch('http://localhost:5102/internal/update_available_models', {
                method: 'POST',
                headers: { 'Content-Type': 'text/html; charset=utf-8' },
                body: document.documentElement.outerHTML
            });
             console.log("[API Bridge] 页面源码已成功发送。");
        } catch (e) {
            console.error("[API Bridge] 发送页面源码失败:", e);
        }
    }

    // --- 启动连接 ---
    console.log("========================================");
    console.log("  LMArena API Bridge v4.0 (Interceptor + Auto-Trigger Edition) 正在运行。");
    console.log("  - 聊天功能已连接到 ws://localhost:5102");
    console.log("  - ID 捕获器将发送到 http://localhost:5103");
    console.log("========================================");

    connect();

})();
