document.addEventListener('DOMContentLoaded', () => {
    const toggleButton = document.querySelector('.toggle-password');
    const passwordInput = document.getElementById('password') || document.getElementById('field_password');
    const roleSelect = document.getElementById('rol_id') || document.getElementById('field_rol_id');
    const permissionsBox = document.getElementById('permissions-box');
    const form = document.querySelector('form');

    const rolePermissions = {
        admin: { label: 'Admin', text: 'Acceso total al sistema' },
        administrador: { label: 'Admin', text: 'Acceso total al sistema' },
        'administrador del sistema': { label: 'Admin', text: 'Acceso total al sistema' },
        vendedor: { label: 'Vendedor', text: 'Ventas, consulta de inventario' },
        bodeguero: { label: 'Bodeguero', text: 'Inventario, Analítica IA y desarme' },
        almacenista: { label: 'Bodeguero', text: 'Inventario, Analítica IA y desarme' }
    };

    function disableAutocompleteForAllFields() {
        document.querySelectorAll('input, select').forEach((field) => {
            field.setAttribute('autocomplete', field.name === 'password' ? 'new-password' : 'off');
            field.setAttribute('autocapitalize', 'none');
            field.setAttribute('autocorrect', 'off');
            field.setAttribute('spellcheck', 'false');
            field.setAttribute('aria-autocomplete', 'none');
            field.dataset.isCleared = 'true';
        });
    }

    function updatePermissions() {
        if (!roleSelect || !permissionsBox) return;

        const selectedOption = roleSelect.selectedOptions[0];
        const roleName = (selectedOption?.textContent || '').trim().toLowerCase();
        const roleInfo = rolePermissions[roleName] || null;

        if (!roleInfo) {
            permissionsBox.innerHTML = '<strong>Permisos del rol:</strong><span>Selecciona un rol para ver sus permisos.</span>';
            return;
        }

        permissionsBox.innerHTML = `<strong>Permisos del rol ${roleInfo.label}:</strong><span>${roleInfo.text}</span>`;
    }

    disableAutocompleteForAllFields();

    if (form) {
        form.addEventListener('submit', () => {
            setTimeout(() => {
                disableAutocompleteForAllFields();
            }, 50);
        });
    }

    window.addEventListener('pageshow', () => {
        disableAutocompleteForAllFields();
    });

    if (toggleButton && passwordInput) {
        toggleButton.addEventListener('click', () => {
            const isPassword = passwordInput.type === 'password';
            passwordInput.type = isPassword ? 'text' : 'password';
            toggleButton.innerHTML = isPassword
                ? '<i class="fa-regular fa-eye-slash"></i>'
                : '<i class="fa-regular fa-eye"></i>';
            toggleButton.setAttribute('aria-label', isPassword ? 'Ocultar contraseña' : 'Mostrar contraseña');
        });
    }

    if (roleSelect) {
        roleSelect.addEventListener('change', updatePermissions);
        updatePermissions();
    }
});
