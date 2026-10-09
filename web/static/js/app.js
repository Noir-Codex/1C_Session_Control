(() => {
  const body = document.body;
  const initialBaseCount = Number(body.dataset.initialBases || 0);

  const setRasState = (ok, hasErrors, updatedAt) => {
    document.querySelectorAll('[data-ras-dot]').forEach(dot => {
      dot.className = `status-dot ${hasErrors ? 'danger' : ok ? 'success' : 'loading'}`;
    });
    document.querySelectorAll('[data-ras-label]').forEach(label => {
      label.textContent = hasErrors ? 'Ошибка RAS' : ok ? 'RAS подключен' : 'Получение данных';
    });
    document.querySelectorAll('[data-last-updated]').forEach(label => {
      label.textContent = updatedAt ? `Обновлено: ${new Date(updatedAt).toLocaleTimeString()}` : 'Ожидание первого ответа';
    });
  };

  const setBadge = (element, key, label) => {
    if (!element) return;
    element.className = `badge ${key}`;
    element.replaceChildren();
    const dot = document.createElement('span');
    dot.className = `status-dot ${key}`;
    element.append(dot, document.createTextNode(label));
  };

  const filterTable = table => {
    if (!table) return;
    const search = document.querySelector(`[data-table-search="${CSS.escape(table.id)}"]`);
    const query = search ? search.value.trim().toLocaleLowerCase() : '';
    const filters = [...document.querySelectorAll('[data-table-filter]')];
    table.querySelectorAll('tbody tr').forEach(row => {
      if (!row.dataset.searchText && !Object.keys(row.dataset).some(key => key !== 'searchText')) return;
      const matchesSearch = !query || (row.dataset.searchText || row.textContent.toLocaleLowerCase()).includes(query);
      const matchesFilters = filters.every(control => {
        const value = control.value.toLocaleLowerCase();
        return !value || (row.dataset[control.dataset.tableFilter] || '') === value;
      });
      row.hidden = !(matchesSearch && matchesFilters);
    });
  };

  document.querySelectorAll('[data-table-search]').forEach(input => {
    input.addEventListener('input', () => filterTable(document.getElementById(input.dataset.tableSearch)));
  });
  document.querySelectorAll('[data-table-filter]').forEach(select => {
    select.addEventListener('change', () => filterTable(document.querySelector('.admin-table')));
  });

  document.querySelectorAll('[data-open-dialog]').forEach(button => {
    button.addEventListener('click', () => document.getElementById(button.dataset.openDialog)?.showModal());
  });
  document.querySelectorAll('[data-close-dialog]').forEach(button => {
    button.addEventListener('click', () => button.closest('dialog')?.close());
  });
  document.querySelectorAll('dialog').forEach(dialog => {
    dialog.addEventListener('click', event => {
      if (event.target === dialog) dialog.close();
    });
  });

  document.querySelector('[data-sidebar-toggle]')?.addEventListener('click', () => body.classList.toggle('sidebar-open'));
  document.querySelector('[data-sidebar-backdrop]')?.addEventListener('click', () => body.classList.remove('sidebar-open'));

  const updateCompanyRows = companies => {
    for (const company of companies) {
      const scopes = document.querySelectorAll(`[data-company-row="${company.id}"], body[data-company-id="${company.id}"]`);
      scopes.forEach(scope => {
        scope.querySelectorAll('[data-company-users]').forEach(node => node.textContent = company.user_session_count);
        scope.querySelectorAll('[data-company-remaining]').forEach(node => node.textContent = company.remaining_sessions ?? '—');
        scope.querySelectorAll('[data-company-ignored]').forEach(node => node.textContent = company.ignored_session_count);
        scope.querySelectorAll('[data-company-usage-text]').forEach(node => node.textContent = `${company.user_session_count} / ${company.session_limit || '∞'}`);
        scope.querySelectorAll('[data-company-progress]').forEach(progress => {
          progress.max = company.session_limit || 1;
          progress.value = company.user_session_count;
          progress.className = progress.classList.contains('limit-progress') ? `limit-progress ${company.status_key}` : company.status_key;
        });
        scope.querySelectorAll('[data-company-status]').forEach(node => setBadge(node, company.status_key, company.status_label));
        const status = scope.querySelector('[data-control-status]');
        if (status) status.textContent = company.control_status?.error || company.control_status?.message || '';
      });
    }
  };

  const addTextCell = (row, value, className = '') => {
    const cell = document.createElement('td');
    if (className) cell.className = className;
    cell.textContent = value || '—';
    row.appendChild(cell);
    return cell;
  };

  const renderCompanySessions = companies => {
    const companyId = Number(body.dataset.companyId || 0);
    const target = document.querySelector('[data-company-session-body]');
    if (!companyId || !target) return;
    const company = companies.find(item => item.id === companyId);
    if (!company) return;
    target.replaceChildren();
    if (!company.sessions.length) {
      const row = document.createElement('tr');
      const cell = document.createElement('td'); cell.colSpan = 6;
      const empty = document.createElement('div'); empty.className = 'empty-state compact';
      const title = document.createElement('strong'); title.textContent = 'Активных пользовательских сеансов нет';
      const hint = document.createElement('span'); hint.textContent = 'Новые подключения появятся автоматически.';
      empty.append(title, hint); cell.appendChild(empty); row.appendChild(cell); target.appendChild(row); return;
    }
    for (const session of company.sessions) {
      const row = document.createElement('tr');
      row.dataset.searchText = `${session.user_name || ''} ${session.infobase_name || ''}`.toLocaleLowerCase();
      const user = addTextCell(row, session.user_name); user.innerHTML = '';
      const strong = document.createElement('strong'); strong.textContent = session.user_name || '—'; user.appendChild(strong);
      addTextCell(row, session.infobase_name || session.infobase_id);
      addTextCell(row, session.host);
      const app = addTextCell(row, ''); app.textContent = '';
      const tag = document.createElement('span'); tag.className = 'code-badge'; tag.textContent = session.app_id || '—'; app.appendChild(tag);
      addTextCell(row, session.started_at);
      const id = addTextCell(row, ''); id.textContent = '';
      const code = document.createElement('code'); code.textContent = session.id || '—'; id.appendChild(code);
      target.appendChild(row);
    }
    filterTable(document.getElementById('company-sessions'));
  };

  const renderAllSessions = sessions => {
    const target = document.querySelector('[data-all-sessions-body]');
    if (!target) return;
    target.replaceChildren();
    if (!sessions.length) {
      const row = document.createElement('tr'); const cell = document.createElement('td'); cell.colSpan = 7;
      const empty = document.createElement('div'); empty.className = 'empty-state';
      const title = document.createElement('strong'); title.textContent = 'Активных сеансов нет';
      empty.appendChild(title); cell.appendChild(empty); row.appendChild(cell); target.appendChild(row); return;
    }
    for (const session of sessions) {
      const row = document.createElement('tr');
      if (!session.counted) row.className = 'technical-row';
      row.dataset.searchText = `${session.user_name || ''} ${session.host || ''} ${session.infobase_name || ''}`.toLocaleLowerCase();
      row.dataset.company = (session.company_name || '').toLocaleLowerCase();
      row.dataset.base = (session.infobase_name || '').toLocaleLowerCase();
      row.dataset.kind = session.counted ? 'counted' : 'ignored';
      addTextCell(row, session.company_name); addTextCell(row, session.infobase_name);
      const user = addTextCell(row, ''); user.textContent = ''; const strong = document.createElement('strong'); strong.textContent = session.user_name || '—'; user.appendChild(strong);
      addTextCell(row, session.host);
      const app = addTextCell(row, ''); app.textContent = ''; const tag = document.createElement('span'); tag.className = 'code-badge'; tag.textContent = session.app_id || '—'; app.appendChild(tag);
      addTextCell(row, session.started_at);
      const counted = addTextCell(row, ''); counted.textContent = ''; const badge = document.createElement('span'); setBadge(badge, session.counted ? 'success' : 'neutral', session.counted ? 'Учитывается' : 'Не учитывается'); counted.appendChild(badge);
      target.appendChild(row);
    }
    document.querySelector('[data-session-total]').textContent = sessions.length;
    document.querySelector('[data-session-counted]').textContent = sessions.filter(item => item.counted).length;
    filterTable(document.getElementById('sessions-table'));
  };

  async function refresh() {
    document.querySelectorAll('[data-refresh-button]').forEach(button => button.disabled = true);
    try {
      const response = await fetch('/api/dashboard', {cache: 'no-store'});
      if (response.status === 401) { location.reload(); return; }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (data.available_bases.length !== initialBaseCount) { location.reload(); return; }
      setRasState(Boolean(data.last_updated), data.errors.length > 0, data.last_updated);
      document.querySelectorAll('[data-kpi="bases"]').forEach(node => node.textContent = data.available_bases.length);
      document.querySelectorAll('[data-kpi="sessions"]').forEach(node => node.textContent = data.total_user_sessions);
      document.querySelectorAll('[data-kpi="problems"]').forEach(node => node.textContent = data.problem_count);
      updateCompanyRows(data.companies);
      renderCompanySessions(data.companies);
      renderAllSessions(data.all_sessions);
    } catch (error) {
      setRasState(false, true, null);
    } finally {
      document.querySelectorAll('[data-refresh-button]').forEach(button => button.disabled = false);
    }
  }

  const manualRefresh = async () => {
    try {
      await fetch('/api/refresh', {method: 'POST'});
    } finally {
      await refresh();
    }
  };
  document.querySelectorAll('[data-refresh-button]').forEach(button => button.addEventListener('click', manualRefresh));
  refresh();
  window.setInterval(refresh, 2000);
})();
