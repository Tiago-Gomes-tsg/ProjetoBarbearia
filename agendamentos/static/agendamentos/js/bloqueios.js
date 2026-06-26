(function () {
    function iniciarBloqueios() {
        const inicioInput = document.getElementById('data_inicio');
        const fimInput = document.getElementById('data_fim');
        if (!inicioInput || !fimInput) return;

        inicioInput.addEventListener('change', function () {
            if (!fimInput.value || fimInput.value < this.value) {
                fimInput.value = this.value;
            }
            fimInput.min = this.value;
        });
    }

    document.addEventListener('DOMContentLoaded', iniciarBloqueios);
})();
