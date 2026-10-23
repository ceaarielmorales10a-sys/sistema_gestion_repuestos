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
  const typeInput = form.querySelector('#tipo-repuesto-search');
  const typeMenu = form.querySelector('#tipo-repuesto-options');
  const locationSelect = form.querySelector('#id_ubicacion');
  const locationInput = form.querySelector('#id_ubicacion_submit');
  const setLocation = (locationId) => {
    const value = locationId || '';
    if (locationSelect) locationSelect.value = value;
    if (locationInput) locationInput.value = value;
  };

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

  const closeTypeMenu = () => {
    if (!typeMenu || !typeInput) return;
    typeMenu.hidden = true;
    typeInput.setAttribute('aria-expanded', 'false');
  };
  const chooseType = (option) => {
    if (!option || !typeInput || !typeSelect) return;
    typeInput.value = option.textContent.trim();
    typeSelect.value = option.value;
    typeInput.setCustomValidity('');
    setLocation(option.dataset.ubicacionId);
    closeTypeMenu();
  };
  const showTypeOptions = () => {
    if (!typeMenu || !typeInput || !typeSelect) return;
    const query = typeInput.value.trim().toLocaleLowerCase();
    typeSelect.value = '';
    typeInput.setCustomValidity(query ? 'Selecciona un tipo de repuesto de la lista.' : 'Escribe y selecciona un tipo de repuesto del catálogo.');
    setLocation('');
    typeMenu.replaceChildren();
    const matches = [...typeSelect.options].filter((option) => option.value && option.textContent.trim().toLocaleLowerCase().includes(query));
    if (!query) {
      closeTypeMenu();
      return;
    }
    if (!matches.length) {
      const empty = document.createElement('div');
      empty.className = 'type-autocomplete-empty';
      empty.textContent = 'No se encontraron tipos de repuesto';
      typeMenu.append(empty);
    } else {
      matches.forEach((option) => {
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'type-autocomplete-option';
        item.setAttribute('role', 'option');
        item.textContent = option.textContent.trim();
        item.addEventListener('click', () => chooseType(option));
        typeMenu.append(item);
      });
    }
    typeMenu.hidden = false;
    typeInput.setAttribute('aria-expanded', 'true');
  };
  if (typeInput && typeSelect) {
    if (typeSelect.value) {
      const selectedType = typeSelect.selectedOptions[0];
      const configuredLocation = selectedType?.dataset.ubicacionId || locationInput?.value || '';
      setLocation(configuredLocation);
    }
    if (typeSelect.value) {
      typeInput.value = typeSelect.selectedOptions[0]?.textContent.trim() || '';
      typeInput.setCustomValidity('');
    }
    typeInput.addEventListener('input', showTypeOptions);
    typeInput.addEventListener('focus', () => { if (typeInput.value.trim()) showTypeOptions(); });
    typeInput.addEventListener('blur', () => window.setTimeout(closeTypeMenu, 120));
    typeInput.addEventListener('keydown', (event) => {
      const first = typeMenu?.querySelector('.type-autocomplete-option');
      if (event.key === 'ArrowDown' && first) { event.preventDefault(); first.focus(); }
      if (event.key === 'Escape') closeTypeMenu();
    });
    typeMenu?.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') { closeTypeMenu(); typeInput.focus(); }
    });
  }

  const photoInput = form.querySelector('#fotografia-upload');
  const preview = form.querySelector('#photo-preview');
  const previewImage = form.querySelector('#photo-preview-image');
  const previewName = form.querySelector('#photo-preview-name');
  const previewFrame = form.querySelector('#photo-placeholder');
  const showPreview = (file) => {
    if (!file) return;
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
    if (!typeSelect?.value) typeInput?.setCustomValidity('Escribe y selecciona un tipo de repuesto del catálogo.');
    const submitButton = form.querySelector('button[type="submit"]');
    if (submitButton) {
      submitButton.disabled = true;
      submitButton.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Guardando...';
    }
  });
})();
