(function () {
    function atualizarPlaceholder() {
        const tipo = document.getElementById('tipo-desconto');
        const label = document.getElementById('label-valor');
        const input = document.getElementById('input-valor');
        if (!tipo || !label || !input) return;

        if (tipo.value === 'PERCENTUAL') {
            label.textContent = 'Desconto (%)';
            input.placeholder = 'Ex: 15';
            input.max = 100;
        } else {
            label.textContent = 'Desconto (R$)';
            input.placeholder = 'Ex: 10.00';
            input.removeAttribute('max');
        }
    }

    function iniciarCupons() {
        const codigo = document.querySelector('[name="codigo"]');
        if (codigo) {
            codigo.addEventListener('input', function () {
                this.value = this.value.toUpperCase().replace(/\s/g, '');
            });
        }
        atualizarPlaceholder();
    }

    window.atualizarPlaceholder = atualizarPlaceholder;
    document.addEventListener('DOMContentLoaded', iniciarCupons);
})();
