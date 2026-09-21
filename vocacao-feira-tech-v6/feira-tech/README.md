# 1ª Feira Tech dos Jovens da Vocação — Sistema de Gestão (v6 · SQLite)

Sistema web em **Python + Flask + HTML/CSS/JavaScript**: cadastro de stands,
credenciamento do visitante, avaliação por QR Code, mural, dashboard do
expositor, painel do organizador e relatório final.

## O que mudou na v6

| Área | Antes (v5) | Agora (v6) |
|---|---|---|
| Armazenamento | `feira_tech.json` (reescrito inteiro a cada voto) | **SQLite** (`data/feira_tech.db`), modo WAL |
| Desempenho sob carga | caía de ~100 para ~33 req/s com 4 mil avaliações | ~700 req/s constante, p99 < 100 ms (teste local) |
| Voto duplo | checado só no código | checado **também no banco** (índice `UNIQUE`) |
| Dados de teste | o `feira_tech.json` do repositório (com stands/visitas de teste) entrava em produção | banco nasce **vazio** |
| Nome do stand com HTML (XSS) | aceito e executado no painel do admin | recusado no servidor **e** escapado no front-end |
| Senha do admin | a senha padrão do README valia em qualquer lugar | em hospedagem a senha padrão é **recusada** (erro 503 até definir `ADMIN_PASSWORD`) |
| Força bruta | ilimitada | ~20 erros / 5 min por IP → bloqueio temporário (429) |
| QR Codes | usavam o endereço do momento | usam `PUBLIC_URL` (dá para trocar de hospedagem sem reimprimir) |
| HTTPS atrás de proxy | QR podia sair com `http://` | `ProxyFix` ativado em hospedagem |
| Backup | JSON sem códigos de acesso | JSON **e** cópia `.db` consistente (`/api/admin/backup/sqlite`) |
| Vercel | arquivos incluídos, mas não funciona com persistência | removidos (veja "Onde hospedar") |

O front-end e todas as rotas da API continuam iguais.

## Onde hospedar (leia antes de publicar)

O SQLite é **um arquivo**. Ele só sobrevive se o servidor tiver um **disco que
não é apagado** entre reinícios.

| Opção | Dados do SQLite | Observação |
|---|---|---|
| **Seu notebook/PC na rede local** | ✔ | Ótimo para o ensaio e como plano B. A leitura de QR embutida no site exige HTTPS; a câmera nativa do celular lê o QR normalmente. |
| **Render — plano pago + Persistent Disk** | ✔ | Recomendado. Use o `render.yaml` deste projeto. |
| **VPS barato** (Hetzner, Contabo, DigitalOcean…) | ✔ | Mensalidade menor; exige configurar HTTPS (Caddy/nginx) e o serviço. |
| Render **gratuito** | ✘ | Dorme após 15 min (cerca de 1 min para acordar) e **apaga o disco** a cada reinício. Só para testes. |
| **Vercel / serverless** | ✘ | Sistema de arquivos somente leitura (só `/tmp`, efêmero). Exigiria banco externo (Postgres) e reescrever `db_store.py`. |

### Render (passo a passo)

1. Suba o projeto para um repositório **privado** no GitHub (o `.gitignore` já
   impede versionar banco e senhas).
2. Render → **New → Blueprint** → escolha o repositório (usa o `render.yaml`).
3. Quando pedir, informe `ADMIN_PASSWORD` (uma frase longa; **não** use a do exemplo).
4. Confirme que o disco está montado em `/var/data` e que
   `DATA_PATH=/var/data/feira_tech.db`.
5. Abra `https://SEU-APP.onrender.com/health` e confira:
   `"storage": "sqlite"`, `"persistent": true`, `"admin_configured": true`.
   Se aparecer `storage_warning`, **não imprima os QR Codes ainda**.
6. Defina `PUBLIC_URL` com o endereço definitivo (de preferência um subdomínio
   da Vocação apontado para o Render) e só então gere e imprima os QR Codes.

> Sem `PUBLIC_URL`, os QR Codes apontam para o endereço `onrender.com`. Se você
> trocar de hospedagem depois, terá que reimprimir tudo.

### Variáveis de ambiente

| Variável | Obrigatória | Para quê |
|---|---|---|
| `ADMIN_PASSWORD` | **sim, em hospedagem** | Senha do `/admin` e do `/relatorio` |
| `DATA_PATH` | **sim, em hospedagem** | Caminho do arquivo `.db` no disco persistente |
| `PUBLIC_URL` | recomendada | Endereço usado nos QR Codes |
| `SECRET_KEY` | recomendada | Chave do Flask |
| `PORT`, `HOST`, `FLASK_DEBUG` | não | Execução local |
| `BEHIND_PROXY=1` | não | Força o `ProxyFix` fora do Render (ex.: VPS com nginx) |

## Teste local

### Mac / Linux

```bash
chmod +x run.sh run.command
./run.sh
```

### Windows

```powershell
.\run.bat
```

Abre em `http://localhost:5000` (ou na próxima porta livre). O painel do
organizador fica em `/admin`. A senha local padrão (`admin@feira2025`) só
funciona no seu computador.

Para testar com celulares na mesma rede Wi-Fi, defina uma senha própria
(qualquer pessoa na rede alcança o `/admin`):

```bash
ADMIN_PASSWORD="minha-senha" HOST=0.0.0.0 ./run.sh
# e abra http://IP-DO-COMPUTADOR:5000 no celular
```

### Smoke test

```bash
python smoke_test.py
```

23 verificações (fluxo completo, Trava de Ouro, voto duplo concorrente,
força bruta, XSS, senha padrão em hospedagem, backup…). Usa um banco
temporário e não mexe nos dados reais.

## Dados

```text
data/feira_tech.db      banco SQLite (criado automaticamente)
data/feira_tech.db-wal  arquivos auxiliares do modo WAL (é normal existirem)
```

* **Backup durante o evento:** em `/admin`, botão de backup (JSON), ou abra
  `/api/admin/backup/sqlite?password=SUA_SENHA` para baixar uma cópia `.db`
  consistente (segura com o servidor em uso). Faça isso no meio e no fim do
  evento. A cópia `.db` contém os hashes dos códigos de acesso: trate como dado sensível.
* **Zerar tudo** (antes do evento, depois dos testes): `python reset_data.py`.
* **Importar dados da v5** (`feira_tech.json`):
  `python migrate_json_to_sqlite.py caminho/feira_tech.json`.

## LGPD — atenção

O sistema guarda **nome e contato** (e-mail/telefone) de visitantes, e o
público inclui **jovens e possivelmente menores de idade**. Antes do evento:

* mostre um aviso curto de privacidade no credenciamento (quem é o
  responsável, para que os dados serão usados, por quanto tempo ficam guardados);
* combine com a coordenação/jurídico da Vocação o tratamento de dados de menores;
* defina a data de exclusão do banco e dos backups depois do relatório final;
* nunca versione o `.db`, os backups ou o CSV do credenciamento.

## Rotas

```text
GET  /health                                 diagnóstico (mostra se o banco é persistente)
GET  /api/stands                             lista de stands
POST /api/stands                             cadastra stand (devolve o código do expositor uma única vez)
POST /api/visitors/profile                   credenciamento único
GET  /api/visitors/passport?key=...          progresso do visitante
POST /api/stands/<id>/visits/start           inicia avaliação
POST /api/stands/<id>/visits/<vid>/finish    conclui avaliação (1 a 5)
POST /api/stands/<id>/engagement/share       registra compartilhamento por rede
POST /api/stands/<id>/engagement/support     mensagem para o mural (moderada)
GET  /api/mural                              mural público
GET  /api/admin/dashboard|report|export|...  painel do organizador (senha)
GET  /api/admin/backup                       backup JSON
GET  /api/admin/backup/sqlite                backup .db
GET  /api/admin/qr/<stand_id>                QR Code de qualquer stand
```

## Estrutura

```text
vocacao-feira-tech/
├── app.py                     rotas Flask, segurança básica
├── db_store.py                camada SQLite (regras de negócio)
├── migrate_json_to_sqlite.py  importa dados da v5
├── index.html
├── static/ (style.css, script.js)
├── smoke_test.py
├── reset_data.py
├── run.sh · run.command · run.bat
├── Procfile · render.yaml
├── requirements.txt
└── .env.example
```

## Checklist antes da Feira

```text
[ ] Hospedagem com disco persistente; /health mostra "persistent": true
[ ] ADMIN_PASSWORD forte definida; /admin abre só com ela
[ ] PUBLIC_URL definido e QR Codes gerados DEPOIS disso
[ ] python reset_data.py (ou banco novo) para apagar os dados de teste
[ ] Ensaio com 10+ celulares (iPhone e Android), no Wi-Fi e no 4G/5G
[ ] Rede do local aguenta o público (Wi-Fi com muitos aparelhos costuma ser o gargalo)
[ ] Aviso de privacidade (LGPD) no credenciamento
[ ] Backup .db baixado no meio e no fim do evento
[ ] Um notebook com o projeto rodando localmente como plano B
```
