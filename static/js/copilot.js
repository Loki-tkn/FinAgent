/**
 * FinAgent Copilot - Floating Chat UI & WebSocket Client
 * =====================================================
 * Implements the slide-in Human-In-The-Loop Copilot interface.
 * Connects to REST (/api/copilot/chat) with real-time WebSocket fallback/streaming.
 */

(function () {
    let copilotOpen = false;
    let copilotSessionId = 'default';
    let copilotWs = null;
    let isSending = false;
    let wsReconnectTimer = null;
    let wsConnected = false;

    // Default welcome state
    const WELCOME_MESSAGE = {
        role: 'assistant',
        text: `👋 **Hello! I'm your FinAgent Copilot.**\n\nI can analyze your finances, explain agent reasoning, detect anomalies, review savings goals, or tune runtime parameters in real-time. How can I help you today?`,
        intent: 'GREETING',
        confidence: 1.0,
        sources: [],
        timestamp: new Date().toISOString(),
        disclaimer: null
    };

    // Safe HTML Escaping
    function escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    // Markdown-lite Formatter for Financial Copilot
    function formatMarkdown(text) {
        if (!text) return '';
        let escaped = escapeHtml(text);

        // Code blocks: ```...```
        escaped = escaped.replace(/```([\s\S]*?)```/g, function (match, code) {
            return `<pre class="copilot-code-block"><code>${code.trim()}</code></pre>`;
        });

        // Inline code: `...`
        escaped = escaped.replace(/`([^`]+)`/g, '<code class="copilot-inline-code">$1</code>');

        // Bold: **text**
        escaped = escaped.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

        // Italic: *text*
        escaped = escaped.replace(/\*([^*]+)\*/g, '<em>$1</em>');

        // Event / Proposal IDs: #42 or event #42 or proposal #12 -> interactive links
        escaped = escaped.replace(/(?:event\s*#?|(?<![&\w])#)(\d+)\b/gi, function (match, id) {
            return `<button type="button" class="copilot-event-link" onclick="window.askCopilotPrompt('Explain event ${id}')" title="Inspect event #${id}">${match}</button>`;
        });

        // Format bullet lines starting with • or -
        const lines = escaped.split('\n');
        const formattedLines = lines.map(line => {
            const trimmed = line.trim();
            if (trimmed.startsWith('• ') || trimmed.startsWith('- ')) {
                const content = trimmed.substring(2);
                return `<div class="copilot-bullet-line"><span class="copilot-bullet-dot">▪</span><span>${content}</span></div>`;
            }
            if (trimmed.startsWith('### ')) {
                return `<div class="copilot-heading-3">${trimmed.substring(4)}</div>`;
            }
            if (trimmed.startsWith('## ')) {
                return `<div class="copilot-heading-2">${trimmed.substring(3)}</div>`;
            }
            return line;
        });

        return formattedLines.join('<br>');
    }

    // Format Timestamp
    function formatTime(isoStr) {
        try {
            const d = isoStr ? new Date(isoStr) : new Date();
            return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        } catch {
            return '';
        }
    }

    // Toggle Chat Panel
    function toggleCopilot(force) {
        const panel = document.getElementById('copilot-panel');
        const bubble = document.getElementById('copilot-bubble-btn');
        if (!panel || !bubble) {
            console.warn('[Copilot] Elements not found: panel=', panel, 'bubble=', bubble);
            return;
        }

        copilotOpen = (typeof force === 'boolean') ? force : !copilotOpen;
        console.log('[Copilot] toggleCopilot -> open:', copilotOpen);

        if (copilotOpen) {
            panel.classList.add('open');
            bubble.classList.add('active');
            panel.setAttribute('aria-hidden', 'false');
            const input = document.getElementById('copilot-input');
            if (input) {
                setTimeout(() => input.focus(), 150);
            }
            // Clear unread badge on open
            const badge = document.getElementById('copilot-bubble-badge');
            if (badge) {
                badge.style.display = 'none';
                badge.textContent = '0';
            }
        } else {
            panel.classList.remove('open');
            bubble.classList.remove('active');
            panel.setAttribute('aria-hidden', 'true');
        }

        if (window.lucide && typeof window.lucide.createIcons === 'function') {
            window.lucide.createIcons();
        }
    }

    // Render a Message
    function appendMessage(msg) {
        const container = document.getElementById('copilot-messages');
        if (!container) return;

        const isUser = msg.role === 'user';
        const msgDiv = document.createElement('div');
        msgDiv.className = `copilot-msg ${isUser ? 'copilot-msg-user' : 'copilot-msg-assistant'}`;

        const timeStr = formatTime(msg.timestamp);

        let sourcesHtml = '';
        if (msg.sources && msg.sources.length > 0) {
            sourcesHtml = `
                <details class="copilot-sources-collapse">
                    <summary class="copilot-sources-summary">
                        <i data-lucide="layers"></i>
                        <span>${msg.sources.length} Context Evidence Cited</span>
                        <i data-lucide="chevron-down" class="copilot-chevron"></i>
                    </summary>
                    <div class="copilot-sources-content">
                        ${msg.sources.map(s => `
                            <div class="copilot-source-item" onclick="window.askCopilotPrompt('Explain event ${s.event_id || ''}')" title="Click to inspect event">
                                <div class="copilot-source-header">
                                    <span class="copilot-lane-tag lane-${(s.lane || '').toLowerCase()}">${escapeHtml(s.lane)}</span>
                                    ${s.event_id ? `<span class="copilot-source-id">#${s.event_id}</span>` : ''}
                                    <span class="copilot-source-time">${formatTime(s.timestamp)}</span>
                                </div>
                                <div class="copilot-source-summary">${escapeHtml(s.summary)}</div>
                            </div>
                        `).join('')}
                    </div>
                </details>
            `;
        }

        let disclaimerHtml = '';
        if (msg.disclaimer) {
            disclaimerHtml = `
                <div class="copilot-disclaimer">
                    <i data-lucide="shield"></i>
                    <span>${escapeHtml(msg.disclaimer)}</span>
                </div>
            `;
        }

        let metaHtml = '';
        if (!isUser && msg.intent) {
            const confPct = Math.round((msg.confidence || 0) * 100);
            metaHtml = `
                <div class="copilot-meta-row">
                    <span class="copilot-intent-badge" title="Detected Intent"><i data-lucide="tag"></i> ${escapeHtml(msg.intent)}</span>
                    <span class="copilot-conf-badge" title="Engine Confidence">${confPct}% conf</span>
                </div>
            `;
        }

        msgDiv.innerHTML = `
            <div class="copilot-msg-avatar">
                <i data-lucide="${isUser ? 'user' : 'sparkles'}"></i>
            </div>
            <div class="copilot-msg-body">
                <div class="copilot-msg-header">
                    <span class="copilot-msg-sender">${isUser ? 'You' : 'FinAgent Copilot'}</span>
                    <span class="copilot-msg-time">${timeStr}</span>
                </div>
                ${metaHtml}
                <div class="copilot-msg-text">${formatMarkdown(msg.text)}</div>
                ${sourcesHtml}
                ${disclaimerHtml}
            </div>
        `;

        container.appendChild(msgDiv);
        container.scrollTop = container.scrollHeight;

        if (window.lucide && typeof window.lucide.createIcons === 'function') {
            window.lucide.createIcons();
        }
    }

    // Show Thinking Bubble
    function showThinkingIndicator() {
        hideThinkingIndicator();
        const container = document.getElementById('copilot-messages');
        if (!container) return;

        const thinkingDiv = document.createElement('div');
        thinkingDiv.id = 'copilot-thinking';
        thinkingDiv.className = 'copilot-msg copilot-msg-assistant copilot-thinking-msg';
        thinkingDiv.innerHTML = `
            <div class="copilot-msg-avatar">
                <i data-lucide="sparkles"></i>
            </div>
            <div class="copilot-msg-body">
                <div class="copilot-thinking-content">
                    <span class="copilot-thinking-text">Copilot analyzing context</span>
                    <span class="copilot-dot-pulse"><span></span><span></span><span></span></span>
                </div>
            </div>
        `;
        container.appendChild(thinkingDiv);
        container.scrollTop = container.scrollHeight;

        if (window.lucide && typeof window.lucide.createIcons === 'function') {
            window.lucide.createIcons();
        }
    }

    // Hide Thinking Bubble
    function hideThinkingIndicator() {
        const el = document.getElementById('copilot-thinking');
        if (el) el.remove();
    }

    // Send Message to API
    async function sendCopilotMessage(userText) {
        const trimmed = (userText || '').trim();
        if (!trimmed || isSending) return;

        const input = document.getElementById('copilot-input');
        const sendBtn = document.getElementById('copilot-send-btn');

        // Reset input immediately
        if (input) {
            input.value = '';
            input.style.height = 'auto';
        }
        if (sendBtn) sendBtn.disabled = true;

        // Append user message
        appendMessage({
            role: 'user',
            text: trimmed,
            timestamp: new Date().toISOString()
        });

        isSending = true;
        showThinkingIndicator();

        // Get active role
        const activeRole = window.currentRole || 'User';
        const rolePill = document.getElementById('copilot-role-text');
        if (rolePill) rolePill.textContent = activeRole;

        try {
            const res = await fetch('/api/copilot/chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-User-Role': activeRole
                },
                body: JSON.stringify({
                    message: trimmed,
                    session_id: copilotSessionId
                })
            });

            hideThinkingIndicator();

            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                const errMsg = errData.detail || errData.error || `Server responded with status ${res.status}`;
                appendMessage({
                    role: 'assistant',
                    text: `⚠️ **Error processing request:** ${errMsg}\n\nPlease try again or verify that the server is running.`,
                    intent: 'ERROR',
                    confidence: 0,
                    timestamp: new Date().toISOString()
                });
                return;
            }

            const data = await res.json();
            appendMessage({
                role: 'assistant',
                text: data.reply || '(No reply returned)',
                intent: data.intent || 'GENERAL',
                confidence: typeof data.confidence === 'number' ? data.confidence : 1.0,
                sources: data.sources || [],
                disclaimer: data.disclaimer || null,
                timestamp: data.timestamp || new Date().toISOString()
            });

        } catch (err) {
            console.error('Copilot Chat Error:', err);
            hideThinkingIndicator();
            appendMessage({
                role: 'assistant',
                text: `❌ **Network connection failed.**\nCould not reach the FinAgent Copilot API. Ensure the backend server is active at \`http://127.0.0.1:8000\`.`,
                intent: 'NETWORK_ERROR',
                confidence: 0,
                timestamp: new Date().toISOString()
            });
        } finally {
            isSending = false;
            if (sendBtn && input && input.value.trim().length > 0) {
                sendBtn.disabled = false;
            }
        }
    }

    // Trigger Emergency Kill Switch via Copilot
    async function triggerKillSwitch() {
        const reason = prompt('Emergency Kill Switch: Enter reason for halting all agent actions:', 'User manual emergency halt from Copilot UI');
        if (!reason) return;

        const activeRole = window.currentRole || 'User';

        try {
            const res = await fetch('/api/copilot/kill-switch', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-User-Role': activeRole
                },
                body: JSON.stringify({
                    reason: reason,
                    session_id: copilotSessionId
                })
            });

            const data = await res.json();
            if (res.ok) {
                showSafetyBanner(`🚨 EMERGENCY HALTED: ${data.reason || reason}`);
                appendMessage({
                    role: 'assistant',
                    text: `🚨 **EMERGENCY KILL SWITCH ACTIVATED**\n\n• **Status:** \`${data.status}\`\n• **Reason:** ${data.reason}\n• **Action:** All pending automated orders and background agent proposals have been rejected/halted immediately.\n• **Event ID:** #${data.event_id || 'N/A'}`,
                    intent: 'KILL_SWITCH',
                    confidence: 1.0,
                    timestamp: new Date().toISOString()
                });
                if (typeof window.showToast === 'function') {
                    window.showToast('🚨 Emergency kill switch activated!', 'error');
                }
            } else {
                alert(`Kill switch failed: ${data.detail || 'Unauthorized'}`);
            }
        } catch (err) {
            console.error('Kill switch error:', err);
            alert('Failed to contact kill-switch endpoint.');
        }
    }

    function showSafetyBanner(msg) {
        const banner = document.getElementById('copilot-safety-banner');
        const text = document.getElementById('copilot-safety-msg');
        if (banner && text) {
            text.textContent = msg;
            banner.style.display = 'flex';
        }
    }

    // Reset Chat Messages
    function resetChat() {
        const container = document.getElementById('copilot-messages');
        if (container) {
            container.innerHTML = '';
            appendMessage(WELCOME_MESSAGE);
        }
    }

    // Connect WebSocket for Live Agent Stream
    function initWebSocket() {
        if (wsConnected || (copilotWs && copilotWs.readyState === WebSocket.OPEN)) return;

        try {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/api/copilot/ws?session_id=${encodeURIComponent(copilotSessionId)}`;

            copilotWs = new WebSocket(wsUrl);

            copilotWs.onopen = function () {
                wsConnected = true;
                updateConnStatus(true, 'Live Stream');
                console.log('Copilot WebSocket connected');
            };

            copilotWs.onmessage = function (event) {
                try {
                    const data = JSON.parse(event.data);
                    handleWsMessage(data);
                } catch (e) {
                    console.error('Error parsing WS message', e);
                }
            };

            copilotWs.onclose = function () {
                wsConnected = false;
                updateConnStatus(false, 'REST Ready');
                // Auto reconnect after 5s
                clearTimeout(wsReconnectTimer);
                wsReconnectTimer = setTimeout(initWebSocket, 5000);
            };

            copilotWs.onerror = function () {
                wsConnected = false;
                updateConnStatus(false, 'REST Ready');
            };
        } catch (e) {
            console.log('WS init error, running in REST mode:', e);
            updateConnStatus(false, 'REST Ready');
        }
    }

    function updateConnStatus(isLive, label) {
        const dot = document.getElementById('copilot-status-indicator');
        const text = document.getElementById('copilot-conn-status');
        const bubbleDot = document.querySelector('.bubble-status-dot');

        if (dot) {
            dot.className = `copilot-status-indicator ${isLive ? 'live' : 'ready'}`;
            dot.title = isLive ? 'WebSocket Live Streaming' : 'REST Mode Ready';
        }
        if (text) text.textContent = label;
        if (bubbleDot) {
            bubbleDot.className = `bubble-status-dot ${isLive ? 'live' : 'ready'}`;
        }
    }

    function handleWsMessage(data) {
        if (data.type === 'event') {
            // If Kill Switch was broadcasted
            if (data.lane === 'kill_switch') {
                showSafetyBanner(`🚨 EMERGENCY HALT BROADCAST: ${data.payload ? data.payload.reason : 'Kill switch active'}`);
            }

            // Increment unread badge if panel is closed
            if (!copilotOpen) {
                const badge = document.getElementById('copilot-bubble-badge');
                if (badge) {
                    const count = (parseInt(badge.textContent, 10) || 0) + 1;
                    badge.textContent = count;
                    badge.style.display = 'flex';
                }
            }
        }
    }

    // Expose Global Helper for Suggestion Chips and Event Links
    window.askCopilotPrompt = function (query) {
        if (!copilotOpen) {
            toggleCopilot(true);
        }
        sendCopilotMessage(query);
    };

    window.toggleCopilot = toggleCopilot;

    function initCopilot() {
        // Render initial welcome message
        resetChat();

        // Bubble toggle button
        const bubbleBtn = document.getElementById('copilot-bubble-btn');
        if (bubbleBtn) {
            bubbleBtn.onclick = function (e) {
                if (e) {
                    e.preventDefault();
                    e.stopPropagation();
                }
                toggleCopilot();
            };
        }

        // Sidebar nav trigger button
        const navCopilot = document.getElementById('nav-copilot');
        if (navCopilot) {
            navCopilot.onclick = function (e) {
                if (e) {
                    e.preventDefault();
                    e.stopPropagation();
                }
                toggleCopilot(true);
            };
        }

        // Close button inside panel
        const closeBtn = document.getElementById('copilot-btn-close');
        if (closeBtn) {
            closeBtn.addEventListener('click', (e) => {
                e.preventDefault();
                toggleCopilot(false);
            });
        }

        // Clear chat button
        const clearBtn = document.getElementById('copilot-btn-clear');
        if (clearBtn) {
            clearBtn.addEventListener('click', (e) => {
                e.preventDefault();
                resetChat();
            });
        }

        // Kill Switch button
        const killBtn = document.getElementById('copilot-btn-killswitch');
        if (killBtn) {
            killBtn.addEventListener('click', (e) => {
                e.preventDefault();
                triggerKillSwitch();
            });
        }

        // Input and Send button handling
        const input = document.getElementById('copilot-input');
        const sendBtn = document.getElementById('copilot-send-btn');
        const form = document.getElementById('copilot-form');

        if (input) {
            // Auto-resize textarea
            input.addEventListener('input', function () {
                this.style.height = 'auto';
                this.style.height = Math.min(this.scrollHeight, 120) + 'px';
                if (sendBtn) {
                    sendBtn.disabled = this.value.trim().length === 0 || isSending;
                }
            });

            // Enter to send (Shift+Enter for newline)
            input.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    if (!isSending && input.value.trim().length > 0) {
                        sendCopilotMessage(input.value);
                    }
                }
            });
        }

        if (form) {
            form.addEventListener('submit', function (e) {
                e.preventDefault();
                if (input && input.value.trim().length > 0 && !isSending) {
                    sendCopilotMessage(input.value);
                }
            });
        }

        // Quick Suggestion Chips
        document.querySelectorAll('.suggestion-chip').forEach(btn => {
            btn.addEventListener('click', function (e) {
                e.preventDefault();
                const query = this.getAttribute('data-query');
                if (query) {
                    window.askCopilotPrompt(query);
                }
            });
        });

        // Close on Escape key
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && copilotOpen) {
                toggleCopilot(false);
            }
        });

        // Initialize WebSocket connection
        initWebSocket();

        // Update role indicator when currentRole changes
        setInterval(() => {
            const roleEl = document.getElementById('copilot-role-text');
            if (roleEl && window.currentRole) {
                if (roleEl.textContent !== window.currentRole) {
                    roleEl.textContent = window.currentRole;
                }
            }
        }, 1000);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initCopilot);
    } else {
        initCopilot();
    }
})();
