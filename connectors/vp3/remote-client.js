(function attachVP3HomeServerRemoteConnector(globalScope) {
  'use strict';

  const DEFAULT_PERMISSIONS = [
    'agent.chat',
    'contacts.read',
    'knowledge.search',
    'memory.read',
    'memory.write',
    'notifications.read',
    'tools.execute',
  ];
  const PUBLIC_OPERATIONS = new Set(['capabilities', 'pair.request', 'pair.status']);

  class VP3HomeServerRemoteConnector {
    constructor(options = {}) {
      if (!options.relayBaseUrl) throw new Error('relayBaseUrl is required for remote HomeServer access.');
      this.relayBaseUrl = String(options.relayBaseUrl).replace(/\/+$/, '');
      this.relayToken = options.relayToken || null;
      this.homeServerToken = options.homeServerToken || null;
      this.appKey = options.appKey || 'vp3';
      this.appName = options.appName || 'VP3';
    }

    async _relay(path, options = {}, requireRelayToken = true) {
      const headers = {...(options.headers || {})};
      if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
      if (requireRelayToken) {
        if (!this.relayToken) throw new Error('This HomeServer has not been claimed through the relay.');
        headers.Authorization = `Bearer ${this.relayToken}`;
      }
      const response = await fetch(`${this.relayBaseUrl}${path}`, {
        mode:'cors',
        credentials:'omit',
        cache:'no-store',
        ...options,
        headers,
      });
      let data = {};
      try { data = await response.json(); } catch (_) {}
      if (!response.ok) {
        const detail = data.detail || data?.payload?.detail || `HomeServer relay request failed (${response.status})`;
        const error = new Error(detail);
        error.status = response.status;
        error.data = data;
        throw error;
      }
      return data;
    }

    setRelayToken(token) { this.relayToken = token || null; return this; }
    setHomeServerToken(token) { this.homeServerToken = token || null; return this; }
    clearRelayToken() { this.relayToken = null; }
    clearHomeServerToken() { this.homeServerToken = null; }

    health() { return this._relay('/health', {}, false); }

    async claimHomeServer(claimCode) {
      if (!claimCode) throw new Error('HomeServer relay claim code is required.');
      const result = await this._relay('/v1/claim', {
        method:'POST',
        body:JSON.stringify({claim_code:String(claimCode)}),
      }, false);
      if (!result.relay_token || !result.device_id) throw new Error('Relay returned an invalid claim response.');
      this.relayToken = result.relay_token;
      return result;
    }

    session() { return this._relay('/v1/session'); }

    async rotateRelaySession() {
      const result = await this._relay('/v1/session/rotate', {method:'POST'});
      if (!result.relay_token) throw new Error('Relay did not return a replacement session token.');
      this.relayToken = result.relay_token;
      return result;
    }

    async request(operation, payload = {}) {
      const op = String(operation || '');
      if (!op) throw new Error('Remote HomeServer operation is required.');
      const isPublic = PUBLIC_OPERATIONS.has(op);
      if (!isPublic && !this.homeServerToken) {
        throw new Error('HomeServer is not paired for protected remote capabilities.');
      }
      const result = await this._relay('/v1/request', {
        method:'POST',
        body:JSON.stringify({
          operation:op,
          payload:payload || {},
          bearer_token:isPublic ? null : this.homeServerToken,
        }),
      });
      return result.payload || {};
    }

    capabilities() { return this.request('capabilities'); }

    async requestPairing(permissions = DEFAULT_PERMISSIONS) {
      const pairing = await this.request('pair.request', {
        app_key:this.appKey,
        app_name:this.appName,
        permissions,
      });
      if (!pairing.request_id || !pairing.claim_token || !pairing.code) {
        throw new Error('HomeServer returned an unsupported remote pairing response.');
      }
      return pairing;
    }

    pairingStatus(pairing) {
      if (!pairing?.request_id || !pairing?.claim_token) {
        throw new Error('Pairing request_id and claim_token are required.');
      }
      return this.request('pair.status', {
        request_id:pairing.request_id,
        claim_token:pairing.claim_token,
      });
    }

    async waitForApproval(pairing, options = {}) {
      const intervalMs = Math.max(500, Number(options.intervalMs || 1500));
      const timeoutMs = Math.max(intervalMs, Number(options.timeoutMs || 10 * 60 * 1000));
      const started = Date.now();
      while (Date.now() - started < timeoutMs) {
        const status = await this.pairingStatus(pairing);
        if (typeof options.onStatus === 'function') options.onStatus(status);
        if (status.ready) {
          this.homeServerToken = pairing.claim_token;
          return status;
        }
        if (status.status === 'expired') throw new Error('HomeServer pairing request expired. Start pairing again.');
        if (status.status === 'denied') throw new Error('HomeServer pairing request was denied.');
        await new Promise(resolve => setTimeout(resolve, intervalMs));
      }
      throw new Error('HomeServer remote pairing approval timed out.');
    }

    async pair(permissions = DEFAULT_PERMISSIONS, options = {}) {
      const pairing = await this.requestPairing(permissions);
      if (typeof options.onCode === 'function') options.onCode(pairing.code, pairing);
      const status = await this.waitForApproval(pairing, options);
      return {pairing, status, homeServerToken:this.homeServerToken};
    }

    chat(message, conversationId = null) {
      return this.request('chat', {message, conversation_id:conversationId});
    }
    conversations(limit = 50) {
      return this.request('conversations.list', {limit:Math.max(1, Math.min(100, Number(limit || 50)))});
    }
    conversation(conversationId) {
      if (!conversationId) throw new Error('conversationId is required.');
      return this.request('conversation.get', {conversation_id:Number(conversationId)});
    }
    contacts(query = '') { return this.request('contacts.search', {query:String(query)}); }
    searchKnowledge(query = '') { return this.request('knowledge.search', {query:String(query)}); }
    memory() { return this.request('memory.read'); }
    writeMemory(content, options = {}) {
      return this.request('memory.write', {
        content,
        memory_key:options.memoryKey || null,
        importance:options.importance ?? 0.5,
        agent_id:options.agentId || null,
      });
    }
    tools() { return this.request('tools.list'); }
    skills() { return this.request('skills.list'); }
    executeTool(toolKey, args = {}) {
      if (!toolKey) throw new Error('toolKey is required.');
      return this.request('tool.execute', {tool_key:toolKey, arguments:args});
    }
    actionRequest(requestId) {
      if (!requestId) throw new Error('requestId is required.');
      return this.request('action.status', {request_id:requestId});
    }
  }

  VP3HomeServerRemoteConnector.DEFAULT_PERMISSIONS = [...DEFAULT_PERMISSIONS];
  globalScope.VP3HomeServerRemoteConnector = VP3HomeServerRemoteConnector;
  if (typeof module !== 'undefined' && module.exports) module.exports = VP3HomeServerRemoteConnector;
})(typeof window !== 'undefined' ? window : globalThis);
