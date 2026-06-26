(function () {
    const textos = {
        1: 'Muito ruim',
        2: 'Ruim',
        3: 'Regular',
        4: 'Bom',
        5: 'Excelente!',
    };

    function iniciarAvaliacao() {
        const labels = document.querySelectorAll('.estrelas-wrap label');
        document.querySelectorAll('.estrelas-wrap input').forEach((input) => {
            input.addEventListener('change', () => {
                const valor = parseInt(input.value, 10);
                labels.forEach((label, index) => label.classList.toggle('sel', index < valor));

                const textoNota = document.getElementById('texto-nota');
                if (textoNota) textoNota.textContent = textos[valor] || '';

                const btn = document.getElementById('btn-env');
                if (btn) btn.disabled = false;
            });
        });
    }

    document.addEventListener('DOMContentLoaded', iniciarAvaliacao);
})();
