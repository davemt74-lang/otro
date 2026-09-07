(function attachVP3HomeServerConnector(globalScope) {
  'use strict';

  const DEFAULT_BASE_URL = 'http://127.0.0.1:4377';
  const DEFAULT_PERMISSIONS = [
    'agent.chat',
    'contacts.read',
    'knowledge.search',
    'memory.read',
    'memory.write',
    'notifications.read',
    'tasks.read',
    'tasks.write',
    'tools.execute',
  ];

  class VP3HomeServerConnector {
    constructor(options = {}) {
      this.baseUrl = String(options.baseUrl || DEFAULT_BASE_URL).replace(/\/+$/, '');
      this.appKey = options.appKey || 'vp3';
      this.appName = options.appName || 'VP3';
      this.token = options.token || null;
    }

    async _request(path, options = {}) {
      const headers = {...(options.headers || {})};
      if (options.body && !(options.body instanceof FormData) && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
      const fetchOptions = {mode:'cors', credentials:'omit', cache:'no-store', targetAddressSpace:'local', ...options, headers};
      const response = await fetch(`${this.baseUrl}${path}`, fetchOptions);
      let data = {};
      try { data = await response.json(); } catch (_) {}
      if (!response.ok) {
        const error = new Error(data.detail || `HomeServer request failed (${response.status})`);
        error.status = response.status;
        error.data = data;
        throw error;
      }
      return data;
    }

    async _authorized(path, options = {}) {
      if (!this.token) throw new Error('HomeServer is not paired. Complete pairing before using protected capabilities.');
      return this._request(path, {...options, headers:{...(options.headers || {}), Authorization:`Bearer ${this.token}`}});
    }

    setToken(token) { this.token = token || null; return this; }
    clearToken() { this.token = null; }
    health() { return this._request('/api/v1/health'); }
    capabilities() { return this._request('/api/v1/capabilities'); }

    async requestPairing(permissions = DEFAULT_PERMISSIONS) {
      const pairing = await this._request('/api/v1/pairing/request', {method:'POST', body:JSON.stringify({app_key:this.appKey, app_name:this.appName, permissions})});
      if (!pairing.request_id || !pairing.claim_token || !pairing.code) throw new Error('HomeServer returned an unsupported pairing response.');
      return pairing;
    }

    pairingStatus(pairing) {
      if (!pairing?.request_id || !pairing?.claim_token) throw new Error('Pairing request_id and claim_token are required.');
      return this._request('/api/v1/pairing/status', {method:'POST', body:JSON.stringify({request_id:pairing.request_id, claim_token:pairing.claim_token})});
    }

    async waitForApproval(pairing, options = {}) {
      const intervalMs = Math.max(500, Number(options.intervalMs || 1500));
      const timeoutMs = Math.max(intervalMs, Number(options.timeoutMs || 10 * 60 * 1000));
      const started = Date.now();
      while (Date.now() - started < timeoutMs) {
        const status = await this.pairingStatus(pairing);
        if (typeof options.onStatus === 'function') options.onStatus(status);
        if (status.ready) { this.token = pairing.claim_token; return status; }
        if (status.status === 'expired') throw new Error('HomeServer pairing request expired. Start pairing again.');
        if (status.status === 'denied') throw new Error('HomeServer pairing request was denied.');
        await new Promise(resolve => setTimeout(resolve, intervalMs));
      }
      throw new Error('HomeServer pairing approval timed out.');
    }

    async pair(permissions = DEFAULT_PERMISSIONS, options = {}) {
      const pairing = await this.requestPairing(permissions);
      if (typeof options.onCode === 'function') options.onCode(pairing.code, pairing);
      const status = await this.waitForApproval(pairing, options);
      return {pairing, status, token:this.token};
    }

    me() { return this._authorized('/api/v1/me'); }
    agent() { return this._authorized('/api/v1/agent'); }
    contacts(query = '') { return this._authorized(`/api/v1/contacts?q=${encodeURIComponent(query)}`); }
    searchKnowledge(query = '') { return this._authorized(`/api/v1/knowledge?q=${encodeURIComponent(query)}`); }
    memory() { return this._authorized('/api/v1/memory'); }
    writeMemory(content, options = {}) {
      return this._authorized('/api/v1/memory', {
        method:'POST',
        body:JSON.stringify({content, memory_key:options.memoryKey || null, importance:options.importance ?? 0.5, agent_id:options.agentId || null}),
      });
    }
    tasks(options = {}) {
      const params = new URLSearchParams();
      if (options.status) params.set('status', options.status);
      if (options.query) params.set('q', options.query);
      const suffix = params.toString() ? `?${params}` : '';
      return this._authorized(`/api/v1/tasks${suffix}`);
    }
    createTask(task) {
      return this._authorized('/api/v1/tasks', {method:'POST', body:JSON.stringify(task || {})});
    }
    updateTask(taskId, changes) {
      if (!taskId) throw new Error('taskId is required.');
      return this._authorized(`/api/v1/tasks/${encodeURIComponent(taskId)}`, {method:'PATCH', body:JSON.stringify(changes || {})});
    }
    notifications(unreadOnly = false) {
      return this._authorized(`/api/v1/notifications?unread_only=${unreadOnly ? 'true' : 'false'}`);
    }
    chat(message, conversationId = null) {
      return this._authorized('/api/v1/chat', {method:'POST', body:JSON.stringify({message, conversation_id:conversationId})});
    }
    conversations(limit = 50) {
      const safeLimit = Math.max(1, Math.min(100, Number(limit || 50)));
      return this._authorized(`/api/v1/conversations?limit=${safeLimit}`);
    }
    conversation(conversationId) {
      if (!conversationId) throw new Error('conversationId is required.');
      return this._authorized(`/api/v1/conversations/${encodeURIComponent(conversationId)}`);
    }
    tools() { return this._authorized('/api/v1/tools'); }
    skills() { return this._authorized('/api/v1/skills'); }
    executeTool(toolKey, args = {}) {
      if (!toolKey) throw new Error('toolKey is required.');
      return this._authorized(`/api/v1/tools/${encodeURIComponent(toolKey)}/execute`, {method:'POST', body:JSON.stringify({arguments:args})});
    }
    actionRequest(requestId) {
      if (!requestId) throw new Error('requestId is required.');
      return this._authorized(`/api/v1/action-requests/${encodeURIComponent(requestId)}`);
    }
  }

  VP3HomeServerConnector.DEFAULT_PERMISSIONS = [...DEFAULT_PERMISSIONS];
  VP3HomeServerConnector.DEFAULT_BASE_URL = DEFAULT_BASE_URL;
  globalScope.VP3HomeServerConnector = VP3HomeServerConnector;
  if (typeof module !== 'undefined' && module.exports) module.exports = VP3HomeServerConnector;
})(typeof window !== 'undefined' ? window : globalThis);