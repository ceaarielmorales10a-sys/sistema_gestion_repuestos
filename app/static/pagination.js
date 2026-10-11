(() => {
  const tables = [];
  const emptyRow = (row) => row.classList.contains('empty')
    || row.classList.contains('server-no-data')
    || (!row.dataset.paginationGroupKey && row.querySelector('td[colspan]'));
  const filteredOut = (row) => row.hidden
    || row.classList.contains('hidden')
    || row.style.display === 'none';

  function makeGroups(table) {
    const body = table.tBodies[0];
    if (!body) return [];

    const rows = [...body.rows];
    const groups = [];
    const nextSelector = table.dataset.paginationGroupNext;
    const groupKey = table.dataset.paginationGroupKey;

    if (groupKey) {
      for (let index = 0; index < rows.length;) {
        const key = rows[index].dataset[groupKey];
        if (!key) {
          if (!emptyRow(rows[index])) groups.push({ rows: [rows[index]] });
          index += 1;
          continue;
        }
        const groupRows = [];
        while (index < rows.length && rows[index].dataset[groupKey] === key) {
          groupRows.push(rows[index]);
          index += 1;
        }
        if (groupRows.some((row) => !filteredOut(row))) {
          groups.push({ rows: groupRows });
        }
      }
      return groups;
    }

    for (let index = 0; index < rows.length; index += 1) {
      const row = rows[index];
      if (emptyRow(row) || filteredOut(row)) continue;
      const groupRows = [row];
      if (nextSelector) {
        while (index + 1 < rows.length && rows[index + 1].matches(nextSelector)) {
          groupRows.push(rows[index + 1]);
          index += 1;
        }
      }
      groups.push({ rows: groupRows });
    }
    return groups;
  }

  function getContainer(table) {
    return table.parentElement;
  }

  function render(table, state) {
    const pageSize = Math.max(1, Number(table.dataset.paginationPageSize) || 10);
    const rows = table.tBodies[0] ? [...table.tBodies[0].rows] : [];
    rows.forEach((row) => row.classList.remove('pagination-page-hidden'));
    const groups = makeGroups(table);
    const totalPages = Math.max(1, Math.ceil(groups.length / pageSize));
    state.page = Math.min(state.page, totalPages);
    const start = (state.page - 1) * pageSize;
    const end = Math.min(start + pageSize, groups.length);

    groups.forEach((group, index) => {
      if (index >= start && index < end) return;
      group.rows.forEach((row) => row.classList.add('pagination-page-hidden'));
    });

    if (groups.length <= pageSize) {
      state.navigation.hidden = true;
      return;
    }

    state.navigation.hidden = false;
    state.summary.textContent = `Mostrando ${start + 1}–${end} de ${groups.length} registros`;
    state.previous.disabled = state.page === 1;
    state.next.disabled = state.page === totalPages;
    state.pages.replaceChildren();

    const pageNumbers = new Set([1, totalPages]);
    for (let page = Math.max(1, state.page - 2); page <= Math.min(totalPages, state.page + 2); page += 1) {
      pageNumbers.add(page);
    }
    let previousPage = 0;
    [...pageNumbers].sort((left, right) => left - right).forEach((page) => {
      if (page - previousPage > 1) {
        const gap = document.createElement('span');
        gap.className = 'table-pagination-gap';
        gap.textContent = '…';
        state.pages.append(gap);
      }
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = String(page);
      button.setAttribute('aria-label', `Ir a la página ${page}`);
      if (page === state.page) {
        button.setAttribute('aria-current', 'page');
      }
      button.classList.toggle('is-current', page === state.page);
      button.addEventListener('click', () => {
        state.page = page;
        render(table, state);
      });
      state.pages.append(button);
      previousPage = page;
    });
  }

  function initialize(table) {
    const container = getContainer(table);
    if (!container) return;

    const navigation = document.createElement('nav');
    navigation.className = 'table-pagination';
    navigation.setAttribute('aria-label', 'Paginación de tabla');
    navigation.innerHTML = `
      <span class="table-pagination-summary"></span>
      <div class="table-pagination-controls">
        <button type="button" class="table-pagination-previous" aria-label="Página anterior">‹</button>
        <div class="table-pagination-pages"></div>
        <button type="button" class="table-pagination-next" aria-label="Página siguiente">›</button>
      </div>`;
    container.after(navigation);

    const state = {
      page: 1,
      navigation,
      summary: navigation.querySelector('.table-pagination-summary'),
      pages: navigation.querySelector('.table-pagination-pages'),
      previous: navigation.querySelector('.table-pagination-previous'),
      next: navigation.querySelector('.table-pagination-next'),
    };
    state.previous.addEventListener('click', () => {
      state.page -= 1;
      render(table, state);
    });
    state.next.addEventListener('click', () => {
      state.page += 1;
      render(table, state);
    });
    tables.push({ table, state });
    render(table, state);

    const body = table.tBodies[0];
    if (body) {
      new MutationObserver(() => render(table, state)).observe(body, { childList: true });
    }
  }

  function refresh() {
    tables.forEach(({ table, state }) => render(table, state));
  }

  document.addEventListener('input', refresh);
  document.addEventListener('change', refresh);
  document.addEventListener('click', (event) => {
    if (!(event.target instanceof Element) || !event.target.closest('.table-pagination')) refresh();
  });
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('table[data-pagination-page-size]').forEach(initialize);
  });
})();
