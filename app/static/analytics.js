(() => {
  const initialDataElement = document.getElementById('analytics-initial-data');
  const errorElement = document.getElementById('analytics-error');
  const aiErrorElement = document.getElementById('ai-error');
  const periodButtons = [...document.querySelectorAll('[data-period]')];
  const charts = {};
  let currentDays = Number(
    document.querySelector('[data-period].is-active')?.dataset.period || 30,
  );

  const formatNumber = (value, maximumFractionDigits = 0) =>
    new Intl.NumberFormat('es-GT', { maximumFractionDigits }).format(Number(value) || 0);
  const formatCurrency = (value) =>
    `Q ${new Intl.NumberFormat('es-GT', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(Number(value) || 0)}`;
  const showError = (element, message) => {
    element.textContent = message;
    element.hidden = false;
  };

  const buildPredictionCard = (item) => {
    const card = document.createElement('article');
    card.className = `prediction-card${item.requiere_reabastecimiento ? ' needs-restock' : ''}`;
    const name = document.createElement('div');
    name.className = 'prediction-name';
    const title = document.createElement('strong');
    title.textContent = item.nombre;
    const icon = document.createElement('i');
    icon.className = `fa-regular ${
      item.requiere_reabastecimiento ? 'fa-circle-exclamation' : 'fa-circle-check'
    }`;
    icon.setAttribute('aria-hidden', 'true');
    name.append(title, icon);

    const description = document.createElement('p');
    description.textContent = `Código: ${item.codigo} · ${item.categoria}`;
    const facts = document.createElement('div');
    facts.className = 'prediction-facts';
    const stockFact = document.createElement('span');
    stockFact.append('Stock actual/mín.: ');
    const stockValue = document.createElement('b');
    stockValue.textContent = `${formatNumber(item.stock)} / ${formatNumber(item.minimo)}`;
    stockFact.append(stockValue);
    const exhaustionFact = document.createElement('span');
    exhaustionFact.append('Agotamiento estimado: ');
    const exhaustionValue = document.createElement('b');
    exhaustionValue.textContent = item.dias_agotamiento == null
      ? '— días'
      : `${formatNumber(item.dias_agotamiento, 1)} días`;
    exhaustionFact.append(exhaustionValue);
    facts.append(stockFact, exhaustionFact);

    const status = document.createElement('span');
    status.className = `prediction-status${item.requiere_reabastecimiento ? ' warning' : ''}`;
    status.textContent = item.urgencia;
    card.append(name, description, facts, status);
    return card;
  };

  const renderPredictions = (items) => {
    const grid = document.getElementById('prediction-grid');
    grid.replaceChildren();
    if (!items.length) {
      const empty = document.createElement('div');
      empty.className = 'analytics-empty';
      empty.textContent = 'No hay repuestos para mostrar en este período.';
      grid.append(empty);
      return;
    }
    items.forEach((item) => grid.append(buildPredictionCard(item)));
  };

  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { position: 'bottom', labels: { boxWidth: 9, usePointStyle: true, font: { size: 9 } } },
      tooltip: {
        callbacks: {
          label(context) {
            const value = context.parsed.y ?? context.parsed;
            return `${context.dataset.label}: ${
              context.dataset.currency ? formatCurrency(value) : formatNumber(value, 1)
            }`;
          },
        },
      },
    },
    scales: {
      x: { grid: { display: false }, ticks: { color: '#8290a3', font: { size: 9 }, maxTicksLimit: 8 } },
      y: { beginAtZero: true, grid: { color: '#edf0f5' }, ticks: { color: '#8290a3', font: { size: 9 } } },
    },
  };

  const renderCharts = (data) => {
    if (typeof Chart === 'undefined') {
      showError(errorElement, 'No se pudieron cargar las gráficas. Revisa la conexión con la biblioteca de gráficos.');
      return;
    }
    Object.values(charts).forEach((chart) => chart.destroy());
    charts.temporal = new Chart(document.getElementById('temporal-chart'), {
      type: 'line',
      data: {
        labels: data.temporal.map((item) => item.fecha.slice(5)),
        datasets: [
          {
            label: 'Ingresos',
            data: data.temporal.map((item) => item.ingresos),
            borderColor: '#3877f3',
            backgroundColor: '#3877f322',
            fill: true,
            tension: 0.35,
            pointRadius: 1,
            currency: true,
          },
          {
            label: 'Unidades vendidas',
            data: data.temporal.map((item) => item.ventas),
            borderColor: '#20a56b',
            backgroundColor: '#20a56b',
            tension: 0.35,
            pointRadius: 1,
            yAxisID: 'y1',
          },
        ],
      },
      options: {
        ...chartOptions,
        scales: {
          ...chartOptions.scales,
          y: { ...chartOptions.scales.y, position: 'left' },
          y1: {
            beginAtZero: true,
            position: 'right',
            grid: { drawOnChartArea: false },
            ticks: { color: '#8290a3', font: { size: 9 } },
          },
        },
      },
    });
    charts.type = new Chart(document.getElementById('type-chart'), {
      type: 'bar',
      data: {
        labels: data.categorias.map((item) => item.nombre),
        datasets: [
          {
            label: 'Unidades vendidas',
            data: data.categorias.map((item) => item.unidades),
            backgroundColor: '#43b883',
            borderRadius: 4,
          },
          {
            label: 'Margen (%)',
            data: data.categorias.map((item) => item.margen_porcentaje),
            backgroundColor: '#9971e8',
            borderRadius: 4,
          },
        ],
      },
      options: { ...chartOptions, indexAxis: 'y' },
    });
    charts.inventory = new Chart(document.getElementById('inventory-chart'), {
      type: 'doughnut',
      data: {
        labels: data.distribucion.map((item) => item.nombre),
        datasets: [{
          data: data.distribucion.map((item) => item.repuestos),
          backgroundColor: ['#39a7e8', '#8863df', '#f3a443', '#25ad72', '#ef6c69', '#72c9ca'],
          borderWidth: 2,
          borderColor: '#fff',
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'right', labels: { boxWidth: 9, usePointStyle: true, font: { size: 9 } } } },
      },
    });
    charts.revenue = new Chart(document.getElementById('revenue-chart'), {
      type: 'bar',
      data: {
        labels: data.categorias.map((item) => item.nombre),
        datasets: [{
          label: 'Ingresos',
          data: data.categorias.map((item) => item.ingresos),
          backgroundColor: '#edaa47',
          borderRadius: 4,
          currency: true,
        }],
      },
      options: {
        ...chartOptions,
        plugins: {
          ...chartOptions.plugins,
          tooltip: {
            callbacks: { label: (context) => `${context.dataset.label}: ${formatCurrency(context.parsed.y)}` },
          },
        },
      },
    });
  };

  const renderDashboard = (data) => {
    document.getElementById('metric-products').textContent = formatNumber(data.metricas.repuestos);
    document.getElementById('metric-units').textContent = formatNumber(data.metricas.unidades_inventario);
    document.getElementById('metric-revenue').textContent = formatCurrency(data.metricas.ingresos);
    document.getElementById('metric-sales').textContent = formatNumber(data.metricas.ventas);
    document.getElementById('metric-margin').textContent = `${formatNumber(data.metricas.margen_porcentaje, 1)}%`;
    document.querySelectorAll('[data-period-label]').forEach((label) => {
      label.textContent = `${data.periodo_dias} días`;
    });
    const lowStock = Number(data.repuestos_urgentes) || 0;
    document.getElementById('recommendation-stock').textContent = lowStock
      ? `${formatNumber(lowStock)} repuesto(s) requieren revisión o reabastecimiento. Verifica las predicciones de stock.`
      : 'No se detectaron repuestos bajo el mínimo ni con agotamiento estimado en los próximos 14 días.';
    renderPredictions(data.predicciones || []);
    renderCharts(data);
    errorElement.hidden = true;
  };

  const updatePeriod = async (days) => {
    currentDays = days;
    periodButtons.forEach((button) => {
      const active = Number(button.dataset.period) === days;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', String(active));
      button.disabled = true;
    });
    errorElement.hidden = true;
    try {
      const response = await fetch(`/api/analytics/dashboard?dias=${encodeURIComponent(days)}`, {
        headers: { Accept: 'application/json' },
      });
      const result = await response.json();
      if (!response.ok || !result.success) throw new Error(result.error || 'No se pudieron actualizar las métricas.');
      renderDashboard(result.data);
    } catch (error) {
      showError(errorElement, error.message || 'No se pudieron actualizar las métricas.');
    } finally {
      periodButtons.forEach((button) => { button.disabled = false; });
    }
  };

  try {
    const initialData = JSON.parse(initialDataElement.textContent);
    if (initialData) renderDashboard(initialData);
  } catch {
    showError(errorElement, 'No se pudieron interpretar los datos iniciales de Analytics.');
  }

  periodButtons.forEach((button) => {
    button.addEventListener('click', () => {
      const days = Number(button.dataset.period);
      if (days !== currentDays) updatePeriod(days);
    });
  });

  document.querySelector('[data-close-predictions]')?.addEventListener('click', () => {
    document.getElementById('predictions-panel').hidden = true;
  });

  document.getElementById('generate-ai')?.addEventListener('click', async (event) => {
    const button = event.currentTarget;
    const output = document.getElementById('ai-recommendation');
    aiErrorElement.hidden = true;
    button.disabled = true;
    output.textContent = 'Generando análisis inteligente...';
    try {
      const response = await fetch('/api/analytics/stock', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ dias: currentDays }),
      });
      const result = await response.json();
      if (!response.ok || !result.success) throw new Error(result.error || 'No se pudo generar el análisis de IA.');
      output.textContent = result.analisis;
    } catch (error) {
      output.textContent = 'No se generaron recomendaciones en esta solicitud.';
      showError(aiErrorElement, error.message || 'No se pudo generar el análisis de IA.');
    } finally {
      button.disabled = false;
    }
  });

  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('mobile-sidebar-overlay');
  const menuButton = document.getElementById('mobile-menu-button');
  const closeSidebar = () => {
    sidebar.classList.add('-translate-x-full');
    overlay.classList.add('hidden');
    menuButton.setAttribute('aria-expanded', 'false');
  };
  menuButton?.addEventListener('click', () => {
    const isOpen = !sidebar.classList.contains('-translate-x-full');
    sidebar.classList.toggle('-translate-x-full', isOpen);
    overlay.classList.toggle('hidden', isOpen);
    menuButton.setAttribute('aria-expanded', String(!isOpen));
  });
  overlay?.addEventListener('click', closeSidebar);
})();
