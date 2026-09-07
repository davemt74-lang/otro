(() => {
  'use strict';

  const byId = id => document.getElementById(id);
  const esc = (value = '') => String(value).replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));
  let editingId = null;
  let searchTimer = null;

  async function api(path, options = {}) {
    const headers = {...(options.headers || {})};
    if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
    const response = await fetch(path, {...options, headers});
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
  }

  function flash(message, error = false) {
    const node = byId('flash');
    if (!node) return;
    node.textContent = message;
    node.className = `flash show${error ? ' error' : ''}`;
    clearTimeout(flash.timer);
    flash.timer = setTimeout(() => { node.className = 'flash'; }, 3500);
  }

  function clearForm() {
    editingId = null;
    const form = byId('contactForm');
    form?.reset();
    byId('contactFormTitle').textContent = 'Add contact';
    byId('contactSubmit').textContent = 'Save contact';
    form?.classList.add('hidden');
  }

  function fillForm(contact) {
    editingId = contact.id;
    byId('contactDisplayName').value = contact.display_name || '';
    byId('contactFirstName').value = contact.first_name || '';
    byId('contactLastName').value = contact.last_name || '';
    byId('contactOrganization').value = contact.organization || '';
    byId('contactEmail').value = contact.email || '';
    byId('contactPhone').value = contact.phone || '';
    byId('contactRelationship').value = contact.relationship || '';
    byId('contactNotes').value = contact.notes || '';
    byId('contactFormTitle').textContent = 'Edit contact';
    byId('contactSubmit').textContent = 'Save changes';
    byId('contactForm').classList.remove('hidden');
    byId('contactDisplayName').focus();
  }

  async function loadContacts() {
    const query = encodeURIComponent(byId('contactsSearch')?.value || '');
    const data = await api(`/api/v1/control/contacts?q=${query}&limit=500`);
    const list = byId('contactsList');
    const count = byId('contactsCount');
    if (count) count.textContent = `${data.items.length} contact${data.items.length === 1 ? '' : 's'}`;
    if (!list) return;
    list.innerHTML = data.items.length ? data.items.map(contact => {
      const lines = [
        contact.organization ? `<span>${esc(contact.organization)}</span>` : '',
        contact.relationship ? `<span>${esc(contact.relationship)}</span>` : '',
        contact.email ? `<span>${esc(contact.email)}</span>` : '',
        contact.phone ? `<span>${esc(contact.phone)}</span>` : '',
      ].filter(Boolean).join('');
      return `<article class="panel contact-card" data-contact-id="${contact.id}"><div><h3>${esc(contact.display_name)}</h3><div class="contact-lines">${lines}</div>${contact.notes ? `<p class="contact-notes">${esc(contact.notes)}</p>` : ''}</div><div class="contact-actions"><button type="button" class="text-button" data-edit-contact="${contact.id}">Edit</button><button type="button" class="text-button danger" data-delete-contact="${contact.id}">Delete</button></div></article>`;
    }).join('') : '<div class="panel empty-state">No contacts found.</div>';
    list.dataset.contacts = JSON.stringify(data.items);
  }

  function currentContact(id) {
    const list = byId('contactsList');
    try {
      const items = JSON.parse(list?.dataset.contacts || '[]');
      return items.find(item => Number(item.id) === Number(id)) || null;
    } catch (_) { return null; }
  }

  document.addEventListener('click', async event => {
    const nav = event.target.closest('[data-view="contacts"], [data-go="contacts"]');
    if (nav) {
      if (byId('pageTitle')) byId('pageTitle').textContent = 'Contacts';
      try { await loadContacts(); } catch (err) { flash(err.message, true); }
    }

    if (event.target.id === 'showContactForm') {
      clearForm();
      byId('contactForm').classList.remove('hidden');
      byId('contactDisplayName').focus();
    }
    if (event.target.id === 'cancelContactForm') clearForm();

    const edit = event.target.closest('[data-edit-contact]');
    if (edit) {
      const contact = currentContact(edit.dataset.editContact);
      if (contact) fillForm(contact);
    }

    const remove = event.target.closest('[data-delete-contact]');
    if (remove) {
      const contact = currentContact(remove.dataset.deleteContact);
      if (!confirm(`Delete ${contact?.display_name || 'this contact'}?`)) return;
      try {
        await api(`/api/v1/control/contacts/${encodeURIComponent(remove.dataset.deleteContact)}`, {method:'DELETE'});
        if (editingId === Number(remove.dataset.deleteContact)) clearForm();
        await loadContacts();
        flash('Contact deleted.');
      } catch (err) { flash(err.message, true); }
    }
  });

  byId('contactForm')?.addEventListener('submit', async event => {
    event.preventDefault();
    const payload = {
      display_name: byId('contactDisplayName').value || null,
      first_name: byId('contactFirstName').value || null,
      last_name: byId('contactLastName').value || null,
      organization: byId('contactOrganization').value || null,
      email: byId('contactEmail').value || null,
      phone: byId('contactPhone').value || null,
      relationship: byId('contactRelationship').value || null,
      notes: byId('contactNotes').value || '',
    };
    try {
      const path = editingId ? `/api/v1/control/contacts/${editingId}` : '/api/v1/control/contacts';
      const method = editingId ? 'PUT' : 'POST';
      await api(path, {method, body: JSON.stringify(payload)});
      clearForm();
      await loadContacts();
      flash(editingId ? 'Contact updated.' : 'Contact saved.');
    } catch (err) { flash(err.message, true); }
  });

  byId('contactsSearch')?.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadContacts().catch(err => flash(err.message, true)), 180);
  });
})();
