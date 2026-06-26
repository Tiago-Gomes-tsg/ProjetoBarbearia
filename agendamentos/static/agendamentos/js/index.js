(function () {
    const jsConfig = document.getElementById('agendamento-js-config');
    const appConfig = jsConfig ? jsConfig.dataset : {};
    const listaEsperaAtiva = appConfig.listaEsperaAtiva !== '0';
    // Dados renderizados pelo Django evitam novas consultas durante a escolha do horario.
    const horariosOcupados = readJsonScript('horarios-ocupados-data', []);
    const barbeirosConfig = readJsonScript('barbeiros-config-data', {});

    let flatpickrInstance = null;
    let etapaAtual = 1;

    function prefereMovimentoReduzido() {
        return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    }

    function reiniciarAnimacao(elemento, classe) {
        if (!elemento || prefereMovimentoReduzido()) return;
        elemento.classList.remove(classe);
        void elemento.offsetWidth;
        elemento.classList.add(classe);
    }

    function pulsarTema() {
        if (prefereMovimentoReduzido()) return;
        document.body.classList.remove('is-theme-switching');
        void document.body.offsetWidth;
        document.body.classList.add('is-theme-switching');
        setTimeout(() => document.body.classList.remove('is-theme-switching'), 320);
    }

    function readJsonScript(id, fallback) {
        const el = document.getElementById(id);
        if (!el) return fallback;
        try {
            return JSON.parse(el.textContent);
        } catch (e) {
            return fallback;
        }
    }

    function getCsrfToken() {
        const input = document.querySelector('[name=csrfmiddlewaretoken]');
        return input ? input.value : '';
    }

    function toggleAvisoIndex() {
        const corpo = document.getElementById('corpo-aviso');
        const seta = document.getElementById('seta-aviso');
        if (!corpo || !seta) return;

        const aberto = corpo.style.display !== 'none' && corpo.style.display !== '';
        if (aberto) {
            corpo.classList.remove('aviso-corpo-aberto');
            corpo.style.display = 'none';
        } else {
            corpo.style.display = 'block';
            reiniciarAnimacao(corpo, 'aviso-corpo-aberto');
        }
        seta.style.transform = aberto ? 'rotate(0deg)' : 'rotate(90deg)';
        const cabecalho = document.querySelector('#aviso-notificacao [aria-expanded]');
        if (cabecalho) cabecalho.setAttribute('aria-expanded', aberto ? 'false' : 'true');
    }

    function gerarHorarios(horaInicioStr, horaFimStr, intervaloMinutos, pausaInicio, pausaFim) {
        // Slots respeitam expediente, intervalo entre cortes e pausa cadastrada no painel.
        const horarios = [];
        const toMin = (s) => {
            const partes = s.split(':').map(Number);
            return partes[0] * 60 + partes[1];
        };

        let atual = toMin(horaInicioStr);
        const fim = toMin(horaFimStr);
        const pIni = pausaInicio ? toMin(pausaInicio) : null;
        const pFim = pausaFim ? toMin(pausaFim) : null;

        while (atual < fim) {
            if (pIni !== null && pFim !== null && atual >= pIni && atual < pFim) {
                atual += intervaloMinutos;
                continue;
            }
            const h = String(Math.floor(atual / 60)).padStart(2, '0');
            const m = String(atual % 60).padStart(2, '0');
            horarios.push(`${h}:${m}`);
            atual += intervaloMinutos;
        }

        return horarios;
    }

    function formatarTelefone(digitos) {
        const valor = digitos.slice(0, 11);
        if (valor.length <= 2) return valor.length ? `(${valor}` : '';
        if (valor.length <= 6) return `(${valor.slice(0, 2)}) ${valor.slice(2)}`;
        if (valor.length <= 10) return `(${valor.slice(0, 2)}) ${valor.slice(2, 6)}-${valor.slice(6)}`;
        return `(${valor.slice(0, 2)}) ${valor.slice(2, 7)}-${valor.slice(7)}`;
    }

    function mascaraTelefone(input) {
        const posicaoOriginal = input.selectionStart || input.value.length;
        const digitosAntesCursor = input.value.slice(0, posicaoOriginal).replace(/\D/g, '').length;
        const formatado = formatarTelefone(input.value.replace(/\D/g, ''));
        input.value = formatado;

        let novaPosicao = formatado.length;
        let vistos = 0;
        for (let i = 0; i < formatado.length; i += 1) {
            if (/\d/.test(formatado[i])) vistos += 1;
            if (vistos >= digitosAntesCursor) {
                novaPosicao = i + 1;
                break;
            }
        }
        window.requestAnimationFrame(() => input.setSelectionRange(novaPosicao, novaPosicao));
    }

    function alternarListaEspera(visivel) {
        if (!listaEsperaAtiva) visivel = false;
        const box = document.getElementById('box-lista-espera');
        const feedback = document.getElementById('feedback-lista-espera');
        if (!box) return;
        box.classList.toggle('classe-escondida', !visivel);
        if (feedback && visivel) {
            feedback.textContent = 'Nenhum horário livre para este dia. Entre na lista de espera para receber contato se surgir uma vaga.';
            feedback.style.color = 'var(--cor-texto-suave)';
        }
    }

    function atualizarDependenciasBarbeiro() {
        const barbeiroSelecionado = document.querySelector('input[name="barbeiro"]:checked');
        if (barbeiroSelecionado && flatpickrInstance) {
            const config = barbeirosConfig[barbeiroSelecionado.value];
            if (config) {
                flatpickrInstance.set('disable', [
                    function (date) {
                        if (!config.dias_trabalho || config.dias_trabalho.length === 0) return false;
                        return !config.dias_trabalho.includes(date.getDay());
                    },
                    ...(config.datas_bloqueadas || []).map((d) => new Date(`${d}T00:00:00`)),
                ]);
                flatpickrInstance.clear();
            }
        }

        filtrarHorarios();
        filtrarCortesPorBarbeiro();
        atualizarVisibilidadeCupom();
    }

    function filtrarCortesPorBarbeiro() {
        const barbeiroSelecionado = document.querySelector('input[name="barbeiro"]:checked');
        const cardsCorte = document.querySelectorAll('.card-corte');
        if (!barbeiroSelecionado) return;

        const idBarbeiroAlvo = String(barbeiroSelecionado.value);
        cardsCorte.forEach((card) => {
            const idBarbeiroDoCard = String(card.getAttribute('data-barbeiro-id'));
            const inputRadio = card.querySelector('input[type="radio"]');
            if (!inputRadio) return;

            if (idBarbeiroDoCard === idBarbeiroAlvo) {
                card.classList.remove('classe-escondida');
                inputRadio.disabled = false;
            } else {
                card.classList.add('classe-escondida');
                inputRadio.disabled = true;
                inputRadio.checked = false;
            }
        });
    }

    function atualizarVisibilidadeCupom() {
        const secaoCupom = document.getElementById('secao-cupom');
        if (!secaoCupom) return;

        const barbeiroSelecionado = document.querySelector('input[name="barbeiro"]:checked');
        const config = barbeiroSelecionado ? barbeirosConfig[barbeiroSelecionado.value] : null;
        const deveExibir = !!(config && config.exibir_cupons_publico);

        if (deveExibir) {
            secaoCupom.style.display = 'block';
            reiniciarAnimacao(secaoCupom, 'cupom-visivel');
        } else {
            secaoCupom.classList.remove('cupom-visivel');
            secaoCupom.style.display = 'none';
        }
        if (!deveExibir) removerCupom();
    }

    function filtrarHorarios() {
        // Remove horarios passados, ocupados ou fora da regra do barbeiro selecionado.
        const selectHorario = document.getElementById('horario');
        if (!selectHorario) return;

        selectHorario.innerHTML = '<option value="" disabled selected>Selecione um horário</option>';
        const barbeiroSelecionado = document.querySelector('input[name="barbeiro"]:checked');

        if (!flatpickrInstance || flatpickrInstance.selectedDates.length === 0 || !barbeiroSelecionado) {
            selectHorario.disabled = true;
            selectHorario.options[0].text = 'Selecione a data primeiro';
            alternarListaEspera(false);
            return;
        }

        const config = barbeirosConfig[barbeiroSelecionado.value];
        if (!config) return;

        const horariosGerados = gerarHorarios(
            config.hora_inicio,
            config.hora_fim,
            config.intervalo_minutos,
            config.pausa_inicio,
            config.pausa_fim
        );

        selectHorario.disabled = false;

        const dataSelecionadaObjeto = flatpickrInstance.selectedDates[0];
        const dataSelecionadaStr = flatpickrInstance.formatDate(dataSelecionadaObjeto, 'Y-m-d');
        const hoje = new Date();
        const ehHoje = dataSelecionadaObjeto.toDateString() === hoje.toDateString();
        const tempoMinimoAntecedencia = new Date(hoje.getTime() + 30 * 60 * 1000);

        let temHorarioDisponivel = false;
        horariosGerados.forEach((horario) => {
            let deveBloquear = false;

            if (ehHoje) {
                const partes = horario.split(':').map(Number);
                const dataHoraOpcao = new Date();
                dataHoraOpcao.setHours(partes[0], partes[1], 0, 0);
                if (dataHoraOpcao <= tempoMinimoAntecedencia) deveBloquear = true;
            }

            const jaReservado = horariosOcupados.some((h) => (
                String(h.barbeiro_id) === String(barbeiroSelecionado.value) &&
                h.data === dataSelecionadaStr &&
                h.horario === horario
            ));

            if (jaReservado) deveBloquear = true;
            if (deveBloquear) return;

            const option = document.createElement('option');
            option.value = horario;
            option.textContent = horario;
            selectHorario.appendChild(option);
            temHorarioDisponivel = true;
        });

        if (!temHorarioDisponivel) {
            selectHorario.options[0].text = 'Agenda lotada para este dia';
            selectHorario.disabled = true;
            alternarListaEspera(true);
        } else {
            alternarListaEspera(false);
        }
    }

    async function entrarListaEspera() {
        if (!listaEsperaAtiva) return;
        const nome = document.getElementById('nome').value.trim();
        const telefone = document.getElementById('telefone').value.trim();
        const barbeiroSelecionado = document.querySelector('input[name="barbeiro"]:checked');
        const data = document.getElementById('data').value;
        const feedback = document.getElementById('feedback-lista-espera');

        if (!nome || telefone.length < 14 || !barbeiroSelecionado || !data) {
            if (feedback) {
                feedback.textContent = 'Preencha nome, telefone, barbeiro e data antes de entrar na lista.';
                feedback.style.color = '#ef5350';
            }
            return;
        }

        if (feedback) {
            feedback.textContent = 'Enviando...';
            feedback.style.color = 'var(--cor-destaque)';
        }

        try {
            const response = await fetch(appConfig.listaEsperaUrl || '/lista-espera/entrar/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken(),
                },
                body: JSON.stringify({
                    nome,
                    telefone,
                    barbeiro_id: barbeiroSelecionado.value,
                    data,
                }),
            });
            const dados = await response.json();
            if (feedback) {
                feedback.textContent = dados.mensagem || 'Solicitacao enviada.';
                feedback.style.color = dados.status === 'sucesso' || dados.status === 'ja_cadastrado'
                    ? 'var(--cor-destaque)'
                    : '#ef5350';
            }
        } catch (e) {
            if (feedback) {
                feedback.textContent = 'Nao foi possivel entrar na lista agora. Tente novamente.';
                feedback.style.color = '#ef5350';
            }
        }
    }

    function exibirErroBanner(mensagem) {
        const banner = document.getElementById('banner-erro');
        if (!banner) return;
        banner.innerText = `Atencao: ${mensagem}`;
        banner.classList.remove('classe-escondida');
        reiniciarAnimacao(banner, 'erro-ativo');
        window.scrollTo({ top: 0, behavior: prefereMovimentoReduzido() ? 'auto' : 'smooth' });
    }

    function limparErroBanner() {
        const banner = document.getElementById('banner-erro');
        if (banner) banner.classList.add('classe-escondida');
    }

    function proximaEtapa(numeroDaEtapa) {
        limparErroBanner();
        if (numeroDaEtapa < etapaAtual) {
            mudarAbaVisual(numeroDaEtapa);
            return;
        }

        if (etapaAtual === 1) {
            const nome = document.getElementById('nome').value.trim();
            const telefone = document.getElementById('telefone').value;
            if (nome.length < 3) {
                exibirErroBanner('Por favor, digite seu nome completo.');
                return;
            }
            if (telefone.length < 14) {
                exibirErroBanner('Por favor, informe um WhatsApp valido.');
                return;
            }
        }

        if (etapaAtual === 2 && !document.querySelector('input[name="barbeiro"]:checked')) {
            exibirErroBanner('Selecione um barbeiro.');
            return;
        }

        if (etapaAtual === 3) {
            const data = document.getElementById('data');
            const horario = document.getElementById('horario');
            if (!data.value || !horario.value) {
                exibirErroBanner('Preencha data e horário.');
                return;
            }
        }

        mudarAbaVisual(numeroDaEtapa);
    }

    function mudarAbaVisual(numeroDaEtapa) {
        document.querySelectorAll('.etapa').forEach((aba) => aba.classList.add('classe-escondida'));
        const alvo = document.getElementById(`etapa-${numeroDaEtapa}`);
        if (alvo) {
            alvo.classList.remove('classe-escondida');
            reiniciarAnimacao(alvo, 'etapa-entrando');
        }
        etapaAtual = numeroDaEtapa;
        document.getElementById('form-agendamento')?.scrollIntoView({ block: 'center', behavior: prefereMovimentoReduzido() ? 'auto' : 'smooth' });
    }

    function validarEEnviar() {
        limparErroBanner();
        const corteSelecionado = document.querySelector('input[name="corte"]:checked');
        if (!corteSelecionado) {
            exibirErroBanner('Por favor, selecione qual serviço você deseja realizar.');
            return;
        }
        document.getElementById('form-agendamento').submit();
    }

    function abrirTelaCancelamento() {
        limparErroBanner();
        document.querySelectorAll('.etapa').forEach((aba) => aba.classList.add('classe-escondida'));
        const etapaCancelamento = document.getElementById('etapa-cancelamento');
        etapaCancelamento.classList.remove('classe-escondida');
        reiniciarAnimacao(etapaCancelamento, 'etapa-entrando');
    }

    function voltarParaEtapa1() {
        document.getElementById('etapa-cancelamento').classList.add('classe-escondida');
        const etapaInicial = document.getElementById('etapa-1');
        etapaInicial.classList.remove('classe-escondida');
        reiniciarAnimacao(etapaInicial, 'etapa-entrando');
        etapaAtual = 1;
    }

    function setFeedbackCupom(mensagem, color) {
        const fb = document.getElementById('feedback-cupom');
        if (!fb) return;
        fb.innerHTML = '';
        const span = document.createElement('span');
        span.style.color = color;
        span.textContent = mensagem;
        fb.appendChild(span);
    }

    async function validarCupom() {
        // Validacao fica no servidor para conferir validade e limite de uso do cupom.
        const inputCupom = document.getElementById('input-cupom');
        const fb = document.getElementById('feedback-cupom');
        if (!inputCupom || !fb) return;

        const codigo = inputCupom.value.trim().toUpperCase();
        if (!codigo) {
            fb.innerHTML = '';
            return;
        }

        fb.style.marginTop = '10px';
        setFeedbackCupom('Verificando...', '#555');

        try {
            const response = await fetch(appConfig.validarCupomUrl || '/painel/api/validar-cupom/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken(),
                },
                body: JSON.stringify({ codigo }),
            });
            const dados = await response.json();

            if (dados.valido) {
                const desc = dados.tipo === 'PERCENTUAL'
                    ? `-${dados.valor}% de desconto`
                    : `-R$ ${parseFloat(dados.valor).toFixed(2)} de desconto`;

                document.getElementById('cupom-codigo').value = codigo;
                document.getElementById('cupom-tipo').value = dados.tipo;
                document.getElementById('cupom-valor').value = dados.valor;
                document.getElementById('badge-codigo').textContent = codigo;
                document.getElementById('badge-desconto').textContent = desc;
                document.getElementById('cupom-input-row').style.display = 'none';
                document.getElementById('cupom-aplicado-row').style.display = 'flex';
                const cupomIconSymbol = document.getElementById('cupom-icon-symbol');
                if (cupomIconSymbol) cupomIconSymbol.className = 'svg-icon icon-check';
                document.getElementById('cupom-icon').style.background = '#1b3a1e';
                document.getElementById('cupom-title').textContent = 'Desconto aplicado!';
                document.getElementById('cupom-title').style.color = '#81c784';
                document.getElementById('cupom-sub').textContent = 'sera deduzido no valor final';
                document.getElementById('cupom-wrapper').style.borderColor = '#1b5e20';
                document.getElementById('cupom-wrapper').style.borderStyle = 'solid';
                document.getElementById('cupom-wrapper').style.background = '#0d1f0d';
                const cupomWrapper = document.getElementById('cupom-wrapper');
                reiniciarAnimacao(cupomWrapper, 'cupom-aplicado');
                fb.innerHTML = '';
                fb.style.marginTop = '0';
            } else {
                setFeedbackCupom(dados.mensagem, '#ef5350');
                ['cupom-codigo', 'cupom-tipo', 'cupom-valor'].forEach((id) => {
                    document.getElementById(id).value = '';
                });
            }
        } catch (e) {
            setFeedbackCupom('Erro de conexao. Tente novamente.', '#ef5350');
        }
    }

    function removerCupom() {
        const inputCupom = document.getElementById('input-cupom');
        if (!inputCupom) return;

        inputCupom.value = '';
        ['cupom-codigo', 'cupom-tipo', 'cupom-valor'].forEach((id) => {
            document.getElementById(id).value = '';
        });
        document.getElementById('cupom-input-row').style.display = 'flex';
        document.getElementById('cupom-aplicado-row').style.display = 'none';
        const cupomIconSymbol = document.getElementById('cupom-icon-symbol');
        if (cupomIconSymbol) cupomIconSymbol.className = 'svg-icon icon-ticket';
        document.getElementById('cupom-icon').style.background = '#3a3010';
        document.getElementById('cupom-title').textContent = 'Cupom de desconto';
        document.getElementById('cupom-title').style.color = '#c8a04a';
        document.getElementById('cupom-sub').textContent = 'opcional - insira o codigo e valide';
        document.getElementById('cupom-wrapper').style.borderColor = '#3a3010';
        document.getElementById('cupom-wrapper').style.borderStyle = 'dashed';
        document.getElementById('cupom-wrapper').style.background = '#1a1800';
        document.getElementById('cupom-wrapper').classList.remove('cupom-aplicado');
        document.getElementById('feedback-cupom').innerHTML = '';
        document.getElementById('feedback-cupom').style.marginTop = '0';
    }

    function atualizarBotaoTemaPublico(botao) {
        const temaAtual = document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
        const label = botao.querySelector('[data-public-theme-label]');
        if (label) label.textContent = temaAtual === 'light' ? 'Modo escuro' : 'Modo claro';
        botao.setAttribute('aria-label', temaAtual === 'light' ? 'Ativar modo escuro' : 'Ativar modo claro');
        botao.dataset.activeTheme = temaAtual;
    }

    function iniciarTemaPublico() {
        // Preferencia do cliente fica no navegador sem alterar o tema padrao da barbearia.
        document.querySelectorAll('[data-public-theme-toggle]').forEach((botao) => {
            atualizarBotaoTemaPublico(botao);
            botao.addEventListener('click', () => {
                const proximoTema = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
                document.documentElement.dataset.theme = proximoTema;
                try {
                    localStorage.setItem(botao.dataset.themeKey || 'barbearia:global:agendamento-theme', proximoTema);
                } catch (e) {}
                atualizarBotaoTemaPublico(botao);
                pulsarTema();
            });
        });
    }

    function iniciarFlatpickr() {
        const campoData = document.getElementById('data');
        if (!campoData || typeof flatpickr !== 'function') return;

        flatpickrInstance = flatpickr('#data', {
            locale: 'pt',
            dateFormat: 'Y-m-d',
            altInput: true,
            altFormat: 'd/m/Y',
            minDate: 'today',
            maxDate: new Date().fp_incr(31),
            onChange: function () {
                filtrarHorarios();
            },
        });
    }

    function iniciar() {
        iniciarTemaPublico();
        iniciarFlatpickr();

        document.querySelector('#aviso-notificacao [role="button"]')?.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            event.preventDefault();
            toggleAvisoIndex();
        });

        const erroValidacao = appConfig.erroValidacao || '';
        if (erroValidacao) exibirErroBanner(erroValidacao);

        if (window.history.replaceState) {
            window.history.replaceState(null, null, window.location.href);
        }
    }

    window.toggleAvisoIndex = toggleAvisoIndex;
    window.mascaraTelefone = mascaraTelefone;
    window.atualizarDependenciasBarbeiro = atualizarDependenciasBarbeiro;
    window.proximaEtapa = proximaEtapa;
    window.validarEEnviar = validarEEnviar;
    window.abrirTelaCancelamento = abrirTelaCancelamento;
    window.voltarParaEtapa1 = voltarParaEtapa1;
    window.entrarListaEspera = entrarListaEspera;
    window.validarCupom = validarCupom;
    window.removerCupom = removerCupom;

    document.addEventListener('DOMContentLoaded', iniciar);
})();
