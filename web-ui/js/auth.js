const Auth = {
    TOKEN_KEY: "c2_token",
    REFRESH_KEY: "c2_refresh_token",

    isAuthenticated() {
        return Boolean(this.getToken());
    },

    getToken() {
        return localStorage.getItem(this.TOKEN_KEY);
    },

    getRefreshToken() {
        return localStorage.getItem(this.REFRESH_KEY);
    },

    setToken(token) {
        localStorage.setItem(this.TOKEN_KEY, token);
    },

    setRefreshToken(token) {
        localStorage.setItem(this.REFRESH_KEY, token);
    },

    logout() {
        localStorage.removeItem(this.TOKEN_KEY);
        localStorage.removeItem(this.REFRESH_KEY);
        window.location.href = "/login";
    },

    getHeaders(extraHeaders = {}) {
        const headers = { ...extraHeaders };
        const token = this.getToken();
        if (token) {
            headers.Authorization = `Bearer ${token}`;
        }
        if (!headers["Content-Type"]) {
            headers["Content-Type"] = "application/json";
        }
        return headers;
    },

    async apiCall(url, options = {}) {
        const response = await fetch(url, {
            ...options,
            headers: this.getHeaders(options.headers),
        });

        if (response.status !== 401) {
            return response;
        }

        const refreshed = await this.refreshToken();
        if (!refreshed) {
            this.logout();
            return response;
        }

        return fetch(url, {
            ...options,
            headers: this.getHeaders(options.headers),
        });
    },

    async refreshToken() {
        const refreshToken = this.getRefreshToken();
        if (!refreshToken) {
            return false;
        }

        try {
            const response = await fetch("/api/v1/auth/refresh", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ refresh_token: refreshToken }),
            });

            if (!response.ok) {
                return false;
            }

            const data = await response.json();
            if (!data.access_token) {
                return false;
            }

            this.setToken(data.access_token);
            return true;
        } catch (error) {
            console.error("Token refresh failed:", error);
            return false;
        }
    },
};

window.Auth = Auth;
