/* Current conversational attribution. Recognition never grants account authority. */
(() => {
  'use strict';
  function boot() {
    const adapter = window.VP3_ORB_ADAPTER;
    if (!adapter || window.VP3ParticipantContinuity) return;
    let presence = { active: false, rows: [], at: 0 },
      speaker = null,
      revision = 0,
      signature = '',
      mounted = false,
      loadToken = 0,
      editing = false,
      lastClarification = 0,
      selectedParticipant = null;
    const rejected = new Map(),
      embed =
        new URLSearchParams(location.search).get('orb_embed') === '1' &&
        parent !== window;
    const session = crypto.randomUUID();
    const panel = document.createElement('details');
    panel.className = 'vp3-participant-continuity';
    panel.innerHTML =
      '<summary>Conversation participant</summary><p data-participant-status role="status">No participant assigned to this conversation turn.</p><button type="button" data-participant-reject>That’s not me</button><label>Participant<select data-participant-select aria-label="Conversation participant"></select></label><label>Correct name<input type="text" data-participant-name maxlength="120"></label><label>Remember for conversations<textarea data-participant-note maxlength="1000" rows="3" placeholder="Only details you choose to remember"></textarea></label><p>This note is saved only when you select Save. Recognition stays provisional.</p><div class="vp3-eyes-actions"><button type="button" data-participant-use>Use in typed Chat</button><button type="button" data-participant-save>Save participant details</button><button type="button" data-participant-clear>Forget conversation note</button></div><p data-participant-save-status role="status"></p>';
    const q = (s) => panel.querySelector(s),
      say = (t) => {
        q('[data-participant-status]').textContent = t;
      };
    function signal(kind, value) {
      window.VP3AgentOrb?.participantSignal?.(kind, value);
    }
    function safePerson(value) {
      const id = String(value?.participant_id || '').slice(0, 64),
        name = String(value?.display_name || '').slice(0, 120);
      return {
        participant_id: id,
        display_name: name,
        track_id: String(value?.track_id || '').slice(0, 64),
        identity_source: String(value?.identity_source || '').slice(0, 50),
      };
    }
    function updatePresence(value, remote = false) {
      const rows = Array.isArray(value?.rows)
          ? value.rows.slice(0, 8).map(safePerson)
          : [],
        active = value?.active === true;
      const next = JSON.stringify([active, rows]);
      presence = { active, rows, at: Date.now() };
      if (next !== signature) {
        signature = next;
        revision++;
        speaker = null;
        clearSelection();
        window.VP3VoiceGuard?.clear();
        say(
          active
            ? rows.length > 1
              ? 'Several participants in view. A current voice match is needed.'
              : rows[0]?.display_name
                ? `${rows[0].display_name} in view · provisional. Waiting for speech.`
                : rows.length
                  ? 'Participant in view · name unassigned.'
                  : 'No participant in view.'
            : 'Camera is off. Voice matching can still identify a speaker provisionally.',
        );
      }
      if (!remote) signal('presence', { active, rows });
    }
    function assessVoice(value, { notify = true } = {}) {
      const person = safePerson(value),
        at = Date.now();
      for (const [id, until] of rejected) if (until <= at) rejected.delete(id);
      let reason = 'voice';
      if (rejected.has(person.participant_id)) reason = 'corrected';
      else if (presence.active && at - presence.at > 3500)
        reason = 'stale-camera';
      else if (presence.active) {
        const named = presence.rows.filter((r) => r.participant_id),
          same = named.filter(
            (r) => r.participant_id === person.participant_id,
          );
        if (same.length > 1 || (named.length && same.length !== 1))
          reason = 'camera-voice-conflict';
        else if (same.length === 1) reason = 'camera-and-voice';
      }
      const accepted = ['voice', 'camera-and-voice'].includes(reason);
      if (notify) {
        speaker = accepted ? { ...person, at, revision } : null;
        say(
          accepted
            ? `Speaking with ${person.display_name || 'a participant'} · ${reason === 'camera-and-voice' ? 'camera and voice agree' : 'local voice match'} · provisional.`
            : reason === 'corrected'
              ? 'Previous match dismissed. Introduce yourself or choose the correct participant.'
              : reason === 'stale-camera'
                ? 'Camera evidence is stale. No participant assigned.'
                : 'Camera and voice disagree. Who is speaking? No participant assigned.',
        );
        if (accepted) signal('speaker', person);
        else {
          signal('speaker', null);
          if (
            reason === 'camera-voice-conflict' &&
            at - lastClarification > 30000
          ) {
            lastClarification = at;
            window.VP3AgentOrb?.bubble?.(
              'Camera and voice disagree. Who is speaking?',
            );
          }
        }
      }
      return { accepted, reason, revision };
    }
    function receiveSpeaker(value) {
      speaker = value
        ? { ...safePerson(value), at: Date.now(), revision }
        : null;
      say(
        speaker
          ? `Speaking with ${speaker.display_name} · provisional local voice match.`
          : 'No participant assigned to this conversation turn.',
      );
    }
    function selectedContext() {
      if (!selectedParticipant || Date.now() - selectedParticipant.at > 1800000)
        return null;
      const { at, ...value } = selectedParticipant;
      return value;
    }
    function setSelection(value, remote = false) {
      const previous = selectedContext();
      selectedParticipant =
        value?.source === 'manual_selection' &&
        value.authentication_authority === false
          ? {
              ...safePerson(value),
              source: 'manual_selection',
              conversation_note: String(value.conversation_note || '').slice(
                0,
                1000,
              ),
              authentication_authority: false,
              at: Date.now(),
            }
          : null;
      if (
        !remote &&
        JSON.stringify(previous) !== JSON.stringify(selectedContext())
      )
        signal('selection', selectedContext());
    }
    function clearSelection() {
      setSelection(null);
    }
    async function useParticipant() {
      const id = q('[data-participant-select]').value,
        token = ++loadToken;
      if (!id)
        return void (q('[data-participant-save-status]').textContent =
          'Choose a participant.');
      try {
        const p = await adapter.participantDetails(id);
        if (token !== loadToken || q('[data-participant-select]').value !== id)
          return;
        setSelection({
          participant_id: String(p.id),
          display_name: p.name,
          conversation_note: p.note || '',
          source: 'manual_selection',
          authentication_authority: false,
        });
        say(
          `${p.name} selected for typed Chat. Change or dismiss this selection to switch people.`,
        );
        q('[data-participant-save-status]').textContent =
          'Saved details selected for typed Chat. Unsaved edits were not used.';
      } catch (_) {
        q('[data-participant-save-status]').textContent =
          'Participant could not be selected.';
      }
    }
    function log(type, person = {}) {
      void Promise.resolve(
        adapter.recordEyes?.({
          event_id: crypto.randomUUID(),
          session_id: session,
          track_id: crypto.randomUUID(),
          event_type: type,
          participant_id: person.participant_id || '',
          display_name: person.display_name || '',
          identity_source: person.participant_id
            ? 'manual_correction'
            : 'unidentified',
          authentication_authority: false,
        }),
      ).catch(() => {});
    }
    function rejectCurrent(remote = false, id = '') {
      const current =
        speaker && Date.now() - speaker.at < 10000
          ? speaker
          : presence.rows.length === 1
            ? presence.rows[0]
            : null;
      const participantId = String(id || current?.participant_id || '');
      if (participantId) rejected.set(participantId, Date.now() + 120000);
      speaker = null;
      revision++;
      clearSelection();
      window.VP3VoiceGuard?.clear();
      window.VP3VoiceGuard?.cancelEnrollment();
      if (!remote) {
        window.VP3AgentEyes?.rejectCurrent?.();
        signal('reject', { participant_id: participantId });
        log('attribution_corrected', current || {});
      }
      say(
        'Match dismissed for this visit. Your saved profiles were kept. Introduce yourself to continue.',
      );
    }
    async function loadDetails() {
      const token = ++loadToken,
        id = q('[data-participant-select]').value;
      q('[data-participant-save-status]').textContent = '';
      if (!id) {
        q('[data-participant-name]').value = '';
        q('[data-participant-note]').value = '';
        return;
      }
      try {
        const p = await adapter.participantDetails(id);
        if (token !== loadToken || q('[data-participant-select]').value !== id)
          return;
        q('[data-participant-name]').value = p.name;
        q('[data-participant-note]').value = p.note || '';
        editing = false;
      } catch (_) {
        if (token === loadToken)
          q('[data-participant-save-status]').textContent =
            'Participant details are unavailable.';
      }
    }
    async function refresh() {
      if (editing) return;
      const token = ++loadToken,
        selected = q('[data-participant-select]').value;
      try {
        const rows = await adapter.loadEyesProfiles();
        if (token !== loadToken) return;
        const options = rows.map((p) => {
          const o = document.createElement('option');
          o.value = p.id;
          o.textContent = p.name;
          return o;
        });
        const blank = document.createElement('option');
        blank.value = '';
        blank.textContent = 'Choose participant';
        q('[data-participant-select]').replaceChildren(blank, ...options);
        if (rows.some((p) => String(p.id) === selected))
          q('[data-participant-select]').value = selected;
        await loadDetails();
      } catch (_) {
        q('[data-participant-save-status]').textContent =
          'Participant list is unavailable.';
      }
    }
    async function save(clearNote = false) {
      const id = q('[data-participant-select]').value,
        note = clearNote ? '' : q('[data-participant-note]').value.trim(),
        token = ++loadToken;
      let name = q('[data-participant-name]').value.trim();
      if (!id || !name)
        return void (q('[data-participant-save-status]').textContent =
          'Choose a participant and enter their name.');
      q('[data-participant-save]').disabled = true;
      q('[data-participant-clear]').disabled = true;
      try {
        if (clearNote) {
          name = (await adapter.participantDetails(id)).name;
          if (token !== loadToken) return;
        }
        const p = await adapter.saveParticipantDetails({
          id,
          name,
          note,
          isCurrent: () =>
            token === loadToken && q('[data-participant-select]').value === id,
        });
        if (token !== loadToken) return;
        editing = false;
        revision++;
        speaker = null;
        clearSelection();
        window.VP3VoiceGuard?.clear();
        signal('invalidate', {});
        await window.VP3AgentEyes?.refreshProfiles?.();
        log('participant_updated', {
          participant_id: id,
          display_name: p.name,
        });
        await refresh();
        q('[data-participant-save-status]').textContent = clearNote
          ? 'Conversation note forgotten.'
          : 'Participant details saved.';
      } catch (error) {
        if (token === loadToken)
          q('[data-participant-save-status]').textContent = String(
            error.message || 'Participant details could not be saved.',
          );
      } finally {
        q('[data-participant-save]').disabled = false;
        q('[data-participant-clear]').disabled = false;
      }
    }
    panel.addEventListener('toggle', () => {
      if (panel.open) void refresh();
    });
    q('[data-participant-select]').addEventListener('change', () => {
      editing = false;
      void loadDetails();
    });
    for (const s of ['[data-participant-name]', '[data-participant-note]'])
      q(s).addEventListener('input', () => (editing = true));
    q('[data-participant-use]').addEventListener(
      'click',
      () => void useParticipant(),
    );
    q('[data-participant-reject]').addEventListener('click', () =>
      rejectCurrent(),
    );
    q('[data-participant-save]').addEventListener('click', () => void save());
    q('[data-participant-clear]').addEventListener(
      'click',
      () => void save(true),
    );
    function mount() {
      if (mounted || embed) return;
      const host = document.querySelector('.vp3-eyes-content');
      if (host) {
        host.append(panel);
        mounted = true;
      }
    }
    mount();
    const timer = setInterval(() => {
      mount();
      if (selectedParticipant && Date.now() - selectedParticipant.at > 1800000) {
        clearSelection();
        say('Participant selection expired. Choose a participant for typed Chat.');
      }
      if (
        speaker &&
        (Date.now() - speaker.at > 10000 || speaker.revision !== revision)
      ) {
        speaker = null;
        say(selectedParticipant
          ? `${selectedParticipant.display_name} selected for typed Chat.`
          : 'No participant assigned to this conversation turn.');
      }
    }, 1000);
    window.addEventListener(
      'stonefellow:chat-context-changing',
      clearSelection,
    );
    window.addEventListener('homeserver:chat-context-changing', clearSelection);
    window.addEventListener('vp3:participant-forgotten', (event) => {
      clearSelection();
      revision++;
      speaker = null;
      window.VP3VoiceGuard?.clear();
      signal('invalidate', {});
      log('recognition_forgotten', {
        participant_id: String(event.detail?.id || ''),
        display_name: String(event.detail?.name || ''),
      });
    });
    window.addEventListener('vp3:participant-presence', (event) =>
      updatePresence(event.detail),
    );
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        clearSelection();
        speaker = null;
        presence = { active: false, rows: [], at: 0 };
        revision++;
        window.VP3VoiceGuard?.clear();
        signal('presence', { active: false, rows: [] });
      }
    });
    window.addEventListener(
      'pagehide',
      () => {
        clearInterval(timer);
        loadToken++;
        speaker = null;
      },
      { once: true },
    );
    window.VP3ParticipantContinuity = {
      updatePresence,
      assessVoice,
      receiveSpeaker,
      rejectCurrent,
      selectedContext,
      setSelection,
      clearSelection,
      snapshot: () => ({ active: presence.active, rows: presence.rows }),
      invalidate: () => {
        revision++;
        speaker = null;
        window.VP3VoiceGuard?.clear();
      },
      hear: (text) => {
        if (
          !/^(?:that['’]s not me|that is not me|you have the wrong person)[.!]?$/i.test(
            String(text).trim(),
          )
        )
          return false;
        rejectCurrent();
        return true;
      },
    };
  }
  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  else boot();
})();
