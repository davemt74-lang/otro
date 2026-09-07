(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const state = {tasks: [], notifications: [], filter: 'open'};
  const flash = (message, type='success') => { $('flash').textContent = message || ''; $('flash').className = `flash ${message ? type : ''}`; };
  const api = async (url, options={}) => {
    const response = await fetch(url, {cache:'no-store', credentials:'same-origin', ...options, headers:{'Content-Type':'application/json', ...(options.headers||{})}});
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
  };
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const localValue = iso => {
    if (!iso) return '';
    const d = new Date(iso);
    const pad=n=>String(n).padStart(2,'0');
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };
  const isoValue = value => value ? new Date(value).toISOString() : null;
  const fmt = value => value ? new Date(value).toLocaleString() : '—';
  const isOverdue = task => task.due_at && ['pending','in_progress'].includes(task.status) && new Date(task.due_at) < new Date();
  const isToday = task => {
    if (!task.due_at) return false;
    const d = new Date(task.due_at), n = new Date();
    return d.getFullYear()===n.getFullYear() && d.getMonth()===n.getMonth() && d.getDate()===n.getDate();
  };
  const filteredTasks = () => state.tasks.filter(task => {
    if (state.filter === 'all') return true;
    if (state.filter === 'completed') return task.status === 'completed';
    if (state.filter === 'overdue') return isOverdue(task);
    if (state.filter === 'today') return isToday(task) && ['pending','in_progress'].includes(task.status);
    return ['pending','in_progress'].includes(task.status);
  });
  const renderTasks = () => {
    const items = filteredTasks();
    $('tasksList').innerHTML = items.length ? items.map(task => `
      <article class="task" data-task-id="${task.id}">
        <div class="task-row"><div><h3>${esc(task.title)}</h3><div class="muted">${esc(task.description || '')}</div></div><span class="chip ${esc(task.priority)}">${esc(task.priority)}</span></div>
        <div class="meta"><span class="chip">${esc(task.status.replace('_',' '))}</span>${task.due_at ? `<span class="chip${isOverdue(task)?' urgent':''}">Due ${esc(fmt(task.due_at))}</span>`:''}${task.remind_at ? `<span class="chip">Remind ${esc(fmt(task.remind_at))}</span>`:''}${task.recurrence !== 'none' ? `<span class="chip">${esc(task.recurrence_interval)}× ${esc(task.recurrence)}</span>`:''}${task.contact_name ? `<span class="chip">${esc(task.contact_name)}</span>`:''}</div>
        <div class="task-actions"><button class="button" data-edit="${task.id}">Edit</button>${!['completed','cancelled'].includes(task.status)?`<button class="button" data-complete="${task.id}">Complete</button>`:''}<button class="button danger" data-delete="${task.id}">Delete</button></div>
      </article>`).join('') : '<div class="panel empty">No tasks in this view.</div>';
  };
  const renderNotifications = () => {
    const visible = state.notifications.filter(n => !n.dismissed_at);
    const unread = visible.filter(n => !n.read_at).length;
    $('notificationSummary').textContent = `${unread} unread · ${visible.length} visible`;
    $('notificationsList').innerHTML = visible.length ? visible.map(n => `
      <div class="notification ${n.read_at?'':'unread'}" data-notification-id="${n.id}"><strong>${esc(n.title)}</strong><p>${esc(n.body || '')}</p><div class="meta"><span class="chip">${esc(n.level)}</span>${n.task_id ? `<span class="chip">Task #${n.task_id}</span>`:''}<span class="chip">${esc(fmt(n.created_at))}</span></div><div class="task-actions">${n.read_at?'':`<button class="button" data-read="${n.id}">Mark read</button>`}<button class="button" data-dismiss="${n.id}">Dismiss</button></div></div>`).join('') : '<div class="empty">No notifications yet.</div>';
  };
  const loadTasks = async () => { const q = $('search').value.trim(); const data = await api(`/api/v1/control/tasks?q=${encodeURIComponent(q)}`); state.tasks = data.items || []; renderTasks(); };
  const loadNotifications = async () => { const data = await api('/api/v1/control/task-notifications'); state.notifications = data.items || []; renderNotifications(); };
  const loadAll = async () => { try { await Promise.all([loadTasks(), loadNotifications()]); flash(''); } catch (e) { flash(e.message,'error'); } };
  const resetForm = () => {
    $('taskId').value=''; $('taskFormTitle').textContent='Create task or reminder'; $('title').value=''; $('description').value=''; $('priority').value='normal'; $('status').value='pending'; $('dueAt').value=''; $('remindAt').value=''; $('recurrence').value='none'; $('recurrenceInterval').value='1'; $('contactId').value=''; $('cancelEdit').classList.add('hidden');
  };
  const editTask = task => {
    $('taskId').value=task.id; $('taskFormTitle').textContent=`Edit task #${task.id}`; $('title').value=task.title||''; $('description').value=task.description||''; $('priority').value=task.priority; $('status').value=task.status; $('dueAt').value=localValue(task.due_at); $('remindAt').value=localValue(task.remind_at); $('recurrence').value=task.recurrence; $('recurrenceInterval').value=task.recurrence_interval||1; $('contactId').value=task.contact_id||''; $('cancelEdit').classList.remove('hidden'); window.scrollTo({top:0,behavior:'smooth'});
  };
  $('taskForm').addEventListener('submit', async event => {
    event.preventDefault();
    const id = $('taskId').value;
    const payload = {title:$('title').value.trim(), description:$('description').value.trim(), priority:$('priority').value, status:$('status').value, due_at:isoValue($('dueAt').value), remind_at:isoValue($('remindAt').value), recurrence:$('recurrence').value, recurrence_interval:Number($('recurrenceInterval').value||1), contact_id:$('contactId').value?Number($('contactId').value):null};
    try { await api(id ? `/api/v1/control/tasks/${id}` : '/api/v1/control/tasks', {method:id?'PATCH':'POST', body:JSON.stringify(payload)}); flash(id?'Task updated.':'Task created.'); resetForm(); await loadAll(); } catch(e){ flash(e.message,'error'); }
  });
  $('cancelEdit').addEventListener('click', resetForm);
  $('refreshTasks').addEventListener('click', loadTasks);
  $('refreshNotifications').addEventListener('click', loadNotifications);
  $('search').addEventListener('input', () => { clearTimeout(window.__taskSearch); window.__taskSearch=setTimeout(loadTasks,250); });
  document.querySelector('.tabs').addEventListener('click', event => { const button=event.target.closest('[data-filter]'); if(!button)return; state.filter=button.dataset.filter; document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x===button)); renderTasks(); });
  $('tasksList').addEventListener('click', async event => {
    const edit=event.target.closest('[data-edit]'), complete=event.target.closest('[data-complete]'), del=event.target.closest('[data-delete]');
    if(edit){ const task=state.tasks.find(x=>x.id===Number(edit.dataset.edit)); if(task) editTask(task); return; }
    if(complete){ try{ await api(`/api/v1/control/tasks/${complete.dataset.complete}`,{method:'PATCH',body:JSON.stringify({status:'completed'})}); flash('Task completed.'); await loadAll(); }catch(e){flash(e.message,'error');} return; }
    if(del){ if(!confirm('Delete this task?')) return; try{ await api(`/api/v1/control/tasks/${del.dataset.delete}`,{method:'DELETE'}); flash('Task deleted.'); await loadAll(); }catch(e){flash(e.message,'error');} }
  });
  $('notificationsList').addEventListener('click', async event => {
    const read=event.target.closest('[data-read]'), dismiss=event.target.closest('[data-dismiss]');
    try { if(read) await api(`/api/v1/control/notifications/${read.dataset.read}`,{method:'PATCH',body:JSON.stringify({read:true})}); if(dismiss) await api(`/api/v1/control/notifications/${dismiss.dataset.dismiss}`,{method:'PATCH',body:JSON.stringify({dismissed:true})}); if(read||dismiss) await loadNotifications(); } catch(e){ flash(e.message,'error'); }
  });
  loadAll();
})();