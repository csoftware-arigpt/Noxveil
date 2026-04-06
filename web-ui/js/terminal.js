const Terminal = {
    agentId: null,
    ws: null,
    agent: null,
    allowReconnect: true,
    reconnectTimer: null,
    historyTimer: null,
    terminalMode: "unknown",
    activeSessionId: null,
    selectedSessionId: null,
    sessionCreatePending: false,
    sessions: {},
    sessionOrder: [],
    generalTranscriptLines: [],
    commandHistory: [],
    historyIndex: -1,
    historyDraft: "",
    archiveItems: [],
    archiveOffset: 0,
    archiveLimit: 20,
    archiveTotal: 0,
    archiveHasMore: false,
    archiveLoading: false,
    pendingTasks: new Map(),
    pendingTaskContexts: [],

    async init(agentId) {
        this.agentId = agentId;
        this.loadStoredCommandHistory();
        const canConnect = await this.loadAgentInfo();
        this.setupEventListeners();
        this.renderCommandHistory();
        this.renderSessionList();
        this.renderActiveTranscript();
        await this.loadRecentHistory();
        this.startHistoryRefresh();
        this.updateSessionMeta();
        this.updateSessionControls();
        if (canConnect) {
            this.connect();
        }
    },

    async loadAgentInfo() {
        try {
            const response = await Auth.apiCall(`/api/v1/agents/${encodeURIComponent(this.agentId)}`);
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const agent = await response.json();
            this.agent = agent;
            this.updateAgentInfo(agent);
            if (agent.is_alive !== true) {
                this.allowReconnect = false;
                this.setInputEnabled(false);
                this.updateStatus("offline");
                this.updateSessionMeta();
                this.updateSessionControls();
                this.appendMessage("This agent is offline, so the terminal is not available", "error");
                return false;
            }

            this.allowReconnect = true;
            this.setInputEnabled(false);
            this.updateSessionMeta();
            this.updateSessionControls();
            return true;
        } catch (error) {
            console.error("Failed to load agent info:", error);
            this.appendMessage("Agent details could not be loaded", "error");
            this.allowReconnect = false;
            this.setInputEnabled(false);
            this.updateSessionMeta();
            this.updateSessionControls();
            return false;
        }
    },

    async loadRecentHistory() {
        const container = document.getElementById("recentHistoryList");
        if (!container) {
            return;
        }

        try {
            const response = await Auth.apiCall(`/api/v1/agents/${encodeURIComponent(this.agentId)}/history?limit=8`);
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            const data = await response.json();
            this.renderHistory(data.tasks || []);
        } catch (error) {
            console.error("Failed to load recent history:", error);
            container.innerHTML = `
                <div class="history-item">
                    <div class="history-command">Recent task history could not be loaded</div>
                    <div class="history-meta">${this.escapeHtml(error.message || "Unknown error")}</div>
                </div>
            `;
        }
    },

    renderHistory(tasks) {
        const container = document.getElementById("recentHistoryList");
        if (!container) {
            return;
        }

        if (!Array.isArray(tasks) || tasks.length === 0) {
            container.innerHTML = `
                <div class="history-item">
                    <div class="history-command">No recent tasks yet</div>
                    <div class="history-meta">Queued commands and their latest output will appear here</div>
                </div>
            `;
            return;
        }

        container.innerHTML = tasks.map((task) => {
            const latestResult = Array.isArray(task.results) && task.results.length > 0 ? task.results[0] : null;
            const resultSnippet = latestResult?.output
                ? this.escapeHtml(String(latestResult.output).slice(0, 140))
                : "No result stored yet";
            return `
                <div class="history-item">
                    <div class="history-command">${this.escapeHtml(task.command || "(empty command)")}</div>
                    <div class="history-meta">
                        ${this.escapeHtml(task.status || "unknown")} · ${this.escapeHtml(this.formatTimestamp(task.created_at))}<br>
                        ${resultSnippet}${String(resultSnippet).length >= 140 ? "..." : ""}
                    </div>
                </div>
            `;
        }).join("");
    },

    async openFullHistory(forceRefresh = false) {
        const modal = document.getElementById("terminalHistoryModal");
        if (!modal) {
            return;
        }
        modal.classList.add("active");
        document.body.style.overflow = "hidden";
        if (forceRefresh || this.archiveItems.length === 0) {
            await this.loadFullHistoryPage(true);
            return;
        }
        this.renderFullHistory();
    },

    closeFullHistory() {
        const modal = document.getElementById("terminalHistoryModal");
        if (!modal) {
            return;
        }
        modal.classList.remove("active");
        document.body.style.overflow = "";
    },

    isFullHistoryOpen() {
        const modal = document.getElementById("terminalHistoryModal");
        return Boolean(modal && modal.classList.contains("active"));
    },

    async loadFullHistoryPage(reset = false) {
        if (this.archiveLoading) {
            return;
        }

        const list = document.getElementById("fullHistoryList");
        const summary = document.getElementById("fullHistorySummary");
        if (!list || !summary) {
            return;
        }

        this.archiveLoading = true;
        if (reset) {
            this.archiveItems = [];
            this.archiveOffset = 0;
            this.archiveTotal = 0;
            this.archiveHasMore = false;
            list.innerHTML = `<div class="terminal-history-empty">Loading the full command archive...</div>`;
            summary.textContent = "Loading the full command archive...";
        }
        this.updateFullHistoryControls();

        try {
            const response = await Auth.apiCall(
                `/api/v1/agents/${encodeURIComponent(this.agentId)}/history?limit=${this.archiveLimit}&offset=${this.archiveOffset}`,
            );
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            const data = await response.json();
            const tasks = Array.isArray(data.tasks) ? data.tasks : [];
            this.archiveItems = reset ? tasks : [...this.archiveItems, ...tasks];
            this.archiveOffset = this.archiveItems.length;
            this.archiveTotal = Number.isFinite(data.total) ? data.total : this.archiveItems.length;
            this.archiveHasMore = Boolean(data.has_more);
            this.renderFullHistory();
        } catch (error) {
            console.error("Failed to load full terminal history:", error);
            list.innerHTML = `
                <div class="terminal-history-empty">
                    The full terminal history could not be loaded.<br>
                    ${this.escapeHtml(error.message || "Unknown error")}
                </div>
            `;
            summary.textContent = "History could not be loaded";
        } finally {
            this.archiveLoading = false;
            this.updateFullHistoryControls();
        }
    },

    renderFullHistory() {
        const list = document.getElementById("fullHistoryList");
        const summary = document.getElementById("fullHistorySummary");
        if (!list || !summary) {
            return;
        }

        if (!Array.isArray(this.archiveItems) || this.archiveItems.length === 0) {
            list.innerHTML = `
                <div class="terminal-history-empty">
                    No executed commands are stored for this agent yet
                </div>
            `;
            summary.textContent = "0 commands in the archive";
            this.updateFullHistoryControls();
            return;
        }

        summary.textContent = `Showing ${this.archiveItems.length} of ${this.archiveTotal || this.archiveItems.length} stored commands`;
        list.innerHTML = this.archiveItems.map((task) => {
            const latestResult = Array.isArray(task.results) && task.results.length > 0 ? task.results[0] : null;
            const output = latestResult?.output || "No output stored yet";
            const status = String(task.status || "unknown").toLowerCase();
            const badgeClass = ["completed", "error", "pending", "sent"].includes(status) ? status : "";
            return `
                <details class="terminal-history-entry">
                    <summary>
                        <div class="terminal-history-entry-top">
                            <div class="terminal-history-command">${this.escapeHtml(task.command || "(empty command)")}</div>
                            <div class="terminal-history-badge ${badgeClass}">${this.escapeHtml(status || "unknown")}</div>
                        </div>
                        <div class="terminal-history-meta">
                            Created: ${this.escapeHtml(this.formatTimestamp(task.created_at))}<br>
                            Updated: ${this.escapeHtml(this.formatTimestamp(latestResult?.received_at || task.completed_at || task.sent_at || task.created_at))}
                        </div>
                    </summary>
                    <div class="terminal-history-output">
                        <div class="terminal-history-output-label">Latest stored output</div>
                        <pre>${this.escapeHtml(output)}</pre>
                    </div>
                </details>
            `;
        }).join("");
        this.updateFullHistoryControls();
    },

    updateFullHistoryControls() {
        const loadMoreBtn = document.getElementById("loadMoreHistoryBtn");
        const refreshBtn = document.getElementById("refreshFullHistoryBtn");
        const openBtn = document.getElementById("openFullHistoryBtn");
        if (loadMoreBtn) {
            loadMoreBtn.disabled = this.archiveLoading || !this.archiveHasMore;
            loadMoreBtn.textContent = this.archiveLoading ? "Loading..." : "Load more";
        }
        if (refreshBtn) {
            refreshBtn.disabled = this.archiveLoading;
        }
        if (openBtn) {
            openBtn.disabled = !this.allowReconnect && !this.agent;
        }
    },

    startHistoryRefresh() {
        this.stopHistoryRefresh();
        this.historyTimer = window.setInterval(() => this.loadRecentHistory(), 15000);
        window.addEventListener("beforeunload", () => this.stopHistoryRefresh(), { once: true });
    },

    stopHistoryRefresh() {
        if (this.historyTimer) {
            window.clearInterval(this.historyTimer);
            this.historyTimer = null;
        }
    },

    connect() {
        if (!this.allowReconnect) {
            return;
        }

        const token = Auth.getToken();
        if (!token) {
            Auth.logout();
            return;
        }

        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = `${protocol}//${window.location.host}/api/v1/ws/terminal/${encodeURIComponent(this.agentId)}?token=${encodeURIComponent(token)}`;

        this.archiveOpenSessions("The connection was reset. Previous live sessions were archived locally");
        this.updateStatus("connecting");
        this.activeSessionId = null;
        this.sessionCreatePending = false;
        this.updateSessionMeta();
        this.updateSessionControls();
        this.appendMessage("Connecting to the agent terminal...", "info");
        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
            this.updateStatus("connected");
            this.clearReconnect();
            this.setInputEnabled(false);
            this.updateSessionControls();
            this.appendMessage("Connected. Waiting for terminal mode and shell session details...", "info");
        };

        this.ws.onmessage = (event) => {
            this.handleMessage(event.data);
        };

        this.ws.onclose = () => {
            this.updateStatus("disconnected");
            this.activeSessionId = null;
            this.sessionCreatePending = false;
            this.setInputEnabled(false);
            this.updateSessionMeta();
            this.updateSessionControls();
            if (this.allowReconnect) {
                this.appendMessage("Connection lost. Trying to reconnect...", "error");
                this.scheduleReconnect();
            }
        };

        this.ws.onerror = (error) => {
            console.error("WebSocket error:", error);
        };
    },

    scheduleReconnect() {
        if (this.reconnectTimer) {
            return;
        }
        this.reconnectTimer = window.setTimeout(() => {
            this.reconnectTimer = null;
            this.connect();
        }, 5000);
    },

    clearReconnect() {
        if (!this.reconnectTimer) {
            return;
        }
        window.clearTimeout(this.reconnectTimer);
        this.reconnectTimer = null;
    },

    handleMessage(data) {
        try {
            const message = JSON.parse(data);
            switch (message.type) {
                case "connected":
                    this.appendMessage(message.data, "info", message.timestamp);
                    if (message.agent) {
                        this.agent = message.agent;
                        this.updateAgentInfo(message.agent);
                    }
                    this.setInputEnabled(false);
                    this.updateSessionMeta();
                    this.updateSessionControls();
                    break;
                case "task_created":
                    {
                        const context = this.pendingTaskContexts.shift() || {
                            command: message.command,
                            sessionId: this.getCommandTargetSessionId(),
                        };
                        this.pendingTasks.set(message.task_id, {
                            command: message.command || context.command,
                            sessionId: context.sessionId,
                        });
                        this.appendMessage(
                            `Queued command: ${message.command}`,
                            "info",
                            message.timestamp,
                            context.sessionId,
                        );
                    }
                    break;
                case "mode":
                    this.terminalMode = message.mode || "legacy";
                    this.appendMessage(
                        message.data || `Terminal mode: ${this.terminalMode}`,
                        this.terminalMode === "interactive" ? "info" : "error",
                    );
                    if (this.terminalMode === "legacy") {
                        this.activeSessionId = null;
                        if (!this.selectedSessionId) {
                            this.selectedSessionId = null;
                        }
                        this.setInputEnabled(true);
                    } else {
                        this.setInputEnabled(this.canSendToSelectedSession());
                    }
                    this.updateSessionMeta();
                    this.updateSessionControls();
                    this.updateInputHint();
                    break;
                case "sessions_state":
                    this.syncSessionsState(message.sessions || [], message.active_session_id || null);
                    break;
                case "session_started":
                    this.sessionCreatePending = false;
                    this.ensureSession(message.session_id, {
                        status: "open",
                        createdAt: message.created_at || message.timestamp,
                        lastEventAt: message.timestamp,
                    });
                    if (!this.selectedSessionId) {
                        this.selectedSessionId = message.session_id;
                    }
                    this.appendMessage(message.data || "Interactive shell started", "info", message.timestamp, message.session_id);
                    this.renderSessionList();
                    this.renderActiveTranscript();
                    this.updateSessionMeta();
                    this.updateSessionControls();
                    this.setInputEnabled(this.canSendToSelectedSession() || this.terminalMode === "legacy");
                    break;
                case "session_activated":
                    if (message.session_id && this.sessions[message.session_id]) {
                        this.activeSessionId = message.session_id;
                        if (!this.selectedSessionId || this.isOpenSessionSelected()) {
                            this.selectedSessionId = message.session_id;
                        }
                        this.sessions[message.session_id].unreadCount = 0;
                    }
                    this.appendMessage(message.data || "Switched to a different shell session", "info", message.timestamp, message.session_id || null);
                    this.renderSessionList();
                    this.renderActiveTranscript();
                    this.updateSessionMeta();
                    this.updateSessionControls();
                    this.setInputEnabled(this.canSendToSelectedSession() || this.terminalMode === "legacy");
                    break;
                case "session_closed":
                    if (message.session_id) {
                        this.ensureSession(message.session_id, {
                            status: "closed",
                            lastEventAt: message.timestamp,
                        });
                        this.appendMessage(
                            message.data || "Shell session closed",
                            "error",
                            message.timestamp,
                            message.session_id,
                        );
                        if (this.activeSessionId === message.session_id) {
                            this.activeSessionId = null;
                        }
                    } else {
                        this.appendMessage(message.data || "Shell session closed", "error", message.timestamp);
                    }
                    this.sessionCreatePending = false;
                    this.renderSessionList();
                    this.renderActiveTranscript();
                    this.updateSessionMeta();
                    this.updateSessionControls();
                    this.updateInputHint();
                    this.setInputEnabled(this.canSendToSelectedSession() || this.terminalMode === "legacy");
                    break;
                case "session_start_failed":
                    this.sessionCreatePending = false;
                    this.updateSessionMeta();
                    this.updateSessionControls();
                    this.appendMessage(message.data || "The interactive shell could not be started", "error", message.timestamp);
                    break;
                case "output":
                    this.renderTaskOutput(message);
                    break;
                case "stream_output":
                    this.appendOutput(
                        message.data,
                        "normal",
                        message.timestamp,
                        false,
                        message.session_id || this.activeSessionId || null,
                    );
                    break;
                case "error":
                    this.appendMessage(message.data, "error", message.timestamp);
                    if (/offline/i.test(String(message.data))) {
                        this.allowReconnect = false;
                        this.clearReconnect();
                        this.setInputEnabled(false);
                        this.updateStatus("offline");
                    }
                    this.updateSessionControls();
                    this.updateSessionMeta();
                    break;
                case "ping":
                    this.send({ type: "pong" });
                    break;
                default:
            this.appendMessage("An unknown message was received from the server", "error");
            }
        } catch (error) {
            console.error("Failed to parse terminal message:", error);
        }
    },

    renderTaskOutput(message) {
        const pending = this.pendingTasks.get(message.task_id) || { command: "", sessionId: null };
        const command = pending.command || "";
        const sessionId = pending.sessionId || null;
        if (command.startsWith("!screenshot") && !message.is_error) {
            this.renderScreenshotArtifact(command, message);
            this.appendMessage("Screenshot captured. The preview was updated in the artifact panel", "info", message.timestamp, sessionId);
            this.pendingTasks.delete(message.task_id);
            this.loadRecentHistory();
            return;
        }
        if (command.startsWith("!download ") && !message.is_error) {
            this.renderDownloadArtifact(command, message);
            this.appendMessage("The file was pulled from the agent. Use the artifact panel to save it locally", "info", message.timestamp, sessionId);
            this.pendingTasks.delete(message.task_id);
            this.loadRecentHistory();
            return;
        }
        if (command.startsWith("!info") && !message.is_error) {
            this.handleInfoPayload(message.data);
        }
        if (command.startsWith("!upload ") && !message.is_error) {
            this.renderTextArtifact("Upload complete", message.data, "success");
        }
        this.appendOutput(message.data, message.is_error ? "error" : "normal", message.timestamp, false, sessionId);
        this.pendingTasks.delete(message.task_id);
        this.loadRecentHistory();
        if (this.isFullHistoryOpen()) {
            this.loadFullHistoryPage(true);
        }
    },

    sendCommand(command) {
        const trimmed = String(command || "").trim();
        if (!trimmed) {
            return;
        }
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            this.appendMessage("The agent terminal is not connected", "error");
            return;
        }

        const sessionId = this.getCommandTargetSessionId();
        const expectsTaskResult = this.terminalMode === "legacy" || this.isTerminalControlCommand(trimmed);
        if (this.terminalMode === "interactive" && !this.isTerminalControlCommand(trimmed) && !sessionId) {
            this.appendMessage("Select an open shell session, or create a new one, before sending interactive input", "error");
            return;
        }

        this.rememberCommand(trimmed);
        this.appendCommand(trimmed, sessionId);
        if (expectsTaskResult) {
            this.pendingTaskContexts.push({ command: trimmed, sessionId });
        }
        this.send({ type: "command", data: trimmed, session_id: sessionId || undefined });
    },

    requestNewSession() {
        if (this.terminalMode !== "interactive") {
            this.appendMessage("This agent build does not support interactive shell sessions", "error");
            return;
        }
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            this.appendMessage("The connection is not ready", "error");
            return;
        }

        this.sessionCreatePending = true;
        this.updateSessionMeta();
        this.updateSessionControls();
        this.appendMessage("Requesting a new interactive shell...", "info");
        this.send({ type: "new_session" });
    },

    requestSwitchSession(sessionId) {
        const session = this.sessions[sessionId];
        if (!session) {
            return;
        }

        this.selectedSessionId = sessionId;
        session.unreadCount = 0;
        this.renderSessionList();
        this.renderActiveTranscript();
        this.updateSessionMeta();
        this.updateSessionControls();
        this.setInputEnabled(this.canSendToSelectedSession() || this.terminalMode === "legacy");

        if (session.status === "open" && this.ws && this.ws.readyState === WebSocket.OPEN && sessionId !== this.activeSessionId) {
            this.send({ type: "switch_session", session_id: sessionId });
        }
    },

    requestCloseSession(sessionId) {
        const session = this.sessions[sessionId];
        if (!session || session.status !== "open") {
            return;
        }
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            this.appendMessage("The connection is not ready", "error");
            return;
        }
        this.send({ type: "close_session", session_id: sessionId });
    },

    syncSessionsState(sessionPayload, remoteActiveSessionId) {
        const openIds = new Set();
        for (const rawSession of sessionPayload) {
            const sessionId = String(rawSession.session_id || "").trim();
            if (!sessionId) {
                continue;
            }
            openIds.add(sessionId);
            this.ensureSession(sessionId, {
                status: "open",
                createdAt: rawSession.created_at,
                lastEventAt: rawSession.last_event_at,
            });
        }

        for (const sessionId of this.sessionOrder) {
            const session = this.sessions[sessionId];
            if (session && session.status === "open" && !openIds.has(sessionId)) {
                session.status = "closed";
            }
        }

        this.activeSessionId = remoteActiveSessionId && this.sessions[remoteActiveSessionId]
            ? remoteActiveSessionId
            : null;

        if (this.activeSessionId && this.sessions[this.activeSessionId]) {
            this.sessions[this.activeSessionId].unreadCount = 0;
        }

        const selected = this.selectedSessionId ? this.sessions[this.selectedSessionId] : null;
        if (!selected) {
            this.selectedSessionId = this.activeSessionId || this.findLatestSessionId();
        } else if (selected.status === "open" && this.activeSessionId) {
            this.selectedSessionId = this.activeSessionId;
        }

        this.renderSessionList();
        this.renderActiveTranscript();
        this.updateSessionMeta();
        this.updateSessionControls();
        this.setInputEnabled(this.canSendToSelectedSession() || this.terminalMode === "legacy");
    },

    ensureSession(sessionId, meta = {}) {
        if (!sessionId) {
            return null;
        }
        if (!this.sessions[sessionId]) {
            this.sessions[sessionId] = {
                id: sessionId,
                ordinal: this.sessionOrder.length + 1,
                status: meta.status || "open",
                createdAt: meta.createdAt || meta.created_at || new Date().toISOString(),
                lastEventAt: meta.lastEventAt || meta.last_event_at || new Date().toISOString(),
                transcriptLines: [],
                unreadCount: 0,
            };
            this.sessionOrder.push(sessionId);
        }

        const session = this.sessions[sessionId];
        if (meta.status) {
            session.status = meta.status;
        }
        if (meta.createdAt || meta.created_at) {
            session.createdAt = meta.createdAt || meta.created_at;
        }
        if (meta.lastEventAt || meta.last_event_at) {
            session.lastEventAt = meta.lastEventAt || meta.last_event_at;
        }
        return session;
    },

    archiveOpenSessions(reason) {
        const nowIso = new Date().toISOString();
        for (const sessionId of this.sessionOrder) {
            const session = this.sessions[sessionId];
            if (!session || session.status !== "open") {
                continue;
            }
            session.status = "closed";
            session.lastEventAt = nowIso;
            session.transcriptLines.push(
                this.createTranscriptEntry(reason, "error", nowIso, true),
            );
        }
        this.activeSessionId = null;
        this.sessionCreatePending = false;
        this.renderSessionList();
        this.renderActiveTranscript();
    },

    findLatestSessionId() {
        if (this.sessionOrder.length === 0) {
            return null;
        }
        return this.sessionOrder[this.sessionOrder.length - 1];
    },

    isOpenSessionSelected() {
        return Boolean(
            this.selectedSessionId
            && this.sessions[this.selectedSessionId]
            && this.sessions[this.selectedSessionId].status === "open",
        );
    },

    canSendToSelectedSession() {
        if (this.terminalMode !== "interactive") {
            return false;
        }
        if (!this.allowReconnect || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
            return false;
        }
        if (!this.selectedSessionId || !this.activeSessionId) {
            return false;
        }
        const selected = this.sessions[this.selectedSessionId];
        if (!selected || selected.status !== "open") {
            return false;
        }
        return this.selectedSessionId === this.activeSessionId;
    },

    getCommandTargetSessionId() {
        if (this.terminalMode !== "interactive") {
            return null;
        }
        if (!this.canSendToSelectedSession()) {
            return null;
        }
        return this.selectedSessionId;
    },

    createTranscriptEntry(text, kind = "normal", timestamp = null, plain = false) {
        return {
            text: `[${this.formatTimestamp(timestamp)}] ${String(text ?? "")}`,
            kind,
            plain,
        };
    },

    pushTranscriptEntry(entry, sessionId = null) {
        if (sessionId) {
            const session = this.ensureSession(sessionId);
            session.transcriptLines.push(entry);
            session.lastEventAt = new Date().toISOString();
            if (this.selectedSessionId !== sessionId) {
                session.unreadCount += 1;
            } else {
                session.unreadCount = 0;
            }
            this.renderSessionList();
            if (this.selectedSessionId === sessionId) {
                this.renderActiveTranscript();
            }
            return;
        }

        this.generalTranscriptLines.push(entry);
        if (!this.selectedSessionId) {
            this.renderActiveTranscript();
        }
    },

    appendCommand(command, sessionId = null) {
        const entry = {
            text: `[${this.formatTimestamp()}] $ ${command}`,
            kind: "command",
            plain: false,
        };
        this.pushTranscriptEntry(entry, sessionId);
    },

    appendMessage(message, kind = "info", timestamp = null, sessionId = null) {
        this.appendOutput(message, kind === "error" ? "error" : "normal", timestamp, true, sessionId);
    },

    appendOutput(text, kind = "normal", timestamp = null, plain = false, sessionId = null) {
        const entry = this.createTranscriptEntry(text, kind, timestamp, plain);
        this.pushTranscriptEntry(entry, sessionId);
    },

    renderSessionList() {
        const container = document.getElementById("sessionList");
        const summary = document.getElementById("sessionSummary");
        if (!container || !summary) {
            return;
        }

        const total = this.sessionOrder.length;
        const openCount = this.sessionOrder.filter((sessionId) => this.sessions[sessionId]?.status === "open").length;
        const closedCount = Math.max(total - openCount, 0);
        summary.textContent = total === 0
            ? "No sessions yet"
            : `${openCount} open • ${closedCount} archived`;

        if (total === 0) {
            container.innerHTML = `
                <div class="history-item">
                    <div class="history-command">No shell sessions yet</div>
                    <div class="history-meta">New shells will appear here as soon as the interactive transport is ready</div>
                </div>
            `;
            return;
        }

        const rows = [...this.sessionOrder].reverse().map((sessionId) => {
            const session = this.sessions[sessionId];
            const isSelected = sessionId === this.selectedSessionId;
            const isActive = sessionId === this.activeSessionId;
            const isClosed = session.status === "closed";
            const isSwitching = isSelected && !isClosed && this.activeSessionId && sessionId !== this.activeSessionId;
            const stateLabel = isClosed ? "closed" : isActive ? "active" : "open";
            const unreadBadge = session.unreadCount > 0
                ? `<span class="session-pill unread">${session.unreadCount} new</span>`
                : "";
            const closeButton = isClosed
                ? `<span></span>`
                : `<button class="btn btn-small btn-danger session-close" data-close-session="${this.escapeHtml(sessionId)}">×</button>`;

            return `
                <div class="session-row">
                    <button
                        type="button"
                        class="session-select ${isSelected ? "active" : ""} ${isClosed ? "closed" : ""} ${isSwitching ? "switching" : ""}"
                        data-session-id="${this.escapeHtml(sessionId)}"
                    >
                        <div class="session-title-row">
                            <div class="session-title">Session ${session.ordinal} · ${this.escapeHtml(this.shortSessionId(sessionId))}</div>
                            <div style="display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end;">
                                <span class="session-pill ${isClosed ? "closed" : "open"}">${this.escapeHtml(stateLabel)}</span>
                                ${unreadBadge}
                            </div>
                        </div>
                        <div class="session-meta">
                            Created: ${this.escapeHtml(this.formatTimestamp(session.createdAt))}<br>
                            Last activity: ${this.escapeHtml(this.formatTimestamp(session.lastEventAt))}<br>
                            Transcript lines: ${session.transcriptLines.length}
                        </div>
                    </button>
                    ${closeButton}
                </div>
            `;
        });
        container.innerHTML = rows.join("");
    },

    getVisibleTranscriptEntries() {
        if (this.selectedSessionId && this.sessions[this.selectedSessionId]) {
            return this.sessions[this.selectedSessionId].transcriptLines;
        }
        return this.generalTranscriptLines;
    },

    renderActiveTranscript() {
        const output = document.getElementById("terminalOutput");
        if (!output) {
            return;
        }
        output.innerHTML = "";

        const entries = this.getVisibleTranscriptEntries();
        if (!Array.isArray(entries) || entries.length === 0) {
            const placeholder = document.createElement("div");
            placeholder.className = "text-dim";
            if (this.selectedSessionId && this.sessions[this.selectedSessionId]) {
                placeholder.textContent = this.sessions[this.selectedSessionId].status === "closed"
                    ? "[This archived session has no transcript yet.]"
                    : "[Waiting for session output...]";
            } else {
                placeholder.textContent = "[Connecting to agent...]";
            }
            output.appendChild(placeholder);
            output.scrollTop = output.scrollHeight;
            return;
        }

        for (const entry of entries) {
            const line = document.createElement("div");
            if (entry.kind === "command") {
                line.className = "command-cmd";
            } else {
                line.className = entry.kind === "error" ? "command-output error" : "command-output";
                if (entry.plain) {
                    line.classList.add("text-dim");
                }
            }
            line.textContent = entry.text;
            output.appendChild(line);
        }
        output.scrollTop = output.scrollHeight;
    },

    copyTranscript() {
        const payload = this.getVisibleTranscriptEntries().map((entry) => entry.text).join("\n").trim();
        if (!payload) {
            this.appendMessage("The transcript is empty", "error");
            return;
        }
        navigator.clipboard.writeText(payload)
            .then(() => this.appendMessage("Transcript copied to the clipboard", "info"))
            .catch((error) => {
                console.error("Copy transcript failed:", error);
                this.appendMessage("The transcript could not be copied", "error");
            });
    },

    saveTranscript() {
        const payload = this.getVisibleTranscriptEntries().map((entry) => entry.text).join("\n").trim();
        if (!payload) {
            this.appendMessage("The transcript is empty", "error");
            return;
        }

        const sessionSuffix = this.selectedSessionId ? `session-${this.shortSessionId(this.selectedSessionId)}` : "general";
        const blob = new Blob([payload], { type: "text/plain;charset=utf-8" });
        const url = window.URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `agent-${this.agentId}-${sessionSuffix}-${Date.now()}.txt`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.setTimeout(() => window.URL.revokeObjectURL(url), 1000);
        this.appendMessage("Transcript saved", "info");
    },

    clearTranscriptView() {
        if (this.selectedSessionId && this.sessions[this.selectedSessionId]) {
            this.sessions[this.selectedSessionId].transcriptLines = [];
            this.renderActiveTranscript();
            this.appendMessage("The selected session transcript was cleared", "info", null, this.selectedSessionId);
            this.renderSessionList();
            return;
        }

        this.generalTranscriptLines = [];
        this.renderActiveTranscript();
        this.appendMessage("The terminal view was cleared", "info");
    },

    updateAgentInfo(agent) {
        document.getElementById("agentHostname").textContent = agent.hostname || "-";
        document.getElementById("agentUser").textContent = agent.username || "-";
        document.getElementById("agentOS").textContent = agent.os_info || "-";
        document.getElementById("agentIP").textContent = agent.internal_ip || "N/A";
        document.getElementById("agentCallback").textContent = `${agent.callback_interval || "-"}s`;
        const firstSeen = this.parseServerDate(agent.first_seen);
        document.getElementById("agentUptime").textContent = this.formatUptime(firstSeen);
    },

    setInputEnabled(enabled) {
        const input = document.getElementById("commandInput");
        const sendBtn = document.getElementById("sendBtn");
        input.disabled = !enabled;
        sendBtn.disabled = !enabled;
        this.updateInputHint();
    },

    updateSessionControls() {
        const newSessionBtn = document.getElementById("newSessionBtn");
        const clearHistoryBtn = document.getElementById("clearCommandHistoryBtn");
        const wsConnected = Boolean(this.ws && this.ws.readyState === WebSocket.OPEN);
        const openSessions = this.sessionOrder.filter((sessionId) => this.sessions[sessionId]?.status === "open").length;

        if (newSessionBtn) {
            newSessionBtn.disabled = !wsConnected
                || this.terminalMode !== "interactive"
                || !this.allowReconnect
                || this.sessionCreatePending
                || openSessions >= 6;
        }
        if (clearHistoryBtn) {
            clearHistoryBtn.disabled = this.commandHistory.length === 0;
        }
        this.updateFullHistoryControls();
    },

    updateInputHint() {
        const input = document.getElementById("commandInput");
        if (!input) {
            return;
        }
        if (input.disabled) {
            if (!this.allowReconnect) {
                input.placeholder = "This agent is offline";
                return;
            }
            if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
                input.placeholder = "Waiting for the terminal to reconnect...";
                return;
            }
            if (this.terminalMode === "interactive") {
                if (this.sessionCreatePending) {
                    input.placeholder = "Creating a new shell session...";
                    return;
                }
                if (!this.selectedSessionId) {
                    input.placeholder = "Create a shell session to get started";
                    return;
                }
                const session = this.sessions[this.selectedSessionId];
                if (!session) {
                    input.placeholder = "Select a shell session";
                    return;
                }
                if (session.status === "closed") {
                    input.placeholder = "You are viewing an archived session. Select an open shell to send commands";
                    return;
                }
                if (this.selectedSessionId !== this.activeSessionId) {
                    input.placeholder = "Switching to the selected shell session...";
                    return;
                }
                input.placeholder = "Waiting for the active shell session...";
                return;
            }
            if (this.terminalMode === "legacy") {
                input.placeholder = "Waiting for terminal availability...";
                return;
            }
            input.placeholder = "Waiting for terminal state...";
            return;
        }
        if (this.terminalMode === "interactive") {
            input.placeholder = `Session ${this.shortSessionId(this.selectedSessionId)} · enter a command or password response...`;
            return;
        }
        if (this.terminalMode === "legacy") {
            input.placeholder = "Enter a one-shot command. Interactive stdin is not available on this agent build";
            return;
        }
        input.placeholder = "Enter a command...";
    },

    updateSessionMeta() {
        const modeLabel = document.getElementById("terminalModeLabel");
        const sessionLabel = document.getElementById("sessionIdLabel");
        if (!modeLabel || !sessionLabel) {
            return;
        }

        const openCount = this.sessionOrder.filter((sessionId) => this.sessions[sessionId]?.status === "open").length;
        if (!this.allowReconnect) {
            modeLabel.textContent = "Mode: unavailable";
            sessionLabel.textContent = "Sessions: offline";
            return;
        }
        if (this.terminalMode === "interactive") {
            const viewing = this.selectedSessionId && this.sessions[this.selectedSessionId]
                ? `${this.shortSessionId(this.selectedSessionId)}${this.sessions[this.selectedSessionId].status === "closed" ? " (archived)" : ""}`
                : "none";
            const active = this.activeSessionId ? this.shortSessionId(this.activeSessionId) : "none";
            modeLabel.textContent = `Mode: interactive shell • ${openCount} open`;
            sessionLabel.textContent = `Active: ${active} • Viewing: ${viewing}`;
            return;
        }
        if (this.terminalMode === "legacy") {
            modeLabel.textContent = "Mode: legacy one-shot";
            sessionLabel.textContent = "Sessions: n/a";
            return;
        }
        modeLabel.textContent = "Mode: detecting...";
        sessionLabel.textContent = "Sessions: pending";
    },

    updateStatus(status) {
        const statusText = document.getElementById("connectionStatus");
        const statusIndicator = document.getElementById("statusIndicator");
        const pill = document.getElementById("heroStatusPill");

        if (status === "connected") {
            statusText.textContent = "Connected";
            statusIndicator.textContent = "🟢";
            pill.className = "terminal-status-pill";
            return;
        }
        if (status === "connecting") {
            statusText.textContent = "Connecting...";
            statusIndicator.textContent = "🟡";
            pill.className = "terminal-status-pill waiting";
            return;
        }
        if (status === "offline") {
            statusText.textContent = "Offline";
            statusIndicator.textContent = "⚫";
            pill.className = "terminal-status-pill disconnected";
            return;
        }
        statusText.textContent = "Disconnected";
        statusIndicator.textContent = "🔴";
        pill.className = "terminal-status-pill disconnected";
    },

    setupEventListeners() {
        const input = document.getElementById("commandInput");
        document.getElementById("sendBtn").addEventListener("click", () => {
            this.sendCommand(input.value);
            input.value = "";
            this.historyDraft = "";
        });
        input.addEventListener("keypress", (event) => {
            if (event.key === "Enter") {
                this.sendCommand(input.value);
                input.value = "";
                this.historyDraft = "";
            }
        });
        input.addEventListener("keydown", (event) => {
            if (event.key === "ArrowUp") {
                event.preventDefault();
                if (this.commandHistory.length === 0) {
                    return;
                }
                if (this.historyIndex >= this.commandHistory.length) {
                    this.historyDraft = input.value;
                }
                if (this.historyIndex > 0) {
                    this.historyIndex -= 1;
                } else {
                    this.historyIndex = 0;
                }
                input.value = this.commandHistory[this.historyIndex] || "";
            } else if (event.key === "ArrowDown") {
                event.preventDefault();
                if (this.commandHistory.length === 0) {
                    return;
                }
                if (this.historyIndex < this.commandHistory.length - 1) {
                    this.historyIndex += 1;
                    input.value = this.commandHistory[this.historyIndex];
                } else {
                    this.historyIndex = this.commandHistory.length;
                    input.value = this.historyDraft;
                }
            } else if (event.ctrlKey && event.key.toLowerCase() === "c") {
                event.preventDefault();
                if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                    this.send({
                        type: "signal",
                        data: "interrupt",
                        session_id: this.getCommandTargetSessionId() || undefined,
                    });
                    this.appendMessage("^C", "info", null, this.getCommandTargetSessionId());
                }
            }
        });
        input.addEventListener("input", () => {
            if (this.historyIndex >= this.commandHistory.length) {
                this.historyDraft = input.value;
            }
        });

        document.getElementById("newSessionBtn").addEventListener("click", () => this.requestNewSession());
        document.getElementById("openFullHistoryBtn").addEventListener("click", () => this.openFullHistory(false));
        document.getElementById("refreshInfoBtn").addEventListener("click", () => this.sendCommand("!info"));
        document.getElementById("screenshotBtn").addEventListener("click", () => this.sendCommand("!screenshot"));
        document.getElementById("persistBtn").addEventListener("click", () => this.sendCommand("!persist"));
        document.getElementById("copyTranscriptBtn").addEventListener("click", () => this.copyTranscript());
        document.getElementById("saveTranscriptBtn").addEventListener("click", () => this.saveTranscript());
        document.getElementById("clearTranscriptBtn").addEventListener("click", () => this.clearTranscriptView());
        document.getElementById("killAgentBtn").addEventListener("click", () => {
            if (window.confirm("Kill this agent?")) {
                this.sendCommand("!kill");
            }
        });
        document.getElementById("downloadFileBtn").addEventListener("click", () => this.queueDownload());
        document.getElementById("uploadFileBtn").addEventListener("click", () => this.uploadSelectedFile());
        document.getElementById("sleepBtn").addEventListener("click", () => this.queueSleepUpdate());
        document.getElementById("clearCommandHistoryBtn").addEventListener("click", () => this.clearCommandHistory());
        document.getElementById("refreshHistoryBtn").addEventListener("click", () => this.loadRecentHistory());
        document.getElementById("refreshFullHistoryBtn").addEventListener("click", () => this.loadFullHistoryPage(true));
        document.getElementById("closeFullHistoryBtn").addEventListener("click", () => this.closeFullHistory());
        document.getElementById("loadMoreHistoryBtn").addEventListener("click", () => this.loadFullHistoryPage(false));
        document.getElementById("terminalPresetGrid").addEventListener("click", (event) => {
            const preset = event.target.closest("[data-terminal-command]");
            if (!preset) {
                return;
            }
            const command = preset.getAttribute("data-terminal-command") || "";
            this.sendCommand(command);
        });
        document.getElementById("commandHistoryList").addEventListener("click", (event) => {
            const button = event.target.closest("[data-history-index]");
            if (!button) {
                return;
            }
            const index = Number(button.getAttribute("data-history-index"));
            if (!Number.isInteger(index) || !this.commandHistory[index]) {
                return;
            }
            this.loadHistoryEntry(this.commandHistory[index]);
        });
        document.getElementById("sessionList").addEventListener("click", (event) => {
            const closeButton = event.target.closest("[data-close-session]");
            if (closeButton) {
                this.requestCloseSession(closeButton.getAttribute("data-close-session"));
                return;
            }
            const sessionButton = event.target.closest("[data-session-id]");
            if (!sessionButton) {
                return;
            }
            this.requestSwitchSession(sessionButton.getAttribute("data-session-id"));
        });
        document.getElementById("terminalHistoryModal").addEventListener("click", (event) => {
            if (event.target.id === "terminalHistoryModal") {
                this.closeFullHistory();
            }
        });

        input.focus();
        document.addEventListener("click", (event) => {
            if (!event.target.closest("button") && !event.target.closest("input[type='file']")) {
                input.focus();
            }
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && this.isFullHistoryOpen()) {
                this.closeFullHistory();
            }
        });
        this.updateSessionMeta();
        this.updateSessionControls();
        this.updateFullHistoryControls();
    },

    getCommandHistoryStorageKey() {
        return `noxveil_terminal_history:${this.agentId}`;
    },

    loadStoredCommandHistory() {
        try {
            const raw = window.localStorage.getItem(this.getCommandHistoryStorageKey());
            const parsed = raw ? JSON.parse(raw) : [];
            this.commandHistory = Array.isArray(parsed)
                ? parsed.filter((item) => typeof item === "string" && item.trim()).slice(-80)
                : [];
        } catch (error) {
            console.warn("Failed to load command history:", error);
            this.commandHistory = [];
        }
        this.historyIndex = this.commandHistory.length;
        this.historyDraft = "";
        this.renderCommandHistory();
    },

    persistCommandHistory() {
        try {
            if (this.commandHistory.length === 0) {
                window.localStorage.removeItem(this.getCommandHistoryStorageKey());
            } else {
                window.localStorage.setItem(
                    this.getCommandHistoryStorageKey(),
                    JSON.stringify(this.commandHistory.slice(-80)),
                );
            }
        } catch (error) {
            console.warn("Failed to persist command history:", error);
        }
        this.updateSessionControls();
    },

    rememberCommand(command) {
        const trimmed = String(command || "").trim();
        if (!trimmed) {
            return;
        }
        if (this.commandHistory[this.commandHistory.length - 1] !== trimmed) {
            this.commandHistory.push(trimmed);
        }
        if (this.commandHistory.length > 80) {
            this.commandHistory = this.commandHistory.slice(-80);
        }
        this.historyIndex = this.commandHistory.length;
        this.historyDraft = "";
        this.persistCommandHistory();
        this.renderCommandHistory();
    },

    renderCommandHistory() {
        const container = document.getElementById("commandHistoryList");
        if (!container) {
            return;
        }
        if (!Array.isArray(this.commandHistory) || this.commandHistory.length === 0) {
            container.innerHTML = `
                <div class="history-item">
                    <div class="history-command">No commands yet</div>
                    <div class="history-meta">Commands sent from this workspace will be saved locally for this agent</div>
                </div>
            `;
            this.updateSessionControls();
            return;
        }

        const recentCommands = this.commandHistory
            .map((command, index) => ({ command, index }))
            .slice(-12)
            .reverse();

        container.innerHTML = recentCommands.map(({ command, index }) => `
            <button
                type="button"
                class="history-item"
                data-history-index="${index}"
                title="Load command back into the terminal prompt"
            >
                <div class="history-command">${this.escapeHtml(command)}</div>
                <div class="history-meta">History slot #${index + 1} • click to reuse</div>
            </button>
        `).join("");
        this.updateSessionControls();
    },

    clearCommandHistory() {
        this.commandHistory = [];
        this.historyIndex = 0;
        this.historyDraft = "";
        this.persistCommandHistory();
        this.renderCommandHistory();
        this.appendMessage("Command history was cleared for this agent workspace", "info");
    },

    loadHistoryEntry(command) {
        const input = document.getElementById("commandInput");
        input.value = String(command || "");
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);
        this.historyIndex = this.commandHistory.length;
        this.historyDraft = input.value;
    },

    async uploadSelectedFile() {
        const fileInput = document.getElementById("uploadFileInput");
        const pathInput = document.getElementById("uploadPath");
        const file = fileInput.files[0];
        const remotePath = pathInput.value.trim();

        if (!file) {
            this.appendMessage("Select a local file first", "error");
            return;
        }
        if (!remotePath) {
            this.appendMessage("Enter a remote upload path first", "error");
            return;
        }

        const arrayBuffer = await file.arrayBuffer();
        const base64Data = this.arrayBufferToBase64(arrayBuffer);
        const payload = JSON.stringify({ path: remotePath, data: base64Data });
        this.sendCommand(`!upload ${payload}`);
        this.renderTextArtifact("Upload pending", `Sending ${file.name} to ${remotePath}`, "info");
        fileInput.value = "";
    },

    queueDownload() {
        const path = document.getElementById("downloadPath").value.trim();
        if (!path) {
            this.appendMessage("Enter a remote file path to download", "error");
            return;
        }
        this.sendCommand(`!download ${path}`);
    },

    queueSleepUpdate() {
        const value = document.getElementById("sleepInterval").value.trim();
        if (!value) {
            this.appendMessage("Enter a callback interval in seconds", "error");
            return;
        }
        this.sendCommand(`!sleep ${value}`);
    },

    handleInfoPayload(rawText) {
        try {
            const info = JSON.parse(rawText);
            if (info && typeof info === "object") {
                this.agent = {
                    ...this.agent,
                    hostname: info.hostname || this.agent?.hostname,
                    username: info.username || this.agent?.username,
                    os_info: info.os_info || this.agent?.os_info,
                    internal_ip: info.internal_ip || this.agent?.internal_ip,
                };
                this.updateAgentInfo(this.agent);
            }
        } catch (error) {
            console.warn("Unable to parse !info payload:", error);
        }
    },

    renderScreenshotArtifact(command, message) {
        const panel = document.getElementById("artifactPanel");
        const src = `data:image/png;base64,${message.data}`;
        panel.innerHTML = `
            <div class="artifact-meta">Latest artifact from <code>${this.escapeHtml(command)}</code></div>
            <img class="artifact-preview-image" src="${src}" alt="Agent screenshot preview">
            <div class="artifact-actions">
                <a class="btn btn-small btn-info" href="${src}" download="agent-screenshot-${Date.now()}.png">Download PNG</a>
                <a class="btn btn-small" href="${src}" target="_blank" rel="noopener">Open full size</a>
            </div>
        `;
    },

    renderDownloadArtifact(command, message) {
        const filePath = command.slice("!download ".length).trim() || "download.bin";
        const blob = this.base64ToBlob(message.data);
        const downloadUrl = window.URL.createObjectURL(blob);
        const filename = filePath.split("/").pop() || "download.bin";
        const panel = document.getElementById("artifactPanel");
        panel.innerHTML = `
            <div class="artifact-meta">Remote file ready: <strong>${this.escapeHtml(filePath)}</strong></div>
            <div class="artifact-actions">
                <a class="btn btn-small btn-primary" href="${downloadUrl}" download="${this.escapeHtml(filename)}">Save local copy</a>
            </div>
            <div class="artifact-meta">This file stays available until you reload the page or fetch another artifact</div>
        `;
    },

    renderTextArtifact(title, text, kind = "info") {
        const panel = document.getElementById("artifactPanel");
        const accent = kind === "success" ? "var(--success)" : kind === "error" ? "var(--danger)" : "var(--info)";
        panel.innerHTML = `
            <div class="artifact-meta" style="color:${accent};">${this.escapeHtml(title)}</div>
            <pre class="artifact-meta" style="white-space:pre-wrap;">${this.escapeHtml(text)}</pre>
        `;
    },

    send(payload) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(payload));
        }
    },

    isTerminalControlCommand(command) {
        const trimmed = String(command || "").trim();
        if (!trimmed) {
            return false;
        }
        if (["!screenshot", "!persist", "!sleep", "!info", "!kill"].includes(trimmed)) {
            return true;
        }
        return ["!download ", "!upload ", "!sleep "].some((prefix) => trimmed.startsWith(prefix));
    },

    arrayBufferToBase64(buffer) {
        let binary = "";
        const bytes = new Uint8Array(buffer);
        const chunkSize = 0x8000;
        for (let index = 0; index < bytes.length; index += chunkSize) {
            binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
        }
        return window.btoa(binary);
    },

    base64ToBlob(base64Data) {
        const binary = window.atob(base64Data);
        const bytes = new Uint8Array(binary.length);
        for (let index = 0; index < binary.length; index += 1) {
            bytes[index] = binary.charCodeAt(index);
        }
        return new Blob([bytes]);
    },

    parseServerDate(dateString) {
        if (!dateString || typeof dateString !== "string") {
            return null;
        }
        const hasTimezone = /(?:Z|[+-]\d{2}:\d{2})$/.test(dateString);
        const normalized = hasTimezone ? dateString : `${dateString}Z`;
        const parsed = new Date(normalized);
        if (Number.isNaN(parsed.getTime())) {
            return null;
        }
        return parsed;
    },

    formatUptime(startDate) {
        if (!(startDate instanceof Date) || Number.isNaN(startDate.getTime())) {
            return "unknown";
        }
        const seconds = Math.floor((Date.now() - startDate.getTime()) / 1000);
        if (!Number.isFinite(seconds) || seconds < 0) {
            return "unknown";
        }
        if (seconds < 60) {
            return `${seconds}s`;
        }
        if (seconds < 3600) {
            return `${Math.floor(seconds / 60)}m`;
        }
        if (seconds < 86400) {
            return `${Math.floor(seconds / 3600)}h`;
        }
        return `${Math.floor(seconds / 86400)}d`;
    },

    formatTimestamp(timestamp = null) {
        return new Date(timestamp || Date.now()).toLocaleTimeString();
    },

    shortSessionId(sessionId) {
        return String(sessionId || "").slice(0, 8) || "none";
    },

    escapeHtml(text) {
        const div = document.createElement("div");
        div.textContent = text ?? "";
        return div.innerHTML;
    },
};

window.Terminal = Terminal;
