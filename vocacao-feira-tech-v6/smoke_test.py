"""Teste rápido da aplicação Feira Tech (versão SQLite).

Uso:
    python smoke_test.py

O teste usa um banco SQLite temporário e não altera o banco real.
Requer as dependências de requirements.txt instaladas.
"""

import os
import tempfile
import unittest

# A senha padrão local só vale fora de hospedagem; os testes a definem.
os.environ.setdefault("ADMIN_PASSWORD", "admin@feira2025")

import db_store as store
import app as app_module
from app import app


class FeiraTechSmokeTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.original_db_path = store.DB_PATH
        store.DB_PATH = os.path.join(self.tmpdir.name, "teste.db")
        store.init_db()
        app_module._reset_rate_limits()
        self.client = app.test_client()

    def tearDown(self):
        store.DB_PATH = self.original_db_path
        self.tmpdir.cleanup()

    def test_fluxo_principal(self):
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.get_json()["ok"])

        visitor_key = "visitante_teste_1234567890"
        profile = self.client.post("/api/visitors/profile", json={
            "visitor_key": visitor_key,
            "profile_type": "visitante_externo",
            "name": "Visitante Teste",
        })
        self.assertEqual(profile.status_code, 201)

        created = self.client.post("/api/stands", json={
            "name": "Stand Teste",
            "course": "webdesign",
        })
        self.assertEqual(created.status_code, 201)
        payload = created.get_json()
        stand_id = payload["stand"]["id"]
        access_code = payload["access_code"]

        start = self.client.post(
            f"/api/stands/{stand_id}/visits/start",
            json={"visitor_key": visitor_key},
        )
        self.assertEqual(start.status_code, 201)

        visit_id = start.get_json()["visit_id"]
        finish = self.client.post(
            f"/api/stands/{stand_id}/visits/{visit_id}/finish",
            json={"visitor_key": visitor_key, "stars": 5},
        )
        self.assertEqual(finish.status_code, 200)

        dashboard = self.client.get(
            f"/api/stands/{stand_id}/dashboard",
            query_string={"code": access_code},
        )
        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(dashboard.get_json()["visitors"], 1)

    def test_trava_de_perfil_na_api(self):
        created = self.client.post("/api/stands", json={
            "name": "Stand Perfil Obrigatorio",
            "course": "programacao",
        })
        stand_id = created.get_json()["stand"]["id"]

        response = self.client.post(
            f"/api/stands/{stand_id}/visits/start",
            json={"visitor_key": "visitante_sem_perfil_123456"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.get_json()["profile_required"])


    def test_erros_de_api_retornam_json(self):
        response = self.client.get("/api/rota-inexistente")
        self.assertEqual(response.status_code, 404)
        self.assertIn("error", response.get_json())

    def test_senha_admin_com_acentos_nao_quebra(self):
        response = self.client.get("/api/admin/dashboard", query_string={"password": "senhá-errada"})
        self.assertEqual(response.status_code, 401)

    def test_trava_de_ouro_do_proprio_grupo(self):
        visitor_key = "aluno_do_grupo_1234567890"
        self.client.post("/api/visitors/profile", json={
            "visitor_key": visitor_key,
            "profile_type": "aluno",
            "name": "Aluno Teste",
            "contact": "aluno@teste.com",
            "cursos": ["webdesign"],
            "group_name": "Horta Inteligente",
        })
        created = self.client.post("/api/stands", json={
            "name": "Horta Inteligente",
            "course": "webdesign",
        })
        stand_id = created.get_json()["stand"]["id"]
        blocked = self.client.post(
            f"/api/stands/{stand_id}/visits/start",
            json={"visitor_key": visitor_key},
        )
        self.assertEqual(blocked.status_code, 403)
        self.assertTrue(blocked.get_json()["own_stand"])

    def test_stand_duplicado_e_qrcode(self):
        first = self.client.post("/api/stands", json={"name": "Robótica Legal", "course": "programacao"})
        self.assertEqual(first.status_code, 201)
        code = first.get_json()["access_code"]
        stand_id = first.get_json()["stand"]["id"]

        duplicated = self.client.post("/api/stands", json={"name": "robotica legal!", "course": "programacao"})
        self.assertEqual(duplicated.status_code, 409)

        qr = self.client.get(f"/api/stands/{stand_id}/qr", query_string={"code": code})
        self.assertEqual(qr.status_code, 200)
        self.assertEqual(qr.mimetype, "image/png")

        export = self.client.get(f"/api/stands/{stand_id}/export", query_string={"code": code})
        self.assertEqual(export.status_code, 200)

        self.assertEqual(
            self.client.get(f"/api/stands/{stand_id}/dashboard", query_string={"code": "ERRADO"}).status_code,
            401,
        )


    # ── Testes do fluxo ágil (cadastro único + QR + passaporte) ────────────

    def test_cadastro_unico_e_passaporte(self):
        """Um cadastro serve para toda a feira e o passaporte mostra o progresso."""
        visitor_key = "visitante_passaporte_123456"

        stand_a = self.client.post("/api/stands", json={
            "name": "Projeto A", "course": "webdesign",
        }).get_json()["stand"]["id"]
        stand_b = self.client.post("/api/stands", json={
            "name": "Projeto B", "course": "programacao",
        }).get_json()["stand"]["id"]

        # Cadastro único, com nome e contato
        profile = self.client.post("/api/visitors/profile", json={
            "visitor_key": visitor_key,
            "profile_type": "empresa",
            "name": "Ana Souza",
            "contact": "ana@empresa.com.br",
        })
        self.assertEqual(profile.status_code, 201)

        # Passaporte começa zerado
        passport = self.client.get(f"/api/visitors/passport?key={visitor_key}").get_json()
        self.assertEqual(passport["rated"], 0)
        self.assertEqual(passport["available"], 2)

        # Avalia o stand A — sem repetir o cadastro
        visit_id = self.client.post(
            f"/api/stands/{stand_a}/visits/start", json={"visitor_key": visitor_key},
        ).get_json()["visit_id"]
        finish = self.client.post(
            f"/api/stands/{stand_a}/visits/{visit_id}/finish",
            json={"visitor_key": visitor_key, "stars": 5},
        )
        self.assertEqual(finish.status_code, 200)

        passport = self.client.get(f"/api/visitors/passport?key={visitor_key}").get_json()
        self.assertEqual(passport["rated"], 1)
        self.assertEqual(passport["profile"]["name"], "Ana Souza")
        restante = [s for s in passport["stands"] if not s["rated"]]
        self.assertEqual(restante[0]["id"], stand_b)

    def test_trava_de_ouro_pelo_stand_escolhido(self):
        """Aluno que escolheu o stand na lista não consegue avaliar o próprio."""
        visitor_key = "aluno_seleciona_stand_123456"
        stand_id = self.client.post("/api/stands", json={
            "name": "Horta Inteligente", "course": "programacao",
        }).get_json()["stand"]["id"]

        self.client.post("/api/visitors/profile", json={
            "visitor_key": visitor_key,
            "profile_type": "aluno",
            "name": "Aluno Seleciona Stand",
            "contact": "(11) 99999-0000",
            "cursos": ["programacao"],
            "own_stand_ids": [stand_id],
        })

        bloqueio = self.client.post(
            f"/api/stands/{stand_id}/visits/start", json={"visitor_key": visitor_key},
        )
        self.assertEqual(bloqueio.status_code, 403)
        self.assertTrue(bloqueio.get_json().get("own_stand"))

    def test_compartilhamento_por_rede_social(self):
        """O compartilhamento registra a rede usada e aparece no painel."""
        visitor_key = "visitante_redes_1234567890"
        stand_id = self.client.post("/api/stands", json={
            "name": "Projeto Redes", "course": "audiovisual",
        }).get_json()["stand"]["id"]
        code = self.client.post("/api/stands", json={
            "name": "Projeto Redes 2", "course": "audiovisual",
        }).get_json()  # segundo stand só para garantir isolamento

        self.client.post("/api/visitors/profile", json={
            "visitor_key": visitor_key, "profile_type": "visitante_externo",
            "name": "Visitante Redes",
        })
        visit_id = self.client.post(
            f"/api/stands/{stand_id}/visits/start", json={"visitor_key": visitor_key},
        ).get_json()["visit_id"]
        self.client.post(
            f"/api/stands/{stand_id}/visits/{visit_id}/finish",
            json={"visitor_key": visitor_key, "stars": 4},
        )

        for rede in ("instagram", "tiktok", "instagram"):
            resposta = self.client.post(
                f"/api/stands/{stand_id}/engagement/share",
                json={"visitor_key": visitor_key, "network": rede},
            )
            self.assertEqual(resposta.status_code, 201)

        painel = self.client.get("/api/admin/dashboard?password=admin@feira2025").get_json()
        self.assertEqual(painel["shares_by_network"]["instagram"], 2)
        self.assertEqual(painel["shares_by_network"]["tiktok"], 1)
        self.assertEqual(painel["checked_in"], 1)

    def test_exportacao_do_credenciamento(self):
        """O organizador consegue baixar a lista de credenciados."""
        self.client.post("/api/visitors/profile", json={
            "visitor_key": "visitante_export_1234567890",
            "profile_type": "empresa",
            "name": "Carlos Lima",
            "contact": "11999999999",
        })
        export = self.client.get("/api/admin/visitors/export?password=admin@feira2025")
        self.assertEqual(export.status_code, 200)
        conteudo = export.data.decode("utf-8-sig")
        self.assertIn("Carlos Lima", conteudo)
        self.assertIn("11999999999", conteudo)

        negado = self.client.get("/api/admin/visitors/export?password=errada")
        self.assertEqual(negado.status_code, 401)


    # ── Recuperação de código e relatórios ────────────────────────────────

    def test_recuperacao_de_codigo_do_expositor(self):
        """Expositor pede novo código; só o admin consegue gerar."""
        criado = self.client.post("/api/stands", json={
            "name": "Grupo Esquecido", "course": "webdesign",
        }).get_json()
        stand_id     = criado["stand"]["id"]
        codigo_antigo = criado["access_code"]

        pedido = self.client.post(
            f"/api/stands/{stand_id}/recovery/request",
            json={"requester": "Maria Silva"},
        )
        self.assertEqual(pedido.status_code, 201)

        # Pedido duplicado não cria um segundo registro
        repetido = self.client.post(
            f"/api/stands/{stand_id}/recovery/request",
            json={"requester": "Maria Silva"},
        )
        self.assertTrue(repetido.get_json().get("duplicate"))

        pendentes = self.client.get(
            "/api/admin/recovery/requests?password=admin@feira2025"
        ).get_json()
        self.assertEqual(len(pendentes), 1)
        self.assertEqual(pendentes[0]["requester"], "Maria Silva")

        # Sem a senha de admin ninguém redefine o código
        negado = self.client.post(
            f"/api/admin/stands/{stand_id}/reset-code", json={"password": "errada"},
        )
        self.assertEqual(negado.status_code, 401)

        novo = self.client.post(
            f"/api/admin/stands/{stand_id}/reset-code",
            json={"password": "admin@feira2025", "request_id": pendentes[0]["id"]},
        )
        self.assertEqual(novo.status_code, 201)
        codigo_novo = novo.get_json()["access_code"]
        self.assertNotEqual(codigo_novo, codigo_antigo)

        # O código antigo deixa de valer e o novo funciona
        self.assertEqual(self.client.post(
            f"/api/stands/{stand_id}/access", json={"access_code": codigo_antigo},
        ).status_code, 401)
        self.assertEqual(self.client.post(
            f"/api/stands/{stand_id}/access", json={"access_code": codigo_novo},
        ).status_code, 200)

        # A fila de pedidos foi esvaziada
        self.assertEqual(self.client.get(
            "/api/admin/recovery/requests?password=admin@feira2025"
        ).get_json(), [])

    def test_relatorio_consolidado(self):
        """O relatório agrega avaliações, horários, cursos e alcance."""
        visitor_key = "visitante_relatorio_123456"
        stand_id = self.client.post("/api/stands", json={
            "name": "Projeto Relatorio", "course": "audiovisual",
        }).get_json()["stand"]["id"]
        vazio_id = self.client.post("/api/stands", json={
            "name": "Projeto Sem Visita", "course": "ppt",
        }).get_json()["stand"]["id"]

        self.client.post("/api/visitors/profile", json={
            "visitor_key": visitor_key, "profile_type": "funcionario",
            "name": "Funcionário Teste", "department": "Coordenação",
        })
        visit_id = self.client.post(
            f"/api/stands/{stand_id}/visits/start", json={"visitor_key": visitor_key},
        ).get_json()["visit_id"]
        self.client.post(
            f"/api/stands/{stand_id}/visits/{visit_id}/finish",
            json={"visitor_key": visitor_key, "stars": 5},
        )
        self.client.post(
            f"/api/stands/{stand_id}/engagement/share",
            json={"visitor_key": visitor_key, "network": "instagram"},
        )

        self.assertEqual(
            self.client.get("/api/admin/report?password=errada").status_code, 401
        )

        r = self.client.get("/api/admin/report?password=admin@feira2025").get_json()
        self.assertEqual(r["total_visitas"], 1)
        self.assertEqual(r["visitantes_unicos"], 1)
        self.assertEqual(r["nota_media"], 5)
        self.assertEqual(r["satisfacao_pct"], 100)
        self.assertEqual(r["stands_avaliados"], 1)
        self.assertEqual(r["por_curso"]["audiovisual"]["visitas"], 1)
        self.assertEqual(r["por_perfil"]["funcionario"], 1)
        self.assertEqual(r["shares_by_network"]["instagram"], 1)
        self.assertEqual(len(r["por_hora"]), 1)
        self.assertIn(vazio_id, [s["id"] for s in r["sem_avaliacao"]])
        self.assertEqual(r["ranking"][0]["name"], "Projeto Relatorio")

    def test_backup_e_exportacao_do_mural(self):
        """Backup preserva o histórico e não expõe os códigos de acesso."""
        import json as _json

        stand_id = self.client.post("/api/stands", json={
            "name": "Projeto Backup", "course": "programacao",
        }).get_json()["stand"]["id"]
        visitor_key = "visitante_backup_1234567890"
        self.client.post("/api/visitors/profile", json={
            "visitor_key": visitor_key, "profile_type": "visitante_externo",
            "name": "Visitante Backup",
        })
        visit_id = self.client.post(
            f"/api/stands/{stand_id}/visits/start", json={"visitor_key": visitor_key},
        ).get_json()["visit_id"]
        self.client.post(
            f"/api/stands/{stand_id}/visits/{visit_id}/finish",
            json={"visitor_key": visitor_key, "stars": 4},
        )
        self.client.post(
            f"/api/stands/{stand_id}/engagement/support",
            json={
                "visitor_key": visitor_key,
                "message": "Projeto muito bem apresentado pelo grupo.",
                "consent": True,
            },
        )

        backup = self.client.get("/api/admin/backup?password=admin@feira2025")
        self.assertEqual(backup.status_code, 200)
        conteudo = _json.loads(backup.data.decode("utf-8"))
        self.assertEqual(len(conteudo["visitas"]), 1)
        self.assertNotIn("code_hash", conteudo["stands"][0])
        self.assertNotIn("code_salt", conteudo["stands"][0])

        mural = self.client.get("/api/admin/supports/export?password=admin@feira2025")
        self.assertEqual(mural.status_code, 200)
        self.assertIn("bem apresentado", mural.data.decode("utf-8-sig"))


    # ── Cadastro detalhado por perfil ───────────────────────────────────────

    def test_aluno_com_multiplos_cursos_e_stands(self):
        """Aluno que faz mais de um curso pode ter mais de um stand protegido."""
        stand_web = self.client.post("/api/stands", json={
            "name": "Grupo Web", "course": "webdesign",
        }).get_json()["stand"]["id"]
        stand_prog = self.client.post("/api/stands", json={
            "name": "Grupo Programação", "course": "programacao",
        }).get_json()["stand"]["id"]
        stand_alheio = self.client.post("/api/stands", json={
            "name": "Grupo de Outro Aluno", "course": "audiovisual",
        }).get_json()["stand"]["id"]

        visitor_key = "aluno_dois_cursos_1234567890"
        cadastro = self.client.post("/api/visitors/profile", json={
            "visitor_key": visitor_key,
            "profile_type": "aluno",
            "name": "Bruno Multi",
            "contact": "bruno@aluno.com",
            "cursos": ["webdesign", "programacao"],
            "own_stand_ids": [stand_web, stand_prog],
        })
        self.assertEqual(cadastro.status_code, 201)

        # Bloqueado nos dois stands do próprio aluno
        for stand_id in (stand_web, stand_prog):
            bloqueio = self.client.post(
                f"/api/stands/{stand_id}/visits/start", json={"visitor_key": visitor_key},
            )
            self.assertEqual(bloqueio.status_code, 403)
            self.assertTrue(bloqueio.get_json().get("own_stand"))

        # Livre para avaliar o stand de outro aluno
        liberado = self.client.post(
            f"/api/stands/{stand_alheio}/visits/start", json={"visitor_key": visitor_key},
        )
        self.assertEqual(liberado.status_code, 201)

        passaporte = self.client.get(
            f"/api/visitors/passport?key={visitor_key}"
        ).get_json()
        proprios = [s for s in passaporte["stands"] if s["own"]]
        self.assertEqual({s["id"] for s in proprios}, {stand_web, stand_prog})
        self.assertEqual(passaporte["profile"]["cursos"], ["webdesign", "programacao"])

    def test_validacoes_do_cadastro_por_perfil(self):
        """Nome é obrigatório para todos; contato e curso são obrigatórios só para aluno."""
        base_key = "visitante_validacao_123456"

        # Sem nome, para qualquer perfil, é rejeitado
        sem_nome = self.client.post("/api/visitors/profile", json={
            "visitor_key": base_key, "profile_type": "visitante_externo",
        })
        self.assertEqual(sem_nome.status_code, 400)

        # Aluno sem contato é rejeitado
        sem_contato = self.client.post("/api/visitors/profile", json={
            "visitor_key": base_key, "profile_type": "aluno",
            "name": "Aluno Sem Contato", "cursos": ["webdesign"],
            "group_name": "Grupo Incompleto",
        })
        self.assertEqual(sem_contato.status_code, 400)

        # Aluno sem nenhum curso é rejeitado
        sem_curso = self.client.post("/api/visitors/profile", json={
            "visitor_key": base_key, "profile_type": "aluno",
            "name": "Aluno Sem Curso", "contact": "11999999999",
            "group_name": "Grupo Incompleto",
        })
        self.assertEqual(sem_curso.status_code, 400)

        # Aluno sem indicar nenhum stand nem grupo manual é rejeitado
        sem_grupo = self.client.post("/api/visitors/profile", json={
            "visitor_key": base_key, "profile_type": "aluno",
            "name": "Aluno Sem Grupo", "contact": "11999999999",
            "cursos": ["webdesign"],
        })
        self.assertEqual(sem_grupo.status_code, 400)

        # Funcionário e visitante externo não precisam de contato
        funcionario = self.client.post("/api/visitors/profile", json={
            "visitor_key": base_key, "profile_type": "funcionario",
            "name": "Funcionário Sem Contato",
        })
        self.assertEqual(funcionario.status_code, 201)

    def test_credenciamento_completo_por_perfil_no_relatorio(self):
        """Departamento e empresa aparecem no CSV; não vazam para outros perfis."""
        self.client.post("/api/visitors/profile", json={
            "visitor_key": "funcionario_setor_1234567890",
            "profile_type": "funcionario",
            "name": "Marta Setor",
            "contact": "marta@vocacao.org",
            "department": "Secretaria",
        })
        self.client.post("/api/visitors/profile", json={
            "visitor_key": "empresa_representante_123456",
            "profile_type": "empresa",
            "name": "João Representante",
            "contact": "joao@empresa.com",
            "company_name": "Tech Solutions Ltda.",
        })

        import csv as _csv
        import io as _io

        export = self.client.get("/api/admin/visitors/export?password=admin@feira2025")
        conteudo = export.data.decode("utf-8-sig")
        self.assertIn("Marta Setor", conteudo)
        self.assertIn("Secretaria", conteudo)
        self.assertIn("Tech Solutions Ltda.", conteudo)

        linhas = list(_csv.DictReader(_io.StringIO(conteudo)))
        por_nome = {l["nome"]: l for l in linhas}

        # O setor de um não aparece na linha do outro (evita vazamento entre perfis)
        self.assertEqual(por_nome["Marta Setor"]["departamento_setor"], "Secretaria")
        self.assertEqual(por_nome["Marta Setor"]["empresa"], "")
        self.assertEqual(por_nome["João Representante"]["empresa"], "Tech Solutions Ltda.")
        self.assertEqual(por_nome["João Representante"]["departamento_setor"], "")


    # ── Novos testes da versão SQLite / endurecimento ─────────────────────

    def _visitante(self, key, tipo="visitante_externo", **extra):
        dados = {"visitor_key": key, "profile_type": tipo, "name": "Fulano de Tal"}
        dados.update(extra)
        return self.client.post("/api/visitors/profile", json=dados)

    def test_nome_de_stand_com_html_e_recusado(self):
        """XSS armazenado: o nome do stand aparece nos painéis do organizador."""
        r = self.client.post("/api/stands", json={
            "name": "<img src=x onerror=alert(1)>", "course": "webdesign",
        })
        self.assertEqual(r.status_code, 400)

        ok = self.client.post("/api/stands", json={"name": "Mãos d'Água & Cia", "course": "webdesign"})
        self.assertEqual(ok.status_code, 201)

    def test_um_voto_por_stand_mesmo_com_requisicoes_simultaneas(self):
        """O UNIQUE do banco impede voto duplo mesmo em corrida entre threads."""
        import threading

        stand_id = self.client.post("/api/stands", json={
            "name": "Stand Corrida", "course": "programacao",
        }).get_json()["stand"]["id"]
        key = "visitante_corrida_1234567890"
        self._visitante(key)

        resultados = []

        def votar():
            c = app.test_client()
            start = c.post(f"/api/stands/{stand_id}/visits/start", json={"visitor_key": key})
            if start.status_code == 201:
                fin = c.post(
                    f"/api/stands/{stand_id}/visits/{start.get_json()['visit_id']}/finish",
                    json={"visitor_key": key, "stars": 5},
                )
                resultados.append(fin.status_code)

        threads = [threading.Thread(target=votar) for _ in range(12)]
        [t.start() for t in threads]
        [t.join() for t in threads]

        self.assertEqual(resultados.count(200), 1)
        with store._read() as c:
            n = c.execute("SELECT COUNT(*) FROM visits WHERE finished_at IS NOT NULL").fetchone()[0]
        self.assertEqual(n, 1)

    def test_bloqueia_forca_bruta_na_senha_do_admin(self):
        for _ in range(app_module._FAIL_MAX):
            r = self.client.get("/api/admin/dashboard?password=chute")
            self.assertEqual(r.status_code, 401)
        bloqueado = self.client.get("/api/admin/dashboard?password=chute")
        self.assertEqual(bloqueado.status_code, 429)
        # Mesmo a senha certa fica bloqueada até a janela passar
        self.assertEqual(
            self.client.get("/api/admin/dashboard?password=admin@feira2025").status_code, 429
        )

    def test_senha_padrao_e_recusada_em_hospedagem(self):
        original = dict(os.environ)
        try:
            os.environ["RENDER"] = "true"
            os.environ["ADMIN_PASSWORD"] = "admin@feira2025"
            r = self.client.get("/api/admin/dashboard?password=admin@feira2025")
            self.assertEqual(r.status_code, 503)

            os.environ["ADMIN_PASSWORD"] = "uma-senha-forte-só-da-organização"
            ok = self.client.get(
                "/api/admin/dashboard?password=uma-senha-forte-só-da-organização"
            )
            self.assertEqual(ok.status_code, 200)
        finally:
            os.environ.clear()
            os.environ.update(original)

    def test_backup_sqlite_baixa_arquivo_valido(self):
        self.client.post("/api/stands", json={"name": "Stand Backup", "course": "audiovisual"})
        r = self.client.get("/api/admin/backup/sqlite?password=admin@feira2025")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data.startswith(b"SQLite format 3"))
        negado = self.client.get("/api/admin/backup/sqlite?password=errada")
        self.assertEqual(negado.status_code, 401)

    def test_url_publica_nos_qr_codes(self):
        original = os.environ.get("PUBLIC_URL")
        try:
            os.environ["PUBLIC_URL"] = "https://feira.exemplo.org.br/"
            with app.test_request_context("/"):
                self.assertEqual(app_module._public_base_url(), "https://feira.exemplo.org.br")
        finally:
            if original is None:
                os.environ.pop("PUBLIC_URL", None)
            else:
                os.environ["PUBLIC_URL"] = original

    def test_health_avisa_quando_banco_e_efemero(self):
        original = dict(os.environ)
        try:
            os.environ["RENDER"] = "true"
            os.environ.pop("DATA_PATH", None)
            aviso = self.client.get("/health").get_json()
            self.assertFalse(aviso["persistent"])
            self.assertTrue(aviso["storage_warning"])
        finally:
            os.environ.clear()
            os.environ.update(original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
