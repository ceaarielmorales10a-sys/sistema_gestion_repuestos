(() => {
  const createModal = document.getElementById('vehicle-modal');
  const detailModal = document.getElementById('vehicle-detail-modal');
  const pieceModal = document.getElementById('piece-modal');
  const incidentModal = document.getElementById('incident-modal');
  const finalizeModal = document.getElementById('finalize-modal');
  const photoModal = document.getElementById('desarme-photo-modal');
  const photoModalImage = document.getElementById('desarme-photo-modal-image');
  const photoModalCaption = document.getElementById('desarme-photo-modal-caption');
  const pieceForm = document.getElementById('piece-form');
  const pieceFormId = document.getElementById('piece-form-id');
  const pieceSubmit = document.getElementById('piece-submit');
  const piecePhoto = document.getElementById('piece-photo');
  const piecePhotoPreview = document.getElementById('piece-photo-preview');
  const piecePhotoName = document.getElementById('piece-photo-name');
  let piecePhotoUrl = null;
  const openButton = document.getElementById('open-create-vehicle');
  const form = document.getElementById('create-vehicle-form');
  const main = document.querySelector('.disassembly-main');
  const sidebar = document.getElementById('sidebar');
  const brandSelect = document.getElementById('vehicle-brand');
  const modelSelect = document.getElementById('vehicle-model');
  const submitButton = document.getElementById('vehicle-submit');
  const formAction = document.getElementById('vehicle-form-action');
  const formId = document.getElementById('vehicle-form-id');
  const formTitle = document.getElementById('vehicle-modal-title');
  const detailContent = document.getElementById('vehicle-detail-content');
  let previousFocus = null;
  let modelRequestId = 0;

  const focusableSelector = 'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])';
  const activeModal = () => [pieceModal, incidentModal, finalizeModal, createModal, detailModal, photoModal].find((modal) => modal && !modal.hidden);

  const setPageInert = (inert) => {
    if (main) main.inert = inert;
    if (sidebar) sidebar.inert = inert;
  };

  const resetModels = (label = 'Selecciona primero una marca...') => {
    if (!modelSelect) return;
    modelSelect.replaceChildren(new Option(label, '', true, true));
    modelSelect.setCustomValidity('');
    modelSelect.disabled = true;
  };

  const loadModels = async (brandId, selectedModelId = '') => {
    const requestId = ++modelRequestId;
    resetModels(brandId ? 'Cargando modelos...' : 'Selecciona primero una marca...');
    if (!brandId || !modelSelect) return;

    try {
      const response = await fetch(`/api/modelos?marca_id=${encodeURIComponent(brandId)}`, {
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) throw new Error('No se pudieron cargar los modelos.');
      const models = await response.json();
      if (requestId !== modelRequestId) return;
      modelSelect.replaceChildren(new Option(models.length ? 'Seleccionar...' : 'No hay modelos para esta marca', '', true, true));
      models.forEach((model) => modelSelect.add(new Option(model.nombre, model.id)));
      modelSelect.disabled = models.length === 0;
      if (selectedModelId && models.some((model) => String(model.id) === String(selectedModelId))) {
        modelSelect.value = String(selectedModelId);
      }
    } catch {
      if (requestId !== modelRequestId) return;
      resetModels('Error al cargar modelos. Cambia la marca para reintentar.');
    }
  };

  const closeActiveModal = () => {
    const modal = activeModal();
    if (!modal) return;
    modal.hidden = true;
    if (modal === pieceModal && piecePhotoUrl) {
      URL.revokeObjectURL(piecePhotoUrl);
      piecePhotoUrl = null;
    }
    document.body.classList.remove('modal-open');
    setPageInert(false);
    if (submitButton) {
      submitButton.disabled = false;
      submitButton.innerHTML = '<i class="fa-solid fa-floppy-disk" aria-hidden="true"></i> Guardar';
    }
    if (pieceSubmit) {
      pieceSubmit.disabled = false;
      pieceSubmit.innerHTML = '<i class="fa-solid fa-floppy-disk" aria-hidden="true"></i> Guardar';
    }
    const incidentSubmit = document.getElementById('incident-submit');
    if (incidentSubmit) incidentSubmit.disabled = false;
    previousFocus?.focus?.();
  };

  const showModal = (modal, focusTarget) => {
    if (!modal) return;
    previousFocus = document.activeElement;
    modal.hidden = false;
    document.body.classList.add('modal-open');
    setPageInert(true);
    focusTarget?.focus();
  };

  const resetVehicleForm = () => {
    form?.reset();
    if (formAction) formAction.value = 'create';
    if (formId) formId.value = '';
    if (form) form.dataset.mode = 'create';
    if (formTitle) formTitle.textContent = 'Registrar Vehículo para Desarme';
    if (submitButton) submitButton.innerHTML = '<i class="fa-solid fa-floppy-disk" aria-hidden="true"></i> Guardar';
    resetModels();
  };

  const openCreateModal = () => {
    if (!createModal) return;
    resetVehicleForm();
    showModal(createModal, brandSelect);
  };

  const openEditModal = (button) => {
    if (!createModal || !form) return;
    resetVehicleForm();
    formAction.value = 'edit';
    formId.value = button.dataset.id || '';
    form.dataset.mode = 'edit';
    formTitle.textContent = 'Modificar Vehículo para Desarme';
    if (submitButton) submitButton.innerHTML = '<i class="fa-solid fa-floppy-disk" aria-hidden="true"></i> Guardar cambios';
    brandSelect.value = button.dataset.brand || '';
    document.getElementById('vehicle-year').value = button.dataset.year || '';
    document.getElementById('vehicle-color').value = button.dataset.color || '';
    document.getElementById('vehicle-transmission').value = button.dataset.transmission || '';
    document.getElementById('vehicle-vin').value = button.dataset.vin || '';
    document.getElementById('vehicle-plate').value = button.dataset.plate || '';
    document.getElementById('vehicle-acquired').value = button.dataset.acquired || '';
    document.getElementById('vehicle-origin').value = button.dataset.origin || '';
    document.getElementById('vehicle-state').value = button.dataset.state || '';
    showModal(createModal, brandSelect);
    loadModels(brandSelect.value, button.dataset.model);
  };

  openButton?.addEventListener('click', openCreateModal);
  document.querySelectorAll('[data-edit-vehicle]').forEach((button) => {
    button.addEventListener('click', () => openEditModal(button));
  });
  const requestedEditId = new URLSearchParams(window.location.search).get('edit_vehicle');
  if (requestedEditId) {
    const requestedEditButton = [...document.querySelectorAll('[data-edit-vehicle]')]
      .find((button) => button.dataset.id === requestedEditId);
    if (requestedEditButton) openEditModal(requestedEditButton);
  } else if (new URLSearchParams(window.location.search).get('open_modal') === '1') {
    openCreateModal();
  }
  document.querySelectorAll('[data-cancel-form]').forEach((cancelForm) => {
    cancelForm.addEventListener('submit', (event) => {
      const vehicleId = cancelForm.querySelector('input[name="id_vehiculo_desarme"]')?.value || '';
      if (!window.confirm(`¿Cancelar el proceso VEH-${String(vehicleId).padStart(6, '0')}? Se conservará el vehículo y su historial.`)) {
        event.preventDefault();
      }
    });
  });
  document.querySelectorAll('[data-start-desarme], [data-finalize-desarme]').forEach((processForm) => {
    processForm.addEventListener('submit', (event) => {
      const button = processForm.querySelector('button[type="submit"]');
      const vehicleName = processForm.dataset.vehicleName || 'este vehículo';
      const isStart = processForm.matches('[data-start-desarme]');
      const prompt = isStart
        ? `¿Iniciar el proceso de desarme para ${vehicleName}?`
        : `¿Finalizar el proceso de desarme para ${vehicleName}?`;
      if (!window.confirm(prompt)) {
        event.preventDefault();
        return;
      }
      if (!button) return;
      button.disabled = true;
      button.innerHTML = `<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> ${isStart ? 'Iniciando...' : 'Finalizando...'}`;
    });
  });
  document.querySelectorAll('[data-close-vehicle-modal], [data-close-detail], [data-close-flash]').forEach((button) => {
    button.addEventListener('click', () => {
      if (button.matches('[data-close-flash]')) {
        button.closest('.disassembly-flash')?.remove();
      } else {
        closeActiveModal();
      }
    });
  });
  [createModal, detailModal, pieceModal, incidentModal, finalizeModal].forEach((modal) => modal?.addEventListener('click', (event) => {
    if (event.target === modal) closeActiveModal();
  }));

  const closePhotoModal = () => {
    if (photoModal) {
      photoModal.hidden = true;
      document.body.classList.remove('overflow-hidden');
      if (photoModalImage) photoModalImage.src = '';
      if (photoModalCaption) photoModalCaption.textContent = '';
    }
  };

  document.getElementById('desarme-photo-close')?.addEventListener('click', closePhotoModal);
  photoModal?.addEventListener('click', (event) => { if (event.target === photoModal) closePhotoModal(); });

  document.querySelectorAll('[data-open-piece-modal]').forEach((button) => {
    button.addEventListener('click', () => {
      if (!pieceModal) return;
      if (piecePhotoUrl) URL.revokeObjectURL(piecePhotoUrl);
      piecePhotoUrl = null;
      pieceForm?.reset();
      if (pieceFormId) pieceFormId.value = '';
      const pieceTitle = document.getElementById('piece-modal-title');
      if (pieceTitle) pieceTitle.textContent = 'Registrar Pieza';
      if (pieceSubmit) {
        pieceSubmit.disabled = false;
        pieceSubmit.innerHTML = '<i class="fa-solid fa-floppy-disk" aria-hidden="true"></i> Guardar';
      }
      if (piecePhotoPreview) piecePhotoPreview.innerHTML = '<i class="fa-regular fa-image" aria-hidden="true"></i>';
      if (piecePhotoName) piecePhotoName.textContent = 'Sin fotografía';
      showModal(pieceModal, pieceModal.querySelector('#piece-type'));
    });
  });
  document.querySelectorAll('[data-edit-piece]').forEach((button) => {
    button.addEventListener('click', () => {
      if (!pieceModal || !pieceForm) return;
      if (piecePhotoUrl) URL.revokeObjectURL(piecePhotoUrl);
      piecePhotoUrl = null;
      pieceForm.reset();
      if (pieceFormId) pieceFormId.value = button.dataset.id || '';
      pieceForm.querySelector('[name="id_tipo_repuesto"]').value = button.dataset.type || '';
      pieceForm.querySelector('[name="estado_pieza"]').value = button.dataset.state || '';
      pieceForm.querySelector('[name="id_lado"]').value = button.dataset.side || '';
      pieceForm.querySelector('[name="id_posicion"]').value = button.dataset.position || '';
      pieceForm.querySelector('[name="cantidad"]').value = button.dataset.quantity || '1';
      pieceForm.querySelector('[name="precio_venta"]').value = button.dataset.price || '0';
      pieceForm.querySelector('[name="observaciones"]').value = button.dataset.observations || '';
      const pieceTitle = document.getElementById('piece-modal-title');
      if (pieceTitle) pieceTitle.textContent = 'Modificar Pieza';
      if (pieceSubmit) {
        pieceSubmit.disabled = false;
        pieceSubmit.innerHTML = '<i class="fa-solid fa-floppy-disk" aria-hidden="true"></i> Guardar cambios';
      }
      if (piecePhotoPreview) piecePhotoPreview.innerHTML = '<i class="fa-regular fa-image" aria-hidden="true"></i>';
      if (piecePhotoName) piecePhotoName.textContent = 'Selecciona una foto solo si deseas reemplazarla';
      showModal(pieceModal, pieceModal.querySelector('#piece-type'));
    });
  });
  document.querySelectorAll('[data-close-piece-modal]').forEach((button) => {
    button.addEventListener('click', closeActiveModal);
  });
  document.querySelector('[data-choose-piece-photo]')?.addEventListener('click', () => piecePhoto?.click());
  piecePhoto?.addEventListener('change', () => {
    const file = piecePhoto.files?.[0];
    if (!file) return;
    const allowedTypes = ['image/jpeg', 'image/png', 'image/webp'];
    const allowedExtensions = ['.jpg', '.jpeg', '.png', '.webp'];
    const hasAllowedExtension = allowedExtensions.some((extension) => file.name.toLowerCase().endsWith(extension));
    if (file.size > 3 * 1024 * 1024 || (!allowedTypes.includes(file.type) && !hasAllowedExtension)) {
      piecePhoto.value = '';
      if (piecePhotoName) piecePhotoName.textContent = 'Sin fotografía';
      if (piecePhotoPreview) piecePhotoPreview.innerHTML = '<i class="fa-regular fa-image" aria-hidden="true"></i>';
      window.alert(file.size > 3 * 1024 * 1024 ? 'La fotografía no puede superar 3 MB.' : 'Selecciona una fotografía JPG, PNG o WEBP.');
      return;
    }
    if (piecePhotoUrl) URL.revokeObjectURL(piecePhotoUrl);
    piecePhotoUrl = URL.createObjectURL(file);
    if (piecePhotoPreview) {
      const image = document.createElement('img');
      image.src = piecePhotoUrl;
      image.alt = '';
      piecePhotoPreview.replaceChildren(image);
    }
    if (piecePhotoName) piecePhotoName.textContent = file.name;
  });
  pieceForm?.addEventListener('submit', (event) => {
    if (!pieceForm.reportValidity()) {
      event.preventDefault();
      return;
    }
    // Mark that form was submitted to prevent modal reopening after redirect
    sessionStorage.setItem('pieceFormSubmitted', '1');
    if (pieceSubmit) {
      pieceSubmit.disabled = true;
      pieceSubmit.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Guardando...';
    }
  });
  window.addEventListener('beforeunload', () => {
    if (piecePhotoUrl) URL.revokeObjectURL(piecePhotoUrl);
  });
  brandSelect?.addEventListener('change', () => loadModels(brandSelect.value));

  document.querySelectorAll('[data-vehicle-id]').forEach((button) => {
    button.addEventListener('click', () => {
      const vehicle = button.dataset;
      const fields = [
        ['ID vehículo', `VEH-${String(vehicle.vehicleId || '').padStart(6, '0')}`],
        ['Marca', vehicle.make], ['Modelo', vehicle.model], ['Año', vehicle.year],
        ['Color', vehicle.color], ['Transmisión', vehicle.transmission],
        ['Placa', vehicle.plate], ['VIN / Chasis', vehicle.vin],
        ['Origen', vehicle.origin], ['Fecha de adquisición', vehicle.acquired],
        ['Estado', vehicle.state], ['Fecha de desarme', vehicle.dismantled], ['Piezas', vehicle.pieces],
      ];
      detailContent?.replaceChildren();
      fields.forEach(([label, value]) => {
        const item = document.createElement('div');
        const term = document.createElement('dt');
        const description = document.createElement('dd');
        term.textContent = label;
        description.textContent = value == null || value === '' ? '—' : String(value);
        item.append(term, description);
        detailContent?.append(item);
      });
      showModal(detailModal, detailModal?.querySelector('[data-close-detail]'));
    });
  });

  document.addEventListener('keydown', (event) => {
    const modal = activeModal();
    if (!modal) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      if (modal === photoModal) {
        closePhotoModal();
      } else {
        closeActiveModal();
      }
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = [...modal.querySelectorAll(focusableSelector)].filter((element) => element.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });

  form?.addEventListener('submit', (event) => {
    if (!form.reportValidity()) {
      event.preventDefault();
      return;
    }
    if (modelSelect?.disabled || !modelSelect?.value) {
      event.preventDefault();
      modelSelect?.focus();
      modelSelect?.setCustomValidity('Selecciona un modelo de la marca elegida.');
      modelSelect?.reportValidity();
      modelSelect?.addEventListener('change', () => modelSelect.setCustomValidity(''), { once: true });
      return;
    }
    if (submitButton) {
      submitButton.disabled = true;
      submitButton.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Guardando...';
    }
  });

  document.querySelectorAll('[data-close-flash]').forEach((button) => {
    button.addEventListener('click', () => button.closest('.disassembly-flash')?.remove());
  });
  document.querySelectorAll('.disassembly-flash').forEach((notice) => {
    window.setTimeout(() => {
      notice.classList.add('is-closing');
      window.setTimeout(() => notice.remove(), 220);
    }, 5000);
  });

  const initialParams = new URLSearchParams(window.location.search);
  const formSubmitted = sessionStorage.getItem('pieceFormSubmitted');
  if (initialParams.get('open_piece') === '1' && pieceModal && !formSubmitted) {
    showModal(pieceModal, pieceModal.querySelector('#piece-type'));
    const cleanUrl = new URL(window.location.href);
    cleanUrl.searchParams.delete('open_piece');
    window.history.replaceState({}, '', cleanUrl);
  }
  sessionStorage.removeItem('pieceFormSubmitted');
  if (initialParams.get('open_modal') === '1' && createModal) {
    const editId = initialParams.get('edit_vehicle');
    const editButton = editId
      ? [...document.querySelectorAll('[data-edit-vehicle]')].find((button) => button.dataset.id === editId)
      : null;
    if (editButton) openEditModal(editButton);
    else openCreateModal();
  }

  const detailTabs = [...document.querySelectorAll('[data-desarme-tab]')];
  const selectDetailTab = (tab, moveFocus = false) => {
    const panelId = tab.dataset.desarmeTab;
    detailTabs.forEach((candidate) => {
      const selected = candidate === tab;
      candidate.classList.toggle('is-active', selected);
      candidate.setAttribute('aria-selected', String(selected));
      candidate.tabIndex = selected ? 0 : -1;
      const panel = document.getElementById(candidate.dataset.desarmeTab);
      if (panel) panel.hidden = !selected;
    });
    if (moveFocus) tab.focus();
  };
  const incidentPiece = document.getElementById('incident-piece');
  const openIncidentModal = (button) => {
    const incidentsTab = document.getElementById('tab-incidents');
    if (incidentsTab) selectDetailTab(incidentsTab);
    if (!incidentModal) return;
    if (incidentPiece) incidentPiece.value = button?.dataset.pieceId || '';
    showModal(incidentModal, incidentPiece);
  };
  document.querySelectorAll('[data-open-incident-modal]').forEach((button) => {
    button.addEventListener('click', () => openIncidentModal(button));
  });
  document.querySelectorAll('[data-close-incident-modal]').forEach((button) => {
    button.addEventListener('click', closeActiveModal);
  });
  document.querySelector('[data-open-finalize-modal]')?.addEventListener('click', () => {
    showModal(finalizeModal, document.getElementById('finalize-observations'));
  });
  document.querySelectorAll('[data-close-finalize-modal]').forEach((button) => {
    button.addEventListener('click', closeActiveModal);
  });
  const finalizeForm = document.getElementById('finalize-form');
  const finalizeSubmit = document.getElementById('finalize-submit');
  finalizeForm?.addEventListener('submit', (event) => {
    if (!finalizeForm.reportValidity()) {
      event.preventDefault();
      return;
    }
    if (finalizeSubmit) {
      finalizeSubmit.disabled = true;
      finalizeSubmit.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Finalizando...';
    }
  });

  document.querySelectorAll('.desarme-photo-trigger').forEach((trigger) => {
    trigger.addEventListener('click', () => {
      if (photoModal && photoModalImage && photoModalCaption) {
        photoModalImage.src = trigger.dataset.image;
        photoModalImage.alt = trigger.dataset.caption || 'Fotografía de la pieza';
        photoModalCaption.textContent = trigger.dataset.caption || '';
        photoModal.hidden = false;
        document.body.classList.add('overflow-hidden');
      }
    });
  });

  if (new URLSearchParams(window.location.search).get('open_incident') === '1' && incidentModal) {
    const incidentsTab = document.getElementById('tab-incidents');
    if (incidentsTab) selectDetailTab(incidentsTab);
    showModal(incidentModal, incidentPiece);
  }
  if (new URLSearchParams(window.location.search).get('open_finalize') === '1' && finalizeModal) {
    showModal(finalizeModal, document.getElementById('finalize-observations'));
  }
  const requestedTab = document.getElementById(window.location.hash.slice(1));
  const requestedTabButton = requestedTab
    ? detailTabs.find((tab) => tab === requestedTab || tab.dataset.desarmeTab === requestedTab.id)
    : null;
  if (requestedTabButton) selectDetailTab(requestedTabButton);
  detailTabs.forEach((tab, index) => {
    tab.addEventListener('click', () => selectDetailTab(tab));
    tab.addEventListener('keydown', (event) => {
      let nextIndex = index;
      if (event.key === 'ArrowRight') nextIndex = (index + 1) % detailTabs.length;
      else if (event.key === 'ArrowLeft') nextIndex = (index - 1 + detailTabs.length) % detailTabs.length;
      else if (event.key === 'Home') nextIndex = 0;
      else if (event.key === 'End') nextIndex = detailTabs.length - 1;
      else return;
      event.preventDefault();
      selectDetailTab(detailTabs[nextIndex], true);
    });
  });

  const filterForm = document.getElementById('disassembly-filter-form');
  let searchTimer;
  filterForm?.querySelector('input[name="q"]')?.addEventListener('input', () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => filterForm.requestSubmit(), 350);
  });
  filterForm?.querySelectorAll('select').forEach((select) => select.addEventListener('change', () => filterForm.requestSubmit()));

  const mobileToggle = document.getElementById('mobile-menu-button');
  const mobileOverlay = document.getElementById('mobile-sidebar-overlay');
  const closeMobileMenu = () => {
    sidebar?.classList.add('-translate-x-full');
    mobileOverlay?.classList.add('hidden');
    mobileToggle?.setAttribute('aria-expanded', 'false');
  };
  mobileToggle?.addEventListener('click', () => {
    const isClosed = sidebar?.classList.contains('-translate-x-full');
    sidebar?.classList.toggle('-translate-x-full');
    mobileOverlay?.classList.toggle('hidden');
    mobileToggle?.setAttribute('aria-expanded', String(Boolean(isClosed)));
  });
  mobileOverlay?.addEventListener('click', closeMobileMenu);
  window.addEventListener('resize', () => {
    if (window.innerWidth >= 768) closeMobileMenu();
  });
})();
