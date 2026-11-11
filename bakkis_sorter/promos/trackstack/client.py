"""Trackstack tRPC client constants and JavaScript injection scripts."""

from __future__ import annotations

import json
from typing import Any

TRACKSTACK_APP_URL = "https://my.trackstack.app"
TRACKSTACK_DEFAULT_API_URL = "https://rtfnuvwjkyxxu565jflhvebofe0ucesi.lambda-url.eu-west-1.on.aws"
TRACKSTACK_STREAM_BASE_URL = "https://stream.trackstack-tech.app"
TRACKSTACK_STATIC_BASE_URL = "https://static.trackstack-tech.app"

# JavaScript hook injected into the Trackstack WKWebView to observe auth tokens, workspaceId, inboxId, and userId
_INTERCEPTOR_JS = """
(function() {
    if (!window.__bakkis_ts) {
        window.__bakkis_ts = {
            apiBaseUrl: null,
            workspaceId: null,
            inboxId: null,
            userId: null,
            headers: {},
            installed: true
        };
        const origFetch = window.fetch;
        window.fetch = async function(input, init) {
            try {
                const url = typeof input === 'string' ? input : (input && input.url ? input.url : '');
                if (url.includes('.lambda-url.') && url.includes('.on.aws')) {
                    const u = new URL(url);
                    window.__bakkis_ts.apiBaseUrl = u.origin;
                    const hdrs = (init && init.headers) || (input && input.headers) || {};
                    const copyHeader = (k, v) => {
                        if (!k || !v) return;
                        const kl = String(k).toLowerCase();
                        if (kl === 'authorization' || kl === 'x-workspace-id') {
                            window.__bakkis_ts.headers[kl] = String(v);
                        }
                        if (kl === 'x-workspace-id') {
                            window.__bakkis_ts.workspaceId = String(v);
                        }
                    };
                    if (typeof hdrs.forEach === 'function') {
                        hdrs.forEach((v, k) => copyHeader(k, v));
                    } else if (Array.isArray(hdrs)) {
                        for (const pair of hdrs) {
                            if (Array.isArray(pair) && pair.length >= 2) copyHeader(pair[0], pair[1]);
                        }
                    } else if (hdrs && typeof hdrs === 'object') {
                        for (const k of Object.keys(hdrs)) copyHeader(k, hdrs[k]);
                    }
                    const inpParam = u.searchParams.get('input');
                    if (inpParam) {
                        try {
                            const parsedInp = JSON.parse(inpParam);
                            if (parsedInp && typeof parsedInp === 'object') {
                                if (parsedInp.inboxId) window.__bakkis_ts.inboxId = parsedInp.inboxId;
                                if (parsedInp.workspaceId) window.__bakkis_ts.workspaceId = parsedInp.workspaceId;
                            }
                        } catch (_) {}
                    }
                }
            } catch (_) {}
            const resp = await origFetch.apply(this, arguments);
            try {
                const url = typeof input === 'string' ? input : (input && input.url ? input.url : '');
                if (url.includes('/inbox.getInbox') && !url.includes('Stacks') && !url.includes('Count')) {
                    resp.clone().json().then(d => {
                        const inbox = d && d.result && d.result.data;
                        if (inbox && inbox.id) window.__bakkis_ts.inboxId = inbox.id;
                    }).catch(() => {});
                } else if (url.includes('/auth.getUser')) {
                    resp.clone().json().then(d => {
                        const usr = d && d.result && d.result.data;
                        if (usr && usr.id) window.__bakkis_ts.userId = usr.id;
                        if (usr && usr.activeWorkspaceId && !window.__bakkis_ts.workspaceId) {
                            window.__bakkis_ts.workspaceId = usr.activeWorkspaceId;
                        }
                    }).catch(() => {});
                } else if (url.includes('/workspace.getWorkspace')) {
                    resp.clone().json().then(d => {
                        const ws = d && d.result && d.result.data;
                        if (ws && ws.id) window.__bakkis_ts.workspaceId = ws.id;
                        if (ws && Array.isArray(ws.inboxes) && ws.inboxes.length > 0 && ws.inboxes[0].id) {
                            window.__bakkis_ts.inboxId = ws.inboxes[0].id;
                        }
                    }).catch(() => {});
                }
            } catch (_) {}
            return resp;
        };
    }

    // 1. Check document.cookie for trackstack_id_token (Trackstack's primary JWT storage)
    let cookieToken = null;
    try {
        const m = /(?:^|;\\s*)trackstack_id_token=([^;]+)/.exec(document.cookie || '');
        if (m && m[1]) cookieToken = decodeURIComponent(m[1]).trim();
    } catch (_) {}

    // 2. Also inspect localStorage for activeWorkspaceId and Cognito tokens
    let cognitoToken = null;
    let lsWorkspaceId = null;
    try {
        lsWorkspaceId = localStorage.getItem('activeWorkspaceId') || null;
        for (let i = 0; i < localStorage.length; i++) {
            const k = localStorage.key(i) || '';
            const v = localStorage.getItem(k) || '';
            if (k.includes('CognitoIdentityServiceProvider') && (k.endsWith('.idToken') || k.endsWith('.accessToken'))) {
                if (!cognitoToken || k.endsWith('.idToken')) cognitoToken = v;
            }
        }
    } catch (_) {}

    const activeToken = cookieToken || cognitoToken || (window.__bakkis_ts.headers && window.__bakkis_ts.headers['authorization']) || null;
    if (activeToken && window.__bakkis_ts.headers) {
        window.__bakkis_ts.headers['authorization'] = activeToken;
    }
    const resolvedWsId = window.__bakkis_ts.workspaceId || lsWorkspaceId || null;
    if (resolvedWsId) {
        window.__bakkis_ts.workspaceId = resolvedWsId;
    }

    const href = String(location.href || '');
    const isLogin = href.includes('/auth/') || href.includes('/login') || href.includes('/signin') || href.includes('/onboarding');
    const hasAuth = Boolean(activeToken);
    return {
        href,
        isLogin,
        hasAuth,
        ready: !isLogin && href.includes('trackstack.app') && hasAuth,
        apiBaseUrl: window.__bakkis_ts.apiBaseUrl,
        workspaceId: resolvedWsId,
        inboxId: window.__bakkis_ts.inboxId,
        userId: window.__bakkis_ts.userId,
        hasCapturedHeaders: Boolean(activeToken),
        cognitoTokenFound: Boolean(cookieToken || cognitoToken)
    };
})()
"""


def build_trpc_request_js(
    api_base_url: str,
    workspace_id: str | None,
    method: str,
    procedure: str,
    payload: Any,
) -> str:
    """Build the executable JavaScript for a tRPC call inside the WKWebView."""
    return f"""
    (function() {{
        const ts = window.__bakkis_ts || {{}};
        const baseUrl = ts.apiBaseUrl || {json.dumps(api_base_url)};
        let wsId = '';
        try {{ wsId = localStorage.getItem('activeWorkspaceId') || ''; }} catch (_) {{}}
        if (!wsId) wsId = ts.workspaceId || {json.dumps(workspace_id or "")};
        const method = {json.dumps(method.upper())};
        const proc = {json.dumps(procedure.lstrip("/"))};
        const payload = {json.dumps(payload)};

        let authHeader = '';
        try {{
            const m = /(?:^|;\\s*)trackstack_id_token=([^;]+)/.exec(document.cookie || '');
            if (m && m[1]) authHeader = decodeURIComponent(m[1]).trim();
        }} catch (_) {{}}
        if (!authHeader) {{
            authHeader = (ts.headers && ts.headers['authorization']) || '';
        }}
        if (!authHeader) {{
            try {{
                for (let i = 0; i < localStorage.length; i++) {{
                    const k = localStorage.key(i) || '';
                    if (k.includes('CognitoIdentityServiceProvider') && k.endsWith('.idToken')) {{
                        authHeader = localStorage.getItem(k) || '';
                        break;
                    }}
                }}
            }} catch (_) {{}}
        }}

        let url = baseUrl.replace(/\\/+$/, '') + '/' + proc;
        if (method === 'GET' && payload !== null && payload !== undefined) {{
            url += '?input=' + encodeURIComponent(JSON.stringify(payload));
        }}

        const xhr = new XMLHttpRequest();
        xhr.open(method, url, false);
        xhr.setRequestHeader('Accept', '*/*');
        xhr.setRequestHeader('Content-Type', 'application/json');
        if (wsId) xhr.setRequestHeader('x-workspace-id', wsId);
        if (authHeader) xhr.setRequestHeader('Authorization', authHeader);

        if (method === 'POST') {{
            xhr.send(payload !== null && payload !== undefined ? JSON.stringify(payload) : '{{}}');
        }} else {{
            xhr.send(null);
        }}

        let parsed = null;
        try {{ parsed = JSON.parse(xhr.responseText); }} catch (_) {{ parsed = {{ raw: xhr.responseText }}; }}
        const retryAfter = xhr.getResponseHeader('Retry-After');
        return {{
            status: xhr.status,
            retryAfter: retryAfter ? parseFloat(retryAfter) : null,
            data: parsed
        }};
    }})()
    """
