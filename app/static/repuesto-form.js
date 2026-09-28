(() => {
  const sidebar = document.getElementById('sidebar');
  const mobileToggle = document.getElementById('mobile-menu-button');
  const sidebarOverlay = document.getElementById('mobile-sidebar-overlay');
  const closeSidebar = () => {
    sidebar?.classList.add('-translate-x-full');
    sidebarOverlay?.classList.add('hidden');
    mobileToggle?.setAttribute('aria-expanded', 'false');
    document.body.classList.remove('overflow-hidden');
  };

  mobileToggle?.addEventListener('click', () => {
    const wasClosed = sidebar?.classList.contains('-translate-x-full');
    sidebar?.classList.toggle('-translate-x-full');
    sidebarOverlay?.classList.toggle('hidden');
    mobileToggle?.setAttribute('aria-expanded', String(Boolean(wasClosed)));
    document.body.classList.toggle('overflow-hidden', Boolean(wasClosed));
  });
  sidebarOverlay?.addEventListener('click', closeSidebar);
  window.addEventListener('resize', () => { if (window.innerWidth >= 768) closeSidebar(); });

  const form = document.getElementById('repuesto-form');
  if (!form) return;

  const brandSelect = form.querySelector('#id_marca');
  const modelSelect = form.querySelector('#id_modelo');
  const typeSelect = form.querySelector('#id_tipo_repuesto');
  const locationSelect = form.querySelector('#id_ubicacion');

  const resetModels = (message) => {
    if (!modelSelect) return;
    modelSelect.replaceChildren(new Option(message, '', true, true));
    modelSelect.disabled = true;
  };

  const loadModels = async (brandId) => {
    resetModels(brandId ? 'Cargando modelos...' : 'Seleccionar primero la marca...');
    if (!brandId || !modelSelect) return;

    try {
      const response = await fetch(`/api/modelos?marca_id=${encodeURIComponent(brandId)}`, { headers: { Accept: 'application/json' } });
      if (!response.ok) throw new Error('No se pudieron cargar los modelos.');
      const models = await response.json();
      modelSelect.replaceChildren(new Option(models.length ? 'Seleccionar...' : 'No hay modelos para esta marca', '', true, true));
      models.forEach((model) => modelSelect.add(new Option(model.nombre, model.id)));
      modelSelect.disabled = models.length === 0;
    } catch {
      resetModels('Error al cargar modelos');
    }
  };

  brandSelect?.addEventListener('change', () => loadModels(brandSelect.value));
  if (form.dataset.mode === 'add' && modelSelect) resetModels('Seleccionar primero la marca...');

  const syncWarehouse = () => {
    const option = typeSelect?.options[typeSelect.selectedIndex];
    const locationId = option?.dataset.ubicacionId;
    if (locationSelect && locationId) locationSelect.value = locationId;
  };
  typeSelect?.addEventListener('change', syncWarehouse);
  syncWarehouse();

  const photoInput = form.querySelector('#fotografia-upload');
  const preview = form.querySelector('#photo-preview');
  const previewImage = form.querySelector('#photo-preview-image');
  const previewName = form.querySelector('#photo-preview-name');
  const previewFrame = form.querySelector('#photo-placeholder');
  const showPreview = (file) => {
    if (!file) return;
    if (file.size > 3 * 1024 * 1024) {
      if (photoInput) photoInput.value = '';
      window.alert('La fotografía no puede superar 3 MB.');
      return;
    }
    previewImage.src = URL.createObjectURL(file);
    previewName.textContent = file.name;
    preview.hidden = false;
    if (previewFrame) previewFrame.innerHTML = `<img class="photo-current-image" src="${previewImage.src}" alt="Vista previa del repuesto">`;
  };

  form.querySelector('#choose-photo')?.addEventListener('click', () => photoInput?.click());
  photoInput?.addEventListener('change', () => showPreview(photoInput.files?.[0]));

  const entryDate = form.querySelector('#fecha_ingreso_display');
  if (entryDate && !entryDate.value) entryDate.value = new Date().toLocaleDateString('es-GT');

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') window.location.assign('/inventario');
  });
  form.querySelector('input:not([type="file"]):not([readonly]), select:not(:disabled)')?.focus({ preventScroll: true });
  form.addEventListener('submit', () => {
    const submitButton = form.querySelector('button[type="submit"]');
    if (submitButton) {
      submitButton.disabled = true;
      submitButton.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Guardando...';
    }
  });
})();
