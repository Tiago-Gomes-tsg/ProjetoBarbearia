(function () {
    function readJsonScript(id, fallback) {
        const el = document.getElementById(id);
        if (!el) return fallback;
        try {
            return JSON.parse(el.textContent);
        } catch (e) {
            return fallback;
        }
    }

    function copiarAvaliacao(btn, url) {
        const marcarCopiado = () => {
            const original = btn.textContent;
            btn.textContent = 'Copiado! Envie ao cliente';
            btn.style.background = '#1b3a1b';
            btn.style.color = '#81c784';
            btn.style.borderColor = '#2e7d32';
            btn.classList.add('acao-copiada');
            setTimeout(() => {
                btn.textContent = original;
                btn.style.background = '#1a2a3a';
                btn.style.color = '#64b5f6';
                btn.style.borderColor = '#1976d2';
                btn.classList.remove('acao-copiada');
            }, 3000);
        };

        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(url).then(marcarCopiado).catch(() => copiarComInput(url, btn));
            return;
        }

        copiarComInput(url, btn);
    }

    function copiarComInput(url, btn) {
        const input = document.createElement('input');
        input.value = url;
        document.body.appendChild(input);
        input.select();
        document.execCommand('copy');
        document.body.removeChild(input);
        btn.textContent = 'Copiado!';
        btn.classList.add('acao-copiada');
        setTimeout(() => {
            btn.textContent = 'Copiar link avaliação';
            btn.classList.remove('acao-copiada');
        }, 2500);
    }

    function iniciarGraficos() {
        const graficoFaturamento = document.getElementById('graficoFaturamento');
        const graficoCortes = document.getElementById('graficoCortes');
        if (!graficoFaturamento || !graficoCortes || typeof Chart !== 'function') return;

        const nomes = readJsonScript('grafico-nomes-data', []);
        const cortes = readJsonScript('grafico-cortes-data', []);
        const faturamento = readJsonScript('grafico-faturamento-data', []);
        const corDestaque = getComputedStyle(document.documentElement)
            .getPropertyValue('--cor-destaque').trim() || '#ffb74d';

        new Chart(graficoFaturamento, {
            type: 'bar',
            data: {
                labels: nomes,
                datasets: [{
                    label: 'Faturamento (R$)',
                    data: faturamento,
                    backgroundColor: '#81c784',
                    borderRadius: 4,
                }],
            },
            options: {
                plugins: { title: { display: true, text: 'Faturamento por Barbeiro (R$)', color: '#fff' } },
                scales: { y: { ticks: { color: '#bbb' } }, x: { ticks: { color: '#bbb' } } },
            },
        });

        new Chart(graficoCortes, {
            type: 'bar',
            data: {
                labels: nomes,
                datasets: [{
                    label: 'Qtd de Cortes',
                    data: cortes,
                    backgroundColor: corDestaque,
                    borderRadius: 4,
                }],
            },
            options: {
                plugins: { title: { display: true, text: 'Volume de Cortes', color: '#fff' } },
                scales: { y: { ticks: { color: '#bbb', stepSize: 1 } }, x: { ticks: { color: '#bbb' } } },
            },
        });
    }

    async function carregarInsightGemini() {
        const container = document.getElementById('insight-ia');
        if (!container) return;

        const corpo = container.querySelector('[data-insight-corpo]');
        const texto = container.querySelector('[data-insight-texto]');
        const url = container.dataset.url;
        if (!corpo || !texto || !url) return;

        container.setAttribute('aria-busy', 'true');
        corpo.classList.remove('insight-ia-erro', 'insight-ia-pronto');

        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 45000);

        try {
            const response = await fetch(url, {
                method: 'GET',
                credentials: 'same-origin',
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                signal: controller.signal,
            });
            const data = await response.json();
            if (!response.ok || !data.insight) {
                throw new Error(data.erro || 'Resposta invalida do servidor.');
            }

            corpo.classList.add('insight-ia-trocando');
            setTimeout(() => {
                corpo.querySelector('.insight-ia-spinner')?.remove();
                texto.textContent = data.insight;
                corpo.classList.remove('insight-ia-trocando');
                corpo.classList.add('insight-ia-pronto');
            }, 180);
        } catch (erro) {
            corpo.querySelector('.insight-ia-spinner')?.remove();
            texto.textContent = erro.name === 'AbortError'
                ? 'A análise está demorando mais que o esperado. Recarregue a página para tentar novamente.'
                : 'Não foi possível carregar os insights agora. Tente novamente em instantes.';
            corpo.classList.add('insight-ia-erro');
        } finally {
            clearTimeout(timeout);
            container.setAttribute('aria-busy', 'false');
        }
    }

    window.copiarAvaliacao = copiarAvaliacao;
    document.addEventListener('DOMContentLoaded', iniciarGraficos);
    document.addEventListener('DOMContentLoaded', carregarInsightGemini);
})();
