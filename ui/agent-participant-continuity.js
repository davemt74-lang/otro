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
    const syncPanel=document.createElement('div');
    syncPanel.innerHTML='<p data-participant-sync-status role="status"></p><div data-participant-sync-link hidden><label>Cloud participant<select data-participant-cloud aria-label="Cloud participant to link"></select></label><button type="button" data-participant-link>Link selected Cloud participant</button><button type="button" data-participant-separate>Sync as a separate participant</button></div><div data-participant-sync-conflict hidden><p>Saved local details:</p><pre data-participant-local-version></pre><p>Cloud details:</p><pre data-participant-cloud-version></pre><button type="button" data-participant-use-cloud>Use Cloud details</button><button type="button" data-participant-keep-local>Keep local details</button></div><button type="button" data-participant-remove>Remove participant</button>';
    panel.append(syncPanel);let syncToken=0;
    async function syncStatus(){
      const id=q('[data-participant-select]').value,token=++syncToken;
      q('[data-participant-sync-link]').hidden=true;q('[data-participant-sync-conflict]').hidden=true;
      q('[data-participant-remove]').hidden=!id||!adapter.removeParticipant;
      if(!adapter.participantSyncStatus){q('[data-participant-sync-status]').textContent='';return;}
      try{
        const state=await adapter.participantSyncStatus(id);if(token!==syncToken||q('[data-participant-select]').value!==id)return;
        q('[data-participant-sync-status]').textContent=state.text;
        if(id&&state.canLink){
          const options=(state.options||[]).map(p=>{const o=document.createElement('option');o.value=p.key;o.textContent=p.label;return o;});
          q('[data-participant-cloud]').replaceChildren(...options);q('[data-participant-link]').disabled=!options.length;q('[data-participant-sync-link]').hidden=false;
        }
        if(id&&state.conflict){
          const describe=p=>!p?'Unavailable':p.deleted?'Participant removed':p.name+'\n'+(p.note||'(No conversation note)');
          q('[data-participant-local-version]').textContent=describe(state.local);q('[data-participant-cloud-version]').textContent=describe(state.remote);
          q('[data-participant-sync-conflict]').hidden=false;q('[data-participant-keep-local]').disabled=state.protected===true;
          q('[data-participant-keep-local]').textContent=state.remote?.deleted?'Keep local as a separate participant':'Keep local details';
        }
      }catch(error){if(token===syncToken)q('[data-participant-sync-status]').textContent=String(error.message||'Participant sync unavailable.');}
    }
    async function syncAction(action){
      const id=q('[data-participant-select]').value;if(!id)return;
      const buttons=Array.from(syncPanel.querySelectorAll('button'));buttons.forEach(b=>b.disabled=true);
      try{await action(id);clearSelection();window.VP3VoiceGuard?.clear();await window.VP3AgentEyes?.refreshProfiles?.();await refresh();}
      catch(error){q('[data-participant-save-status]').textContent=String(error.message||'Participant update unavailable.');}
      finally{buttons.forEach(b=>b.disabled=false);await syncStatus();}
    }
    q('[data-participant-link]').addEventListener('click',()=>void syncAction(id=>adapter.linkParticipant(id,q('[data-participant-cloud]').value)));
    q('[data-participant-separate]').addEventListener('click',()=>void syncAction(id=>adapter.linkParticipant(id,'')));
    q('[data-participant-use-cloud]').addEventListener('click',()=>void syncAction(id=>adapter.resolveParticipantSync(id,'cloud')));
    q('[data-participant-keep-local]').addEventListener('click',()=>void syncAction(id=>adapter.resolveParticipantSync(id,'local')));
    q('[data-participant-remove]').addEventListener('click',()=>{
      if(confirm('Remove this participant and their saved conversation note? A linked deletion also syncs to Cloud or HomeServer.'))void syncAction(id=>adapter.removeParticipant(id));
    });
    window.addEventListener('vp3:participant-sync-status',()=>{if(panel.open)void syncStatus();});
    window.addEventListener('vp3:participant-memory-changed',()=>{clearSelection();if(panel.open&&!editing)void refresh();});
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
          ? value.rows.slice(0, 8).map(safePerson).sort((a,b)=>a.track_id.localeCompare(b.track_id))
          : [],
        active = value?.active === true;
      if(!active)rows.length=0;
      const next = JSON.stringify([active, rows]);
      presence = { active, rows, at: Date.now() };
      if (next !== signature) {
        signature = next;
        revision++;
        speaker = null;
        announceSpeaker();
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
      if (!person.participant_id) reason = 'unassigned';
      else if (rejected.has(person.participant_id)) reason = 'corrected';
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
        const visible=presence.rows.filter(r=>r.participant_id===person.participant_id);
        speaker = accepted ? { ...person, track_id:visible.length===1?visible[0].track_id:'', at, expires_at:at+3000, revision } : null;
        announceSpeaker();
        say(
          accepted
            ? `Speaking with ${person.display_name || 'a participant'} · ${reason === 'camera-and-voice' ? 'camera and voice agree' : 'local voice match'} · provisional.`
            : reason === 'corrected'
              ? 'Previous match dismissed. Introduce yourself or choose the correct participant.'
              : reason === 'stale-camera'
                ? 'Camera evidence is stale. No participant assigned.'
                : 'Camera and voice disagree. Who is speaking? No participant assigned.',
        );
        if (accepted) signal('speaker', {...person,matched_at:at,presence_key:signature});
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
    function activeSpeaker(){
      if(!speaker||speaker.revision!==revision||document.hidden||Date.now()-presence.at>3500||!presence.active)return null;
      return window.VP3EyesCore.speakerTrack(presence.rows,speaker)?{...speaker}:null;
    }
    function announceSpeaker(){window.dispatchEvent(new CustomEvent('vp3:active-speaker',{detail:activeSpeaker()}));}
    function clearSpeaker(remote=false){speaker=null;announceSpeaker();if(!remote)signal('speaker',null);}
    function receiveSpeaker(value) {
      // A delayed popup match belongs only to the exact camera snapshot it saw.
      // Recheck the parent's correction and camera evidence, without echoing it.
      const at=Date.now(),matched=value?.matched_at;
      const valid=value&&value.presence_key===signature&&Number.isFinite(matched)&&matched<=at&&at-matched<3000&&assessVoice(value,{notify:false}).accepted;
      const person=safePerson(value),visible=presence.rows.filter(r=>r.participant_id===person.participant_id);
      speaker = valid?{...person,track_id:visible.length===1?visible[0].track_id:'',at:matched,expires_at:matched+3000,revision}:null;
      announceSpeaker();
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
      announceSpeaker();
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
      void syncStatus();
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
        const rows = await (adapter.loadConversationParticipants?.()||adapter.loadEyesProfiles());
        if (token !== loadToken) return;
        const options = rows.map((p) => {
          const o = document.createElement('option');
          o.value = p.id;
          o.textContent = p.name+(rows.filter(r=>r.name===p.name).length>1?' · #'+p.id:'');
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
        announceSpeaker();
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
        announceSpeaker();
        say(selectedParticipant
          ? `${selectedParticipant.display_name} selected for typed Chat.`
          : 'No participant assigned to this conversation turn.');
      }
    }, 1000);
    const changeConversation=()=>{clearSelection();revision++;clearSpeaker();window.VP3VoiceGuard?.clear();};
    window.addEventListener('stonefellow:chat-context-changing',changeConversation);
    window.addEventListener('homeserver:chat-context-changing',changeConversation);
    window.addEventListener('vp3:participant-forgotten', (event) => {
      clearSelection();
      revision++;
      speaker = null;
      announceSpeaker();
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
        announceSpeaker();
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
        announceSpeaker();
      },
      { once: true },
    );
    window.VP3ParticipantContinuity = {
      updatePresence,
      assessVoice,
      receiveSpeaker,
      activeSpeaker,
      clearSpeaker,
      rejectCurrent,
      selectedContext,
      setSelection,
      clearSelection,
      snapshot: () => ({ active: presence.active, rows: presence.rows }),
      invalidate: () => {
        revision++;
        speaker = null;
        announceSpeaker();
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
