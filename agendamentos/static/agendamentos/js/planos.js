(function () {
    function destacarVencidos() {
        document.querySelectorAll('.badge-vencendo').forEach((badge) => {
            const linha = badge.closest('tr');
            if (linha) linha.style.opacity = '0.7';
        });
    }

    document.addEventListener('DOMContentLoaded', destacarVencidos);
})();
