from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
import json
import os
from types import SimpleNamespace
from unittest.mock import call, patch
import zipfile

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from agendamentos.images import sanitizar_imagem_upload
from agendamentos.models import (
    AceiteTermos,
    Agendamento,
    AssinaturaCliente,
    AvaliacaoAtendimento,
    Barbeiro,
    BloqueioAgenda,
    Cliente,
    ConfigLoyalty,
    ConfiguracaoBarbearia,
    Corte,
    CupomDesconto,
    Empresa,
    EntradaListaEspera,
    ItemEstoque,
    LancamentoComissao,
    MetaBarbeiro,
    MembroEmpresa,
    PlanoMensal,
    SaldoLoyalty,
    TransacaoFinanceira,
    TransacaoLoyalty,
    UserSession,
    upload_foto_barbeiro,
    upload_logo,
)
from agendamentos.tenancy import empresa_context
from agendamentos.views.dashboard_views import (
    MAX_LOGO_IMAGE_SIZE,
    _clientes_com_aniversario_entre,
    _gerar_insight_gemini,
    _imagem_upload_valida,
    _montar_prompt_insights,
    _solicitar_insight_gemini,
    _somar_meses,
)


class FrontendSmokeTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(nome='Empresa Teste', slug='empresa-teste')
        self._empresa_context = empresa_context(self.empresa)
        self._empresa_context.__enter__()
        self.addCleanup(self._empresa_context.__exit__, None, None, None)
        self.user = User.objects.create_superuser(
            username='admin',
            email='admin@example.com',
            password='senha-teste-123',
        )
        self.client.force_login(self.user)
        ConfiguracaoBarbearia.objects.get_or_create(
            empresa=self.empresa,
            defaults={'nome_barbearia': self.empresa.nome},
        )
        cache.clear()

    def aceitar_termos_para(self, usuario):
        AceiteTermos.objects.get_or_create(
            usuario=usuario,
            versao_termos='1.0',
            versao_privacidade='1.0',
        )

    def test_public_index_renders(self):
        self.client.logout()
        response = self.client.get(reverse('index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'form-agendamento')

    def test_configuracoes_renders(self):
        response = self.client.get(reverse('configuracoes_barbearia'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Configurações')
        self.assertContains(response, 'tema_painel_padrao')
        self.assertContains(response, 'cor_destaque_agendamento')
        self.assertContains(response, 'agendamento_tema_claro_fundo')
        self.assertContains(response, 'aviso_imagem')
        self.assertContains(response, 'foto_fundo_publico')
        self.assertContains(response, 'exibir_lista_espera_publica')

    def test_configuracoes_preserva_cores_quando_post_nao_envia_tema(self):
        cfg = ConfiguracaoBarbearia.objects.get(empresa=self.empresa)
        cfg.cor_destaque = '#123456'
        cfg.tema_escuro_fundo = '#112233'
        cfg.agendamento_tema_claro_fundo = '#aabbcc'
        cfg.save()

        response = self.client.post(reverse('configuracoes_barbearia'), {
            'nome_barbearia': 'Empresa Teste Atualizada',
            'slogan': 'Slogan novo',
            'cep': '',
            'logradouro': '',
            'bairro': '',
            'cidade': '',
            'uf': '',
            'numero': '',
            'complemento': '',
            'ddd': '',
            'latitude': '',
            'longitude': '',
        })

        cfg.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(cfg.cor_destaque, '#123456')
        self.assertEqual(cfg.tema_escuro_fundo, '#112233')
        self.assertEqual(cfg.agendamento_tema_claro_fundo, '#aabbcc')

    def test_lista_espera_publica_pode_ser_desativada_por_tenant(self):
        cfg = ConfiguracaoBarbearia.objects.get(empresa=self.empresa)
        cfg.exibir_lista_espera_publica = False
        cfg.save()

        self.client.logout()
        index = self.client.get(reverse('index'))
        response = self.client.post(
            reverse('entrar_lista_espera'),
            data=json.dumps({'nome': 'Cliente', 'telefone': '(11) 99999-0000'}),
            content_type='application/json',
        )

        self.assertNotContains(index, 'box-lista-espera')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['status'], 'erro')

    def test_upload_exige_imagem_decodificavel_e_remove_metadados(self):
        falso = SimpleUploadedFile(
            'arquivo.png',
            b'\x89PNG\r\n\x1a\nconteudo-invalido',
            content_type='image/png',
        )
        valido, _ = _imagem_upload_valida(falso, MAX_LOGO_IMAGE_SIZE)
        self.assertFalse(valido)

        buffer = BytesIO()
        Image.new('RGB', (80, 60), '#336699').save(buffer, format='PNG')
        real = SimpleUploadedFile('logo.png', buffer.getvalue(), content_type='image/png')
        valido, erro = _imagem_upload_valida(real, MAX_LOGO_IMAGE_SIZE)
        self.assertTrue(valido, erro)
        processado = sanitizar_imagem_upload(real, tamanho_maximo=(40, 40))
        with Image.open(processado) as imagem:
            self.assertEqual(imagem.format, 'WEBP')
            self.assertLessEqual(max(imagem.size), 40)
            self.assertFalse(imagem.getexif())

    def test_equipe_renders(self):
        response = self.client.get(reverse('lista_equipe'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'foto_perfil')
        self.assertContains(response, 'senha_confirmacao')

    def test_contratacao_exige_confirmacao_de_senha(self):
        response = self.client.post(reverse('adicionar_membro'), {
            'nome': 'Novo Profissional',
            'username': 'novo-profissional',
            'email': 'novo@example.com',
            'senha': 'senha-teste-123',
            'senha_confirmacao': 'senha-diferente',
        })

        self.assertEqual(response.status_code, 302)
        self.assertFalse(User.objects.filter(username='novo-profissional').exists())

    def test_dono_redefine_senha_pela_edicao_do_barbeiro(self):
        user = User.objects.create_user(username='barbeiro-senha', password='senha-antiga-123')
        membro = Barbeiro.objects.create(usuario=user, nome='Barbeiro Senha')

        response = self.client.post(reverse('editar_membro', args=[membro.id]), {
            'nome': 'Barbeiro Senha',
            'email': 'barbeiro@example.com',
            'nova_senha': 'senha-nova-123',
            'nova_senha_confirmacao': 'senha-nova-123',
            'hora_inicio': '09:00',
            'hora_fim': '18:00',
            'intervalo_minutos': '30',
            'tipo_remuneracao': 'COMISSAO',
            'salario_fixo': '0.00',
            'porcentagem_comissao': '30.00',
            't_segunda': 'on',
        })

        user.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(user.check_password('senha-nova-123'))
        self.assertEqual(user.email, 'barbeiro@example.com')

    def test_avaliacoes_renders(self):
        response = self.client.get(reverse('lista_avaliacoes'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'filtro-data-inicio')

    def test_public_index_uses_public_theme_palette(self):
        cfg = ConfiguracaoBarbearia.objects.get(empresa=self.empresa)
        cfg.tema_claro_fundo = '#112233'
        cfg.agendamento_tema_claro_fundo = '#aabbcc'
        cfg.save()

        self.client.logout()
        response = self.client.get(reverse('index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '--theme-light-fundo: #aabbcc')
        self.assertNotContains(response, '--theme-light-fundo: #112233')

    def test_public_index_uses_background_on_body_and_shop_photo_on_hero(self):
        cfg = ConfiguracaoBarbearia.objects.get(empresa=self.empresa)
        cfg.foto_barbearia.name = 'tenants/teste/branding/capas/fachada.webp'
        cfg.foto_fundo_publico.name = 'tenants/teste/branding/backgrounds/fundo.webp'
        cfg.save()

        self.client.logout()
        response = self.client.get(reverse('index'))

        self.assertContains(response, 'class="public-page-has-bg"')
        self.assertContains(response, "--public-page-bg: url('/media/tenants/teste/branding/backgrounds/fundo.webp')")
        self.assertContains(response, "url('/media/tenants/teste/branding/capas/fachada.webp')")

    def test_public_notice_respects_date_window(self):
        cfg = ConfiguracaoBarbearia.objects.get(empresa=self.empresa)
        cfg.aviso_ativo = True
        cfg.aviso_titulo = 'Evento especial'
        cfg.aviso_mensagem = 'Mensagem sazonal para clientes.'
        cfg.aviso_data_inicio = date.today() + timedelta(days=2)
        cfg.aviso_data_fim = date.today() + timedelta(days=4)
        cfg.save()

        self.client.logout()
        response = self.client.get(reverse('index'))
        self.assertNotContains(response, 'Mensagem sazonal para clientes.')

        cfg.aviso_data_inicio = date.today()
        cfg.save()
        response = self.client.get(reverse('index'))
        self.assertContains(response, 'Mensagem sazonal para clientes.')
        self.assertContains(response, 'aria-expanded="false"')
        self.assertContains(response, 'id="corpo-aviso" style="display:none;')

    def test_frontend_assets_are_local_not_cdn(self):
        self.client.logout()
        public_response = self.client.get(reverse('index'))
        self.assertEqual(public_response.status_code, 200)
        self.assertContains(public_response, 'vendor/flatpickr/flatpickr.min.js')
        self.assertNotContains(public_response, 'cdn.jsdelivr.net')

        self.client.force_login(self.user)
        painel_response = self.client.get(reverse('painel_home'))
        self.assertEqual(painel_response.status_code, 200)
        self.assertContains(painel_response, 'vendor/chartjs/chart.umd.min.js')
        self.assertNotContains(painel_response, 'cdn.jsdelivr.net')

    def test_painel_renderiza_container_assincrono_de_insights(self):
        response = self.client.get(reverse('painel_home'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="insight-ia"')
        self.assertContains(response, reverse('api_insights_gemini'))
        self.assertContains(response, 'Carregando insights inteligentes com IA')

    @patch('agendamentos.views.dashboard_views._gerar_insight_gemini')
    def test_api_insights_agrega_mes_atual_e_retorna_texto(self, gerar_insight):
        user_barbeiro = User.objects.create_user(username='barbeiro-ia', password='senha-teste-123')
        barbeiro = Barbeiro.objects.create(usuario=user_barbeiro, nome='Barbeiro Destaque')
        corte = Corte.objects.create(barbeiro=barbeiro, nome='Corte Premium', preco=80)
        cliente_um = Cliente.objects.create(nome='Cliente IA Um', telefone='(11) 90000-0801')
        cliente_dois = Cliente.objects.create(nome='Cliente IA Dois', telefone='(11) 90000-0802')
        hoje = date.today()

        agendamento_com_entrada = Agendamento.objects.create(
            cliente=cliente_um,
            barbeiro=barbeiro,
            corte=corte,
            data=hoje,
            horario='09:00',
            status='concluido',
        )
        Agendamento.objects.create(
            cliente=cliente_dois,
            barbeiro=barbeiro,
            corte=corte,
            data=hoje,
            horario='10:00',
            status='concluido',
            valor_final=Decimal('70.00'),
        )
        TransacaoFinanceira.objects.create(
            tipo='ENTRADA',
            categoria='SERVICO',
            valor=Decimal('80.00'),
            data=hoje,
            agendamento=agendamento_com_entrada,
            barbeiro=barbeiro,
        )
        TransacaoFinanceira.objects.create(
            tipo='ENTRADA',
            categoria='PRODUTO',
            valor=Decimal('20.00'),
            data=hoje,
        )
        gerar_insight.return_value = 'O mes ganhou ritmo e revelou uma boa oportunidade.'

        response = self.client.get(reverse('api_insights_gemini'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'insight': 'O mes ganhou ritmo e revelou uma boa oportunidade.',
        })
        prompt = gerar_insight.call_args.args[0]
        self.assertIn('R$ 170,00', prompt)
        self.assertIn('"atendimentos":2', prompt)
        self.assertIn('Profissional destaque', prompt)
        self.assertIn('Corte Premium', prompt)
        self.assertIn('gestor do tenant', prompt)
        self.assertNotIn('Barbeiro Destaque', prompt)
        self.assertNotIn('Administrador', prompt)
        self.assertNotIn('Cliente IA Um', prompt)

        response_em_cache = self.client.get(reverse('api_insights_gemini'))
        self.assertEqual(response_em_cache.json(), response.json())
        gerar_insight.assert_called_once()

    def test_prompt_de_insights_inclui_evento_comercial_apenas_quando_proximo(self):
        dados = {
            'periodo': '01/07/2026 a 25/07/2026',
            'faturamento_total': Decimal('1000.00'),
            'ticket_medio_servicos': Decimal('50.00'),
            'total_agendamentos': 20,
            'total_cancelados': 1,
            'taxa_cancelamento': Decimal('4.8'),
            'barbeiro_destaque': 'Profissional destaque',
            'atendimentos_barbeiro_destaque': 10,
            'servico_destaque': 'Corte',
            'atendimentos_servico_destaque': 12,
        }

        with patch('agendamentos.views.dashboard_views.timezone.localdate', return_value=date(2026, 7, 25)):
            prompt = _montar_prompt_insights(dados, 'gestor do tenant', 'dono')
        with patch('agendamentos.views.dashboard_views.timezone.localdate', return_value=date(2026, 3, 1)):
            prompt_sem_evento = _montar_prompt_insights(dados, 'gestor do tenant', 'dono')

        self.assertIn('Dia dos Pais', prompt)
        self.assertIn('evento_comercial_proximo', prompt)
        self.assertNotIn('"evento_comercial_proximo":', prompt_sem_evento)

    def test_barbeiro_comum_nao_ve_nem_acessa_insights(self):
        usuario = User.objects.create_user(username='barbeiro-sem-insight', password='senha-teste-123')
        Barbeiro.objects.create(usuario=usuario, nome='Barbeiro sem Insight')
        self.aceitar_termos_para(usuario)
        self.client.force_login(usuario)

        painel = self.client.get(reverse('painel_home'))
        api = self.client.get(reverse('api_insights_gemini'))

        self.assertEqual(painel.status_code, 200)
        self.assertNotContains(painel, 'id="insight-ia"')
        self.assertEqual(api.status_code, 403)

    @patch('agendamentos.views.dashboard_views._gerar_insight_gemini')
    def test_dono_e_gerente_veem_e_acessam_insights(self, gerar_insight):
        gerar_insight.return_value = 'Insight permitido para a gestao.'

        for indice, permissoes in enumerate(({'is_dono': True}, {'is_gerente': True}), start=1):
            usuario = User.objects.create_user(
                username=f'gestor-insight-{indice}',
                password='senha-teste-123',
            )
            Barbeiro.objects.create(
                usuario=usuario,
                nome=f'Gestor Insight {indice}',
                telefone='(11) 98888-0000',
                **permissoes,
            )
            self.aceitar_termos_para(usuario)
            self.client.force_login(usuario)

            painel = self.client.get(reverse('painel_home'))
            api = self.client.get(reverse('api_insights_gemini'))

            self.assertContains(painel, 'id="insight-ia"')
            self.assertEqual(api.status_code, 200)

    @patch.dict(os.environ, {
        'GEMINI_API_KEY': 'chave-principal-teste',
        'GEMINI_API_KEY_RESERVA': 'chave-reserva-teste',
    })
    @patch('agendamentos.views.dashboard_views._solicitar_insight_gemini')
    def test_gemini_usa_reserva_somente_apos_falha_da_principal(self, solicitar):
        solicitar.side_effect = [RuntimeError('falha principal'), 'Insight completo da reserva.']

        insight = _gerar_insight_gemini('prompt de teste')

        self.assertEqual(insight, 'Insight completo da reserva.')
        self.assertEqual(solicitar.call_args_list, [
            call('prompt de teste', 'chave-principal-teste'),
            call('prompt de teste', 'chave-reserva-teste'),
        ])

    @patch('google.genai.Client')
    def test_gemini_rejeita_resposta_interrompida_por_limite(self, client_class):
        from google.genai import types

        resposta = SimpleNamespace(
            candidates=[SimpleNamespace(finish_reason=types.FinishReason.MAX_TOKENS)],
            text='Este texto terminou no meio',
        )
        client_class.return_value.models.generate_content.return_value = resposta

        with self.assertRaisesRegex(RuntimeError, 'MAX_TOKENS'):
            _solicitar_insight_gemini('prompt de teste', 'chave-ficticia')

        config = client_class.return_value.models.generate_content.call_args.kwargs['config']
        self.assertEqual(config.max_output_tokens, 180)
        self.assertEqual(config.thinking_config.thinking_budget, 0)

    @patch('google.genai.Client')
    def test_gemini_normaliza_resposta_em_paragrafo_unico(self, client_class):
        from google.genai import types

        client_class.return_value.models.generate_content.return_value = SimpleNamespace(
            candidates=[SimpleNamespace(finish_reason=types.FinishReason.STOP)],
            text='O mes comecou bem.\n\nAproveite o ritmo com uma acao simples.',
        )

        insight = _solicitar_insight_gemini('prompt de teste', 'chave-ficticia')

        self.assertEqual(
            insight,
            'O mes comecou bem. Aproveite o ritmo com uma acao simples.',
        )

    def test_api_insights_exige_autenticacao(self):
        self.client.logout()

        response = self.client.get(reverse('api_insights_gemini'))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_painel_filtra_desempenho_por_periodo_e_exporta_xlsx(self):
        user_um = User.objects.create_user(username='barbeiro-um', password='senha-teste-123')
        user_dois = User.objects.create_user(username='barbeiro-dois', password='senha-teste-123')
        barbeiro_um = Barbeiro.objects.create(usuario=user_um, nome='Barbeiro Um')
        barbeiro_dois = Barbeiro.objects.create(usuario=user_dois, nome='Barbeiro Dois')
        corte_um = Corte.objects.create(barbeiro=barbeiro_um, nome='Corte Um', preco=70)
        corte_dois = Corte.objects.create(barbeiro=barbeiro_dois, nome='Corte Dois', preco=90)
        cliente_junho = Cliente.objects.create(nome='Cliente Junho', telefone='(11) 90000-0201')
        cliente_maio = Cliente.objects.create(nome='Cliente Maio', telefone='(11) 90000-0202')

        Agendamento.objects.create(
            cliente=cliente_junho,
            barbeiro=barbeiro_um,
            corte=corte_um,
            data=date(2026, 6, 5),
            horario='09:00',
            status='concluido',
        )
        Agendamento.objects.create(
            cliente=cliente_junho,
            barbeiro=barbeiro_dois,
            corte=corte_dois,
            data=date(2026, 6, 7),
            horario='10:00',
            status='concluido',
        )
        Agendamento.objects.create(
            cliente=cliente_maio,
            barbeiro=barbeiro_um,
            corte=corte_um,
            data=date(2026, 5, 20),
            horario='11:00',
            status='concluido',
        )

        filtros = {'data_inicio': '2026-06-01', 'data_fim': '2026-06-30'}
        response = self.client.get(reverse('painel_home'), filtros)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['faturamento_periodo'], Decimal('160'))
        self.assertEqual(response.context['grafico_nomes'], ['Barbeiro Dois', 'Barbeiro Um'])
        self.assertContains(response, 'Exportar Excel')

        exportacao = self.client.get(reverse('exportar_desempenho'), filtros)
        self.assertEqual(exportacao.status_code, 200)
        self.assertEqual(
            exportacao['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

        with zipfile.ZipFile(BytesIO(exportacao.content)) as xlsx:
            self.assertIn('xl/workbook.xml', xlsx.namelist())
            resumo = xlsx.read('xl/worksheets/sheet1.xml').decode('utf-8')
            atendimentos = xlsx.read('xl/worksheets/sheet2.xml').decode('utf-8')

        self.assertIn('Barbeiro Um', resumo)
        self.assertIn('Barbeiro Dois', resumo)
        self.assertIn('Cliente Junho', atendimentos)
        self.assertNotIn('Cliente Maio', atendimentos)

    def test_perfil_cliente_filtra_metricas_por_mes_e_barbeiro(self):
        user_um = User.objects.create_user(username='perfil-barbeiro-um', password='senha-teste-123')
        user_dois = User.objects.create_user(username='perfil-barbeiro-dois', password='senha-teste-123')
        barbeiro_um = Barbeiro.objects.create(usuario=user_um, nome='Perfil Barbeiro Um')
        barbeiro_dois = Barbeiro.objects.create(usuario=user_dois, nome='Perfil Barbeiro Dois')
        corte_um = Corte.objects.create(barbeiro=barbeiro_um, nome='Corte Perfil Um', preco=80)
        corte_dois = Corte.objects.create(barbeiro=barbeiro_dois, nome='Corte Perfil Dois', preco=90)
        cliente = Cliente.objects.create(nome='Cliente Perfil', telefone='(11) 90000-0301')

        agendamento_filtrado = Agendamento.objects.create(
            cliente=cliente,
            barbeiro=barbeiro_um,
            corte=corte_um,
            data=date(2026, 5, 10),
            horario='09:00',
            status='concluido',
        )
        Agendamento.objects.create(
            cliente=cliente,
            barbeiro=barbeiro_dois,
            corte=corte_dois,
            data=date(2026, 5, 12),
            horario='10:00',
            status='concluido',
        )
        Agendamento.objects.create(
            cliente=cliente,
            barbeiro=barbeiro_um,
            corte=corte_um,
            data=date(2026, 6, 12),
            horario='11:00',
            status='concluido',
        )

        response = self.client.get(reverse('perfil_cliente', args=[cliente.id]), {
            'mes': '5',
            'ano': '2026',
            'barbeiro': str(barbeiro_um.id),
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_cortes_periodo'], 1)
        self.assertEqual(response.context['gasto_periodo'], Decimal('80'))
        self.assertEqual(response.context['total_cortes'], 2)
        self.assertEqual(response.context['gasto_total'], Decimal('160'))
        self.assertEqual(list(response.context['historico_pessoal']), [agendamento_filtrado])

    def test_gerente_visualiza_bloqueios_somente_leitura(self):
        gerente_user = User.objects.create_user(username='gerente', password='senha-teste-123')
        Barbeiro.objects.create(
            usuario=gerente_user,
            nome='Gerente QA',
            is_gerente=True,
        )
        alvo_user = User.objects.create_user(username='barbeiro-alvo', password='senha-teste-123')
        alvo = Barbeiro.objects.create(usuario=alvo_user, nome='Barbeiro Alvo')

        BloqueioAgenda.objects.create(
            barbeiro=alvo,
            data_inicio=date.today(),
            data_fim=date.today() + timedelta(days=1),
            motivo='Folga',
        )

        self.aceitar_termos_para(gerente_user)
        self.client.force_login(gerente_user)
        response = self.client.get(reverse('lista_bloqueios'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Somente leitura')
        self.assertNotContains(response, 'Bloquear Per')
        self.assertNotContains(response, 'Remover')

        total_antes = BloqueioAgenda.objects.count()
        self.client.post(reverse('lista_bloqueios'), {
            'acao': 'adicionar',
            'barbeiro_id': alvo.id,
            'data_inicio': date.today().isoformat(),
            'data_fim': date.today().isoformat(),
            'motivo': 'Tentativa gerente',
        })
        self.assertEqual(BloqueioAgenda.objects.count(), total_antes)

    def test_funcionario_nao_acessa_perfil_de_cliente_alheio_por_url(self):
        func_user = User.objects.create_user(username='funcionario', password='senha-teste-123')
        func = Barbeiro.objects.create(usuario=func_user, nome='Funcionario QA')
        outro_user = User.objects.create_user(username='outro-funcionario', password='senha-teste-123')
        outro = Barbeiro.objects.create(usuario=outro_user, nome='Outro Funcionario')
        corte_func = Corte.objects.create(barbeiro=func, nome='Corte Func', preco=50)
        corte_outro = Corte.objects.create(barbeiro=outro, nome='Corte Outro', preco=60)
        cliente_proprio = Cliente.objects.create(nome='Cliente Proprio', telefone='(11) 90000-0001')
        cliente_alheio = Cliente.objects.create(nome='Cliente Alheio', telefone='(11) 90000-0002')

        Agendamento.objects.create(
            cliente=cliente_proprio,
            barbeiro=func,
            corte=corte_func,
            data=date.today(),
            horario='09:00',
        )
        Agendamento.objects.create(
            cliente=cliente_alheio,
            barbeiro=outro,
            corte=corte_outro,
            data=date.today(),
            horario='10:00',
        )

        self.aceitar_termos_para(func_user)
        self.client.force_login(func_user)
        response_proprio = self.client.get(reverse('perfil_cliente', args=[cliente_proprio.id]))
        self.assertEqual(response_proprio.status_code, 200)

        response_alheio = self.client.get(reverse('perfil_cliente', args=[cliente_alheio.id]))
        self.assertEqual(response_alheio.status_code, 302)
        self.assertIn(reverse('historico'), response_alheio['Location'])

    def test_gerente_filtra_historico_por_barbeiro(self):
        gerente_user = User.objects.create_user(username='gerente-historico', password='senha-teste-123')
        Barbeiro.objects.create(usuario=gerente_user, nome='Gerente Historico', is_gerente=True)
        user_um = User.objects.create_user(username='hist-um', password='senha-teste-123')
        user_dois = User.objects.create_user(username='hist-dois', password='senha-teste-123')
        barbeiro_um = Barbeiro.objects.create(usuario=user_um, nome='Historico Um')
        barbeiro_dois = Barbeiro.objects.create(usuario=user_dois, nome='Historico Dois')
        corte_um = Corte.objects.create(barbeiro=barbeiro_um, nome='Corte Um', preco=50)
        corte_dois = Corte.objects.create(barbeiro=barbeiro_dois, nome='Corte Dois', preco=70)
        cliente_um = Cliente.objects.create(nome='Cliente Um', telefone='(11) 90000-0401')
        cliente_dois = Cliente.objects.create(nome='Cliente Dois', telefone='(11) 90000-0402')

        agendamento_um = Agendamento.objects.create(
            cliente=cliente_um, barbeiro=barbeiro_um, corte=corte_um,
            data=date.today(), horario='09:00'
        )
        Agendamento.objects.create(
            cliente=cliente_dois, barbeiro=barbeiro_dois, corte=corte_dois,
            data=date.today(), horario='10:00'
        )

        self.aceitar_termos_para(gerente_user)
        self.client.force_login(gerente_user)
        response = self.client.get(reverse('historico'), {'barbeiro': str(barbeiro_um.id)})

        self.assertEqual(response.status_code, 200)
        self.assertIn(barbeiro_um, list(response.context['barbeiros']))
        self.assertEqual(list(response.context['page_obj']), [agendamento_um])

    def test_horario_manual_invalido_nao_cria_agendamento_publico(self):
        user = User.objects.create_user(username='barbeiro-horario', password='senha-teste-123')
        barbeiro = Barbeiro.objects.create(usuario=user, nome='Barbeiro Horario')
        corte = Corte.objects.create(barbeiro=barbeiro, nome='Corte Horario', preco=50)
        data_agendada = date.today() + timedelta(days=1)
        while data_agendada.weekday() == 6:
            data_agendada += timedelta(days=1)

        response = self.client.post(reverse('index'), {
            'nome': 'Cliente Horario',
            'telefone': '(11) 98888-0400',
            'barbeiro': str(barbeiro.id),
            'corte': str(corte.id),
            'data': data_agendada.isoformat(),
            'horario': '03:00',
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Agendamento.objects.filter(cliente__telefone='(11) 98888-0400').exists())
        self.assertContains(response, 'Horário inválido')

    def test_cupom_persiste_e_financeiro_usa_valor_final(self):
        user = User.objects.create_user(username='barbeiro-cupom', password='senha-teste-123')
        barbeiro = Barbeiro.objects.create(
            usuario=user,
            nome='Barbeiro Cupom',
            exibir_cupons_publico=True,
            porcentagem_comissao=Decimal('30.00'),
        )
        corte = Corte.objects.create(barbeiro=barbeiro, nome='Corte Cupom', preco=Decimal('100.00'))
        cupom = CupomDesconto.objects.create(
            codigo='QA20',
            tipo='FIXO',
            valor_desconto=Decimal('20.00'),
            validade=date.today() + timedelta(days=5),
            usos_maximo=1,
        )
        data_agendada = date.today() + timedelta(days=1)
        while data_agendada.weekday() == 6:
            data_agendada += timedelta(days=1)

        self.client.post(reverse('index'), {
            'nome': 'Cliente Cupom',
            'telefone': '(11) 98888-0500',
            'barbeiro': str(barbeiro.id),
            'corte': str(corte.id),
            'data': data_agendada.isoformat(),
            'horario': '09:00',
            'cupom_codigo': 'QA20',
        })

        agendamento = Agendamento.objects.get(cliente__telefone='(11) 98888-0500')
        cupom.refresh_from_db()
        self.assertEqual(agendamento.cupom_desconto, cupom)
        self.assertEqual(agendamento.valor_original, Decimal('100.00'))
        self.assertEqual(agendamento.desconto_aplicado, Decimal('20.00'))
        self.assertEqual(agendamento.valor_final, Decimal('80.00'))
        self.assertEqual(cupom.usos_realizados, 1)

        self.client.force_login(self.user)
        self.client.post(reverse('alterar_status', args=[agendamento.id, 'concluido']))

        transacao = TransacaoFinanceira.objects.get(agendamento=agendamento, categoria='SERVICO')
        comissao = LancamentoComissao.objects.get(agendamento=agendamento)
        self.assertEqual(transacao.valor, Decimal('80.00'))
        self.assertEqual(comissao.valor_servico, Decimal('80.00'))
        self.assertEqual(comissao.valor_comissao, Decimal('24.00'))

    def test_assinatura_com_desconto_total_nao_lanca_servico_no_caixa(self):
        user = User.objects.create_user(username='barbeiro-plano-total', password='senha-teste-123')
        barbeiro = Barbeiro.objects.create(
            usuario=user,
            nome='Barbeiro Plano Total',
            porcentagem_comissao=Decimal('30.00'),
        )
        corte = Corte.objects.create(barbeiro=barbeiro, nome='Corte Plano Total', preco=Decimal('100.00'))
        cliente = Cliente.objects.create(nome='Cliente Plano Total', telefone='(11) 98888-0600')
        plano = PlanoMensal.objects.create(
            nome='Plano Ilimitado',
            valor_mensal=Decimal('149.90'),
            desconto_percentual=Decimal('100.00'),
        )
        AssinaturaCliente.objects.create(
            cliente=cliente,
            plano=plano,
            data_inicio=date.today(),
            data_renovacao=date.today() + timedelta(days=30),
            status='ativo',
        )
        agendamento = Agendamento.objects.create(
            cliente=cliente,
            barbeiro=barbeiro,
            corte=corte,
            data=date.today(),
            horario='10:00',
        )

        self.client.force_login(self.user)
        self.client.post(reverse('alterar_status', args=[agendamento.id, 'concluido']))

        agendamento.refresh_from_db()
        self.assertEqual(agendamento.valor_original, Decimal('100.00'))
        self.assertEqual(agendamento.desconto_aplicado, Decimal('100.00'))
        self.assertEqual(agendamento.desconto_assinatura_aplicado, Decimal('100.00'))
        self.assertEqual(agendamento.valor_final, Decimal('0.00'))
        self.assertFalse(
            TransacaoFinanceira.objects.filter(agendamento=agendamento, categoria='SERVICO').exists()
        )
        self.assertFalse(LancamentoComissao.objects.filter(agendamento=agendamento).exists())

    def test_assinatura_com_desconto_parcial_lanca_servico_com_valor_final(self):
        user = User.objects.create_user(username='barbeiro-plano-parcial', password='senha-teste-123')
        barbeiro = Barbeiro.objects.create(
            usuario=user,
            nome='Barbeiro Plano Parcial',
            porcentagem_comissao=Decimal('30.00'),
        )
        corte = Corte.objects.create(barbeiro=barbeiro, nome='Corte Plano Parcial', preco=Decimal('100.00'))
        cliente = Cliente.objects.create(nome='Cliente Plano Parcial', telefone='(11) 98888-0601')
        plano = PlanoMensal.objects.create(
            nome='Plano Meio Corte',
            valor_mensal=Decimal('79.90'),
            desconto_percentual=Decimal('50.00'),
        )
        AssinaturaCliente.objects.create(
            cliente=cliente,
            plano=plano,
            data_inicio=date.today(),
            data_renovacao=date.today() + timedelta(days=30),
            status='ativo',
        )
        agendamento = Agendamento.objects.create(
            cliente=cliente,
            barbeiro=barbeiro,
            corte=corte,
            data=date.today(),
            horario='11:00',
        )

        self.client.force_login(self.user)
        self.client.post(reverse('alterar_status', args=[agendamento.id, 'concluido']))

        agendamento.refresh_from_db()
        transacao = TransacaoFinanceira.objects.get(agendamento=agendamento, categoria='SERVICO')
        comissao = LancamentoComissao.objects.get(agendamento=agendamento)
        self.assertEqual(agendamento.desconto_assinatura_aplicado, Decimal('50.00'))
        self.assertEqual(agendamento.valor_final, Decimal('50.00'))
        self.assertEqual(transacao.valor, Decimal('50.00'))
        self.assertIn('Desconto de assinatura', transacao.descricao)
        self.assertEqual(comissao.valor_servico, Decimal('50.00'))
        self.assertEqual(comissao.valor_comissao, Decimal('15.00'))

    def test_nao_reativa_cancelado_quando_horario_foi_ocupado(self):
        user = User.objects.create_user(username='barbeiro-conflito', password='senha-teste-123')
        barbeiro = Barbeiro.objects.create(usuario=user, nome='Barbeiro Conflito')
        corte = Corte.objects.create(barbeiro=barbeiro, nome='Corte Conflito', preco=50)
        cliente_cancelado = Cliente.objects.create(nome='Cliente Cancelado', telefone='(11) 90000-0601')
        cliente_ativo = Cliente.objects.create(nome='Cliente Ativo', telefone='(11) 90000-0602')
        data_agendada = date.today() + timedelta(days=1)

        agendamento_cancelado = Agendamento.objects.create(
            cliente=cliente_cancelado,
            barbeiro=barbeiro,
            corte=corte,
            data=data_agendada,
            horario='09:00',
            status='cancelado',
        )
        Agendamento.objects.create(
            cliente=cliente_ativo,
            barbeiro=barbeiro,
            corte=corte,
            data=data_agendada,
            horario='09:00',
            status='agendado',
        )

        self.client.force_login(self.user)
        response = self.client.post(
            reverse('mudar_status_historico', args=[agendamento_cancelado.id]),
            {'novo_status': 'agendado'},
            HTTP_REFERER=reverse('historico'),
        )
        agendamento_cancelado.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(agendamento_cancelado.status, 'cancelado')

    def test_limite_de_tres_sessoes_por_usuario(self):
        user = User.objects.create_user(username='multi-sessao', password='senha-teste-123')
        MembroEmpresa.objects.create(
            usuario=user,
            papel=MembroEmpresa.BARBER,
        )

        for _ in range(4):
            c = Client()
            response = c.post(reverse('login'), {
                'username': 'multi-sessao',
                'password': 'senha-teste-123',
            })
            self.assertEqual(response.status_code, 302)

        self.assertEqual(UserSession.objects.filter(usuario=user).count(), 3)

    def test_somar_meses_respeita_fim_de_mes(self):
        self.assertEqual(_somar_meses(date(2026, 1, 31)), date(2026, 2, 28))
        self.assertEqual(_somar_meses(date(2024, 1, 31)), date(2024, 2, 29))
        self.assertEqual(_somar_meses(date(2026, 12, 31)), date(2027, 1, 31))

    def test_aniversariantes_semana_atravessa_mes(self):
        jan = Cliente.objects.create(
            nome='Aniversario Janeiro',
            telefone='(11) 90000-0101',
            data_nascimento=date(1990, 1, 31),
        )
        fev = Cliente.objects.create(
            nome='Aniversario Fevereiro',
            telefone='(11) 90000-0102',
            data_nascimento=date(1990, 2, 2),
        )
        fora = Cliente.objects.create(
            nome='Fora da Janela',
            telefone='(11) 90000-0103',
            data_nascimento=date(1990, 2, 20),
        )

        encontrados = set(_clientes_com_aniversario_entre(
            date(2026, 1, 28),
            date(2026, 2, 4),
        ))

        self.assertIn(jan, encontrados)
        self.assertIn(fev, encontrados)
        self.assertNotIn(fora, encontrados)


@override_settings(
    TENANT_BASE_DOMAIN='localhost',
    TENANT_ALLOW_SINGLE_FALLBACK=False,
    ALLOWED_HOSTS=['.localhost', 'testserver'],
)
class MultiTenantIsolationTests(TestCase):
    """Cada teste mantém duas empresas para detectar vazamentos por regressão."""

    HOST_A = 'empresa-a.localhost'
    HOST_B = 'empresa-b.localhost'

    def setUp(self):
        self.empresa_a = Empresa.objects.create(nome='Empresa A', slug='empresa-a')
        self.empresa_b = Empresa.objects.create(nome='Empresa B', slug='empresa-b')

        ConfiguracaoBarbearia.all_objects.update_or_create(
            empresa=self.empresa_a,
            defaults={'nome_barbearia': 'Marca A', 'cor_destaque': '#112233'},
        )
        ConfiguracaoBarbearia.all_objects.update_or_create(
            empresa=self.empresa_b,
            defaults={'nome_barbearia': 'Marca B', 'cor_destaque': '#aabbcc'},
        )

        self.user_a = User.objects.create_user('owner-a', password='senha-teste-123')
        self.user_b = User.objects.create_user('owner-b', password='senha-teste-123')

        with empresa_context(self.empresa_a):
            self.barbeiro_a = Barbeiro.objects.create(
                usuario=self.user_a,
                nome='Profissional A',
                telefone='(11) 99999-1111',
                is_dono=True,
            )
            self.corte_a = Corte.objects.create(
                barbeiro=self.barbeiro_a,
                nome='Serviço exclusivo A',
                preco=Decimal('70.00'),
            )
            self.cliente_a = Cliente.objects.create(
                nome='Cliente A',
                telefone='(11) 99999-0000',
            )
            self.agendamento_a = Agendamento.objects.create(
                cliente=self.cliente_a,
                barbeiro=self.barbeiro_a,
                corte=self.corte_a,
                data=date.today() + timedelta(days=1),
                horario='10:00',
            )
            self.item_a = ItemEstoque.objects.create(
                nome='Produto A', quantidade_atual=3,
                preco_compra=Decimal('10.00'),
            )
            TransacaoFinanceira.objects.create(
                tipo='ENTRADA', categoria='OUTRAS_RECEITAS',
                valor=Decimal('100.00'), descricao='Receita exclusiva A',
            )
            CupomDesconto.objects.create(
                codigo='PROMO', tipo='FIXO', valor_desconto=Decimal('10.00'),
                validade=date.today() + timedelta(days=10),
            )
            AceiteTermos.objects.create(
                usuario=self.user_a,
                versao_termos='1.0',
                versao_privacidade='1.0',
            )

        with empresa_context(self.empresa_b):
            self.barbeiro_b = Barbeiro.objects.create(
                usuario=self.user_b,
                nome='Profissional B',
                telefone='(21) 99999-2222',
                is_dono=True,
            )
            self.corte_b = Corte.objects.create(
                barbeiro=self.barbeiro_b,
                nome='Serviço secreto B',
                preco=Decimal('90.00'),
            )
            # O mesmo telefone é válido em empresas distintas.
            self.cliente_b = Cliente.objects.create(
                nome='Cliente B',
                telefone='(11) 99999-0000',
            )
            self.agendamento_b = Agendamento.objects.create(
                cliente=self.cliente_b,
                barbeiro=self.barbeiro_b,
                corte=self.corte_b,
                data=date.today() + timedelta(days=1),
                horario='10:00',
            )
            self.item_b = ItemEstoque.objects.create(
                nome='Produto B', quantidade_atual=5,
                preco_compra=Decimal('20.00'),
            )
            TransacaoFinanceira.objects.create(
                tipo='ENTRADA', categoria='OUTRAS_RECEITAS',
                valor=Decimal('200.00'), descricao='Receita secreta B',
            )
            CupomDesconto.objects.create(
                codigo='PROMO', tipo='FIXO', valor_desconto=Decimal('20.00'),
                validade=date.today() + timedelta(days=10),
            )
            AceiteTermos.objects.create(
                usuario=self.user_b,
                versao_termos='1.0',
                versao_privacidade='1.0',
            )
        cache.clear()

    def test_publico_e_branding_sao_isolados_por_subdominio(self):
        resposta_a = self.client.get(reverse('index'), HTTP_HOST=self.HOST_A)
        resposta_b = self.client.get(reverse('index'), HTTP_HOST=self.HOST_B)

        self.assertContains(resposta_a, 'Marca A')
        self.assertContains(resposta_a, 'Serviço exclusivo A')
        self.assertNotContains(resposta_a, 'Marca B')
        self.assertNotContains(resposta_a, 'Serviço secreto B')
        self.assertContains(resposta_b, 'Marca B')
        self.assertContains(resposta_b, 'Serviço secreto B')
        self.assertNotContains(resposta_b, 'Serviço exclusivo A')

    def test_owner_nao_acessa_ids_da_outra_empresa(self):
        self.client.force_login(self.user_a)

        respostas = [
            self.client.get(
                reverse('editar_membro', args=[self.barbeiro_b.pk]),
                HTTP_HOST=self.HOST_A,
            ),
            self.client.post(
                reverse('alterar_status', args=[self.agendamento_b.pk, 'concluido']),
                HTTP_HOST=self.HOST_A,
            ),
            self.client.post(
                reverse('excluir_item', args=[self.item_b.pk]),
                HTTP_HOST=self.HOST_A,
            ),
        ]

        self.assertTrue(all(resposta.status_code == 404 for resposta in respostas))
        self.assertTrue(ItemEstoque.all_objects.filter(pk=self.item_b.pk).exists())

    def test_login_de_empresa_a_em_host_b_falha_fechado(self):
        self.client.force_login(self.user_a)
        resposta = self.client.get(reverse('painel_home'), HTTP_HOST=self.HOST_B)
        self.assertEqual(resposta.status_code, 404)

    def test_login_rejeita_credencial_valida_de_outro_tenant(self):
        resposta = self.client.post(
            reverse('login'),
            {'username': 'owner-a', 'password': 'senha-teste-123'},
            HTTP_HOST=self.HOST_B,
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    @override_settings(
        LOGIN_RATE_LIMIT_IP=1,
        LOGIN_RATE_LIMIT_USERNAME=1,
        LOGIN_RATE_LIMIT_WINDOW=900,
    )
    def test_login_aplica_rate_limit_por_ip_e_usuario(self):
        self.client.logout()
        primeira = self.client.post(
            reverse('login'),
            {'username': 'owner-a', 'password': 'senha-incorreta'},
            HTTP_HOST=self.HOST_A,
        )
        segunda = self.client.post(
            reverse('login'),
            {'username': 'owner-a', 'password': 'senha-incorreta'},
            HTTP_HOST=self.HOST_A,
        )
        self.assertEqual(primeira.status_code, 200)
        self.assertEqual(segunda.status_code, 429)

    def test_cancelamento_publico_por_token_nao_cruza_empresas(self):
        token_b_no_host_a = self.client.get(
            reverse('cancelar_por_token', args=[self.agendamento_b.token_cancelamento]),
            HTTP_HOST=self.HOST_A,
        )
        self.assertEqual(token_b_no_host_a.status_code, 404)

        resposta = self.client.post(
            reverse('cancelar_por_token', args=[self.agendamento_a.token_cancelamento]),
            HTTP_HOST=self.HOST_A,
        )
        self.agendamento_a.refresh_from_db()
        self.agendamento_b.refresh_from_db()
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(self.agendamento_a.status, 'cancelado')
        self.assertEqual(self.agendamento_b.status, 'agendado')

    def test_exports_nao_incluem_dados_de_outro_tenant(self):
        self.client.force_login(self.user_a)

        financeiro = self.client.get(reverse('exportar_financeiro'), HTTP_HOST=self.HOST_A)
        historico = self.client.get(reverse('exportar_historico'), HTTP_HOST=self.HOST_A)
        conteudo_financeiro = financeiro.content.decode('utf-8-sig')
        conteudo_historico = historico.content.decode('utf-8-sig')

        self.assertIn('Receita exclusiva A', conteudo_financeiro)
        self.assertNotIn('Receita secreta B', conteudo_financeiro)
        self.assertIn('Cliente A', conteudo_historico)
        self.assertNotIn('Cliente B', conteudo_historico)

    def test_mesmo_codigo_de_cupom_tem_valor_independente(self):
        resposta_a = self.client.post(
            reverse('validar_cupom'),
            data=json.dumps({'codigo': 'promo'}),
            content_type='application/json',
            HTTP_HOST=self.HOST_A,
        )
        resposta_b = self.client.post(
            reverse('validar_cupom'),
            data=json.dumps({'codigo': 'promo'}),
            content_type='application/json',
            HTTP_HOST=self.HOST_B,
        )

        self.assertEqual(resposta_a.json()['valor'], 10.0)
        self.assertEqual(resposta_b.json()['valor'], 20.0)

    def test_model_rejeita_relacionamento_cruzado(self):
        with empresa_context(self.empresa_a):
            agendamento = Agendamento(
                cliente=self.cliente_b,
                barbeiro=self.barbeiro_a,
                corte=self.corte_a,
                data=date.today() + timedelta(days=2),
                horario='11:00',
            )
            with self.assertRaises(ValidationError):
                agendamento.save()

    def test_manager_sem_contexto_falha_fechado(self):
        self.assertEqual(Cliente.objects.count(), 0)
        self.assertEqual(Cliente.all_objects.count(), 2)

    def test_slug_do_tenant_precisa_ser_valido_para_dns(self):
        with self.assertRaises(ValidationError):
            Empresa.objects.create(nome='Inválida', slug='empresa_invalida')

    def test_todas_as_entidades_operacionais_ficam_no_tenant_b(self):
        hoje = date.today()
        with empresa_context(self.empresa_b):
            plano = PlanoMensal.objects.create(nome='Plano B', valor_mensal=Decimal('99.00'))
            assinatura = AssinaturaCliente.objects.create(
                cliente=self.cliente_b,
                plano=plano,
                data_inicio=hoje,
                data_renovacao=hoje + timedelta(days=30),
            )
            bloqueio = BloqueioAgenda.objects.create(
                barbeiro=self.barbeiro_b,
                data_inicio=hoje + timedelta(days=20),
                data_fim=hoje + timedelta(days=21),
                motivo='QA B',
            )
            avaliacao = AvaliacaoAtendimento.objects.create(
                agendamento=self.agendamento_b,
                nota=5,
                token='qa-avaliacao-empresa-b',
            )
            saldo = SaldoLoyalty.objects.create(cliente=self.cliente_b, pontos=30)
            pontos = TransacaoLoyalty.objects.create(
                cliente=self.cliente_b,
                agendamento=self.agendamento_b,
                pontos=30,
                descricao='QA fidelidade B',
            )
            espera = EntradaListaEspera.objects.create(
                cliente=self.cliente_b,
                barbeiro=self.barbeiro_b,
                data=hoje + timedelta(days=15),
            )
            meta = MetaBarbeiro.objects.create(
                barbeiro=self.barbeiro_b,
                mes=hoje.month,
                ano=hoje.year,
                meta_cortes=50,
                meta_faturamento=Decimal('5000.00'),
            )
            comissao = LancamentoComissao.objects.create(
                barbeiro=self.barbeiro_b,
                agendamento=self.agendamento_b,
                valor_servico=Decimal('90.00'),
                valor_comissao=Decimal('27.00'),
            )
            objetos_b = (
                self.barbeiro_b, self.corte_b, self.cliente_b, self.agendamento_b,
                self.item_b, plano, assinatura, bloqueio, avaliacao, saldo, pontos,
                espera, meta, comissao,
                ConfiguracaoBarbearia.objects.get(), ConfigLoyalty.objects.get(),
                MembroEmpresa.objects.get(usuario=self.user_b),
                AceiteTermos.objects.get(usuario=self.user_b),
            )

        with empresa_context(self.empresa_a):
            for objeto in objetos_b:
                self.assertFalse(
                    objeto.__class__.objects.filter(pk=objeto.pk).exists(),
                    f'{objeto.__class__.__name__} vazou entre tenants',
                )

        self.assertIn(f'tenants/{self.empresa_b.pk}/', upload_logo(
            ConfiguracaoBarbearia.all_objects.get(empresa=self.empresa_b), 'logo.png'
        ))
        self.assertIn(f'tenants/{self.empresa_b.pk}/', upload_foto_barbeiro(
            self.barbeiro_b, 'perfil.webp'
        ))
        resposta_avaliacao = self.client.get(
            reverse('avaliar_atendimento', args=[avaliacao.token]),
            HTTP_HOST=self.HOST_A,
        )
        self.assertEqual(resposta_avaliacao.status_code, 404)

    def test_membro_precisa_aceitar_termos_vigentes(self):
        with empresa_context(self.empresa_a):
            AceiteTermos.objects.filter(usuario=self.user_a).delete()

        self.client.force_login(self.user_a)
        bloqueado = self.client.get(reverse('painel_home'), HTTP_HOST=self.HOST_A)
        self.assertRedirects(
            bloqueado,
            reverse('aceitar_termos'),
            fetch_redirect_response=False,
        )

        aceito = self.client.post(
            reverse('aceitar_termos'),
            {'aceite': 'on'},
            HTTP_HOST=self.HOST_A,
        )
        self.assertRedirects(aceito, reverse('painel_home'), fetch_redirect_response=False)
        self.assertTrue(AceiteTermos.all_objects.filter(
            empresa=self.empresa_a,
            usuario=self.user_a,
            versao_termos='1.0',
            versao_privacidade='1.0',
            aceite_em_nome_da_empresa=True,
        ).exists())
        comprovante = self.client.get(reverse('aceitar_termos'), HTTP_HOST=self.HOST_A)
        self.assertContains(comprovante, 'Aceite já registrado')
        self.assertNotContains(comprovante, 'name="aceite"')

    def test_usuario_altera_a_propria_senha_e_mantem_a_sessao(self):
        self.client.force_login(self.user_a)
        resposta = self.client.post(
            reverse('alterar_minha_senha'),
            {
                'old_password': 'senha-teste-123',
                'new_password1': 'Nova-Senha-Segura-987!',
                'new_password2': 'Nova-Senha-Segura-987!',
            },
            HTTP_HOST=self.HOST_A,
        )

        self.user_a.refresh_from_db()
        self.assertRedirects(resposta, reverse('painel_home'), fetch_redirect_response=False)
        self.assertTrue(self.user_a.check_password('Nova-Senha-Segura-987!'))
        painel = self.client.get(reverse('painel_home'), HTTP_HOST=self.HOST_A)
        self.assertEqual(painel.status_code, 200)


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class PlatformOnboardingTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username='dev-platform',
            email='dev@example.com',
            password='Senha-Forte-Dev-987!',
        )

    def test_somente_superuser_acessa_portal_e_admin(self):
        usuario = User.objects.create_user(
            username='staff-comum',
            password='Senha-Forte-123!',
            is_staff=True,
        )
        self.client.force_login(usuario)
        self.assertEqual(self.client.get(reverse('empresas_plataforma')).status_code, 404)
        self.assertEqual(self.client.get('/admin/').status_code, 404)

        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(reverse('empresas_plataforma')).status_code, 200)
        self.assertEqual(self.client.get('/admin/').status_code, 200)

    def test_onboarding_cria_tenant_pronto_para_operar(self):
        self.client.force_login(self.superuser)
        resposta = self.client.post(reverse('empresas_plataforma'), {
            'nome_empresa': 'QA Navalha Norte',
            'slug': 'qa-navalha-norte',
            'slogan': 'Precisão em cada corte',
            'timezone': 'America/Manaus',
            'cor_destaque': '#336699',
            'nome_dono': 'Dono QA Norte',
            'telefone_dono': '(92) 99999-1234',
            'username_dono': 'dono-qa-norte',
            'email_dono': 'dono.qa.norte@example.com',
            'senha_dono': 'Senha-Forte-QA-987!',
            'confirmar_senha': 'Senha-Forte-QA-987!',
            'hora_inicio': '08:30',
            'hora_fim': '18:30',
            'intervalo_minutos': '30',
            'trabalha_sabado': 'on',
            'aceita_agendamentos_dono': 'on',
            'servico_inicial': 'Corte QA Norte',
            'preco_servico': '65.00',
            'senha_superuser': 'Senha-Forte-Dev-987!',
        })
        self.assertRedirects(resposta, reverse('empresas_plataforma'))

        empresa = Empresa.objects.get(slug='qa-navalha-norte')
        usuario = User.objects.get(username='dono-qa-norte')
        with empresa_context(empresa):
            membro = MembroEmpresa.objects.get(usuario=usuario)
            barbeiro = Barbeiro.objects.get(usuario=usuario)
            configuracao = ConfiguracaoBarbearia.objects.get()
            servico = Corte.objects.get(barbeiro=barbeiro)

        self.assertEqual(membro.papel, MembroEmpresa.OWNER)
        self.assertTrue(barbeiro.is_dono)
        self.assertEqual(barbeiro.telefone, '(92) 99999-1234')
        self.assertTrue(barbeiro.aceita_agendamentos_online)
        self.assertEqual(configuracao.nome_barbearia, empresa.nome)
        self.assertEqual(configuracao.cor_destaque, '#336699')
        self.assertEqual(servico.preco, Decimal('65.00'))
        self.assertEqual(empresa.timezone, 'America/Manaus')
        dashboard = self.client.get(reverse('empresas_plataforma'))
        self.assertContains(dashboard, '(92) 99999-1234')
        self.assertContains(dashboard, 'dono.qa.norte@example.com')

    def test_onboarding_exige_senha_atual_do_superuser(self):
        self.client.force_login(self.superuser)
        resposta = self.client.post(reverse('empresas_plataforma'), {
            'nome_empresa': 'Não Deve Criar',
            'slug': 'nao-deve-criar',
            'timezone': 'America/Sao_Paulo',
            'cor_destaque': '#336699',
            'nome_dono': 'Dono Inválido',
            'telefone_dono': '(11) 99999-0000',
            'username_dono': 'dono-invalido',
            'email_dono': 'dono.invalido@example.com',
            'senha_dono': 'Senha-Forte-QA-987!',
            'confirmar_senha': 'Senha-Forte-QA-987!',
            'hora_inicio': '09:00',
            'hora_fim': '18:00',
            'intervalo_minutos': '30',
            'servico_inicial': 'Corte',
            'preco_servico': '50.00',
            'senha_superuser': 'senha-errada',
        })
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Senha do superuser incorreta')
        self.assertFalse(Empresa.objects.filter(slug='nao-deve-criar').exists())
