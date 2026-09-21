"""
app.py — Servidor Flask da 1ª Feira Tech dos Jovens da Vocação.

Banco de dados: SQLite (db_store.py). Compatível com:
  - Desenvolvimento local: python app.py  →  http://localhost:5000
  - Render.com (plano pago + Persistent Disk) ou VPS: gunicorn app:app
  - NÃO use Vercel/serverless: o SQLite precisa de disco persistente.
"""

import io
import os
import re
import threading
import time
from datetime import datetime

import qrcode
from flask import Flask, Response, jsonify, request, send_from_directory

from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

import db_store as store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24).hex())

# Atrás do proxy da hospedagem (Render etc.) o app enxerga "http" e o IP do
# proxy. O ProxyFix corrige o esquema (https) dos QR Codes e o IP do cliente.
if store.is_hosted() or os.environ.get("BEHIND_PROXY") == "1":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Cria o banco/tabelas na subida: se o disco estiver errado, falha AGORA e
# não no meio do evento.
store.init_db()

DEFAULT_ADMIN_PASSWORD = store.DEFAULT_ADMIN_PASSWORD


def _public_base_url() -> str:
    """URL usada dentro dos QR Codes.

    Defina PUBLIC_URL (ex.: https://feira.vocacao.org.br) para que os QR
    Codes impressos continuem valendo se você trocar de hospedagem.
    """
    return (os.environ.get("PUBLIC_URL") or request.host_url).rstrip("/")


# ── Proteção contra tentativa de senha (força bruta) ───────────────────────

_GUARDED = re.compile(
    r"^/api/(admin/"
    r"|stands/[^/]+/(access|dashboard|qr|export)$"
    r"|stands/[^/]+/engagement/[^/]+/moderate$)"
)
_FAIL_WINDOW_SECONDS = 300
_FAIL_MAX = 20
_failures: dict[str, list[float]] = {}
_failures_lock = threading.Lock()


def _reset_rate_limits() -> None:
    with _failures_lock:
        _failures.clear()


def _recent_failures(ip: str) -> list[float]:
    now = time.monotonic()
    hits = [t for t in _failures.get(ip, []) if now - t < _FAIL_WINDOW_SECONDS]
    if hits:
        _failures[ip] = hits
    else:
        _failures.pop(ip, None)
    return hits


@app.before_request
def guard_sensitive_routes():
    if not _GUARDED.match(request.path):
        return None

    if request.path.startswith("/api/admin/") and not store.admin_configured():
        return jsonify({
            "error": "A senha do administrador não está configurada no servidor "
                     "(variável ADMIN_PASSWORD).",
        }), 503

    with _failures_lock:
        blocked = len(_recent_failures(request.remote_addr or "?")) >= _FAIL_MAX
    if blocked:
        return jsonify({"error": "Muitas tentativas incorretas. Aguarde alguns minutos."}), 429
    return None


# ── Tratamento de erros: sempre JSON nas rotas /api ────────────────────────

@app.errorhandler(HTTPException)
def handle_http_error(exc: HTTPException):
    if request.path.startswith("/api/"):
        return jsonify({"error": exc.description, "status": exc.code}), exc.code
    return exc


@app.errorhandler(Exception)
def handle_unexpected_error(exc: Exception):
    app.logger.exception("Erro inesperado em %s", request.path)
    if request.path.startswith("/api/"):
        return jsonify({"error": "Erro interno no servidor. Tente novamente."}), 500
    raise exc


@app.after_request
def common_headers(response):
    """Sem cache nas respostas dinâmicas + cabeçalhos básicos de segurança."""
    if request.path.startswith("/api/") or request.path == "/health":
        response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")

    # Conta tentativas erradas de senha/código para bloquear força bruta.
    if response.status_code == 401 and _GUARDED.match(request.path):
        with _failures_lock:
            _failures.setdefault(request.remote_addr or "?", []).append(time.monotonic())
    return response


# ── Páginas (SPA) ──────────────────────────────────────────────────────────

@app.route("/")
@app.route("/visitar/<stand_id>")
@app.route("/expositor")
@app.route("/stand/<stand_id>")
@app.route("/admin")
@app.route("/passaporte")
@app.route("/escanear")
@app.route("/relatorio")
def pages(stand_id=None):
    """Serve o SPA para todas as rotas de navegação."""
    response = send_from_directory(BASE_DIR, "index.html")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/static/<path:filename>")
def static_files(filename):
    # Em hospedagem, cache curto: centenas de celulares baixam o mesmo JS/CSS.
    max_age = 300 if store.is_hosted() else 0
    return send_from_directory(os.path.join(BASE_DIR, "static"), filename, max_age=max_age)


# ── API: Saúde / diagnóstico ───────────────────────────────────────────────

@app.get("/health")
def health():
    """Endpoint simples para testar se o servidor está vivo."""
    warning = store.storage_warning()
    return jsonify({
        "ok": True,
        "service": "feira-tech-vocacao",
        "storage": "sqlite",
        "data_path": store.DB_PATH,
        "persistent": warning is None,
        "storage_warning": warning,
        "admin_configured": store.admin_configured(),
        "admin_password_default": os.environ.get("ADMIN_PASSWORD") == DEFAULT_ADMIN_PASSWORD,
    })


# ── API: Stands ────────────────────────────────────────────────────────────

@app.get("/api/stands")
def list_stands():
    return jsonify(store.list_public_stands())


@app.get("/api/stands/<stand_id>")
def get_stand(stand_id):
    s = store.get_public_stand(stand_id)
    if not s:
        return jsonify({"error": "Stand não encontrado."}), 404
    return jsonify(s)


@app.post("/api/stands")
def create_stand():
    data   = request.get_json(silent=True) or {}
    name   = " ".join(str(data.get("name") or "").split())
    course = str(data.get("course") or "")

    if not 3 <= len(name) <= 100:
        return jsonify({"error": "Informe um nome entre 3 e 100 caracteres."}), 400

    # O nome aparece em painéis do organizador: nada de HTML no nome.
    if "<" in name or ">" in name:
        return jsonify({"error": "O nome não pode conter os caracteres < ou >."}), 400

    if course not in store.VALID_COURSES:
        return jsonify({
            "error": f"Curso inválido. Escolha: {', '.join(sorted(store.VALID_COURSES))}"
        }), 400

    try:
        stand, access_code = store.create_stand(name, course)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409

    return jsonify({"stand": stand, "access_code": access_code}), 201


@app.post("/api/stands/<stand_id>/access")
def access_stand(stand_id):
    data        = request.get_json(silent=True) or {}
    access_code = str(data.get("access_code") or "")
    s = store.authenticate_stand(stand_id, access_code)
    if not s:
        return jsonify({"error": "Código inválido."}), 401
    return jsonify(s)


@app.post("/api/stands/<stand_id>/recovery/request")
def request_code_recovery(stand_id):
    """Expositor que perdeu o código pede a liberação de um novo.

    O código antigo não pode ser mostrado (guardamos apenas o hash), então o
    pedido vai para o painel do organizador, que gera um código novo.
    """
    data      = request.get_json(silent=True) or {}
    requester = " ".join(str(data.get("requester") or "").split())[:80]

    if not 2 <= len(requester) <= 80 or "<" in requester or ">" in requester:
        return jsonify({"error": "Informe seu nome para a organização localizar você."}), 400

    result, error = store.request_code_reset(stand_id, requester)

    if error == "not_found":
        return jsonify({"error": "Stand não encontrado."}), 404

    return jsonify(result), 201


# ── API: Perfis de Visitante ───────────────────────────────────────────────

@app.post("/api/visitors/profile")
def save_profile():
    data         = request.get_json(silent=True) or {}
    visitor_key  = str(data.get("visitor_key") or "")
    profile_type = str(data.get("profile_type") or "")
    name         = " ".join(str(data.get("name") or "").split())[:80]
    contact      = " ".join(str(data.get("contact") or "").split())[:120]
    group_name   = str(data.get("group_name") or "").strip()
    department   = " ".join(str(data.get("department") or "").split())[:120]
    company_name = " ".join(str(data.get("company_name") or "").split())[:120]

    # Um aluno pode estar em mais de um curso (dias diferentes da semana) e
    # pode ter mais de um stand — um por curso/grupo em que participa.
    cursos = []
    for c in (data.get("cursos") or []):
        c = str(c or "").strip()
        if c in store.VALID_COURSES and c not in cursos:
            cursos.append(c)

    own_stand_ids = []
    for sid in (data.get("own_stand_ids") or []):
        sid = str(sid or "").strip()
        if sid and sid not in own_stand_ids:
            own_stand_ids.append(sid)

    if not re.fullmatch(r"[a-zA-Z0-9_-]{16,100}", visitor_key):
        return jsonify({"error": "Identificador de visitante inválido."}), 400

    if profile_type not in store.VALID_PROFILES:
        return jsonify({
            "error": f"Perfil inválido. Use: {', '.join(sorted(store.VALID_PROFILES))}"
        }), 400

    if not 2 <= len(name) <= 80 or "<" in name or ">" in name:
        return jsonify({"error": "Informe seu nome (2 a 80 caracteres)."}), 400

    for campo in (contact, group_name, department, company_name):
        if "<" in campo or ">" in campo:
            return jsonify({"error": "Há um caractere inválido em um dos campos."}), 400

    if profile_type == "aluno":
        if not contact:
            return jsonify({"error": "Informe um telefone ou e-mail para contato."}), 400
        if not cursos:
            return jsonify({"error": "Selecione ao menos um curso."}), 400
        # O aluno identifica o(s) próprio(s) stand(s) marcando na lista
        # (caminho rápido) ou digitando o nome de um grupo ainda sem stand.
        if not own_stand_ids and not 2 <= len(group_name) <= 100:
            return jsonify({
                "error": "Marque o stand do seu grupo ou digite o nome de um grupo ainda não cadastrado."
            }), 400
    else:
        cursos        = []
        own_stand_ids = []
        group_name    = ""
        if profile_type != "funcionario":
            department = ""
        if profile_type != "empresa":
            company_name = ""

    result = store.save_visitor_profile(
        visitor_key=visitor_key,
        profile_type=profile_type,
        name=name,
        contact=contact or None,
        cursos=cursos,
        group_name=group_name or None,
        own_stand_ids=own_stand_ids,
        department=department or None,
        company_name=company_name or None,
    )
    return jsonify(result), 201


@app.get("/api/visitors/profile")
def get_profile():
    visitor_key = request.args.get("key", "")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{16,100}", visitor_key):
        return jsonify({"error": "Identificador inválido."}), 400
    profile = store.get_visitor_profile(visitor_key)
    if not profile:
        return jsonify({"error": "Perfil não encontrado."}), 404
    return jsonify(profile)


@app.get("/api/visitors/passport")
def visitor_passport():
    """Progresso do visitante: o que já avaliou e o que falta visitar."""
    visitor_key = request.args.get("key", "")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{16,100}", visitor_key):
        return jsonify({"error": "Identificador inválido."}), 400
    passport = store.visitor_passport(visitor_key)
    if not passport:
        return jsonify({"error": "Faça seu credenciamento primeiro.", "profile_required": True}), 404
    return jsonify(passport)


# ── API: Avaliações (Visitas) ──────────────────────────────────────────────

@app.post("/api/stands/<stand_id>/visits/start")
def start_visit(stand_id):
    data        = request.get_json(silent=True) or {}
    visitor_key = str(data.get("visitor_key") or "")

    if not re.fullmatch(r"[a-zA-Z0-9_-]{16,100}", visitor_key):
        return jsonify({"error": "Identificador de visita inválido."}), 400

    # A API também enforça o perfil obrigatório; não dependemos apenas do front-end.
    if not store.get_visitor_profile(visitor_key):
        return jsonify({
            "error": "Selecione seu perfil antes de visitar um stand.",
            "profile_required": True,
        }), 403

    visit_id, error = store.start_visit(stand_id, visitor_key)

    if error == "not_found":
        return jsonify({"error": "Stand não encontrado."}), 404

    if error == "own_stand":
        return jsonify({
            "error":    "Você não pode avaliar o stand do seu próprio grupo. "
                        "Visite os projetos dos outros grupos!",
            "own_stand": True,
        }), 403

    if error == "duplicate":
        return jsonify({
            "error":     "Você já avaliou este stand neste dispositivo.",
            "duplicate": True,
        }), 409

    return jsonify({"visit_id": visit_id}), 201


@app.post("/api/stands/<stand_id>/visits/<visit_id>/finish")
def finish_visit(stand_id, visit_id):
    data        = request.get_json(silent=True) or {}
    visitor_key = str(data.get("visitor_key") or "")

    try:
        stars = int(data.get("stars"))
    except (TypeError, ValueError):
        stars = 0

    if stars not in range(1, 6):
        return jsonify({"error": "Escolha uma nota de 1 a 5 estrelas."}), 400

    visit, error = store.finish_visit(visit_id, stand_id, visitor_key, stars)

    if error == "duplicate":
        return jsonify({"error": "Avaliação já registrada.", "duplicate": True}), 409
    if error:
        return jsonify({"error": "Visita não encontrada."}), 404

    return jsonify({"ok": True, "duration_seconds": visit["duration_seconds"]})


# ── API: Engajamentos ──────────────────────────────────────────────────────

@app.post("/api/stands/<stand_id>/engagement/share")
def register_share(stand_id):
    data        = request.get_json(silent=True) or {}
    visitor_key = str(data.get("visitor_key") or "")
    network     = str(data.get("network") or "outro").strip().lower()

    if not re.fullmatch(r"[a-zA-Z0-9_-]{16,100}", visitor_key):
        return jsonify({"error": "Identificador inválido."}), 400

    if not store.register_share(stand_id, visitor_key, network):
        return jsonify({"error": "Conclua a avaliação antes de compartilhar."}), 403

    return jsonify({"ok": True, "network": network}), 201


@app.post("/api/stands/<stand_id>/engagement/support")
def create_support(stand_id):
    data        = request.get_json(silent=True) or {}
    visitor_key = str(data.get("visitor_key") or "")
    message     = " ".join(str(data.get("message") or "").split())

    if data.get("consent") is not True:
        return jsonify({"error": "Confirme a autorização para enviar ao mural."}), 400

    if not re.fullmatch(r"[a-zA-Z0-9_-]{16,100}", visitor_key):
        return jsonify({"error": "Identificador inválido."}), 400

    if not 8 <= len(message) <= 180 or "<" in message or ">" in message:
        return jsonify({"error": "Escreva uma mensagem entre 8 e 180 caracteres."}), 400

    support, error = store.create_support(stand_id, visitor_key, message)

    if error == "duplicate":
        return jsonify({"error": "Você já enviou um apoio para este projeto."}), 409
    if error:
        return jsonify({"error": "Conclua a avaliação antes de enviar seu apoio."}), 403

    return jsonify(support), 201


@app.post("/api/stands/<stand_id>/engagement/<engagement_id>/moderate")
def moderate_support(stand_id, engagement_id):
    data   = request.get_json(silent=True) or {}
    action = str(data.get("action") or "")

    if action not in {"approve", "reject"}:
        return jsonify({"error": "Ação inválida."}), 400

    result, error = store.moderate_support(
        stand_id, engagement_id, str(data.get("access_code") or ""), action
    )

    if error == "unauthorized":
        return jsonify({"error": "Código inválido."}), 401
    if error:
        return jsonify({"error": "Publicação não encontrada."}), 404

    return jsonify(result)


@app.get("/api/mural")
def public_wall():
    return jsonify(store.public_wall())


# ── API: Dashboard do Expositor ────────────────────────────────────────────

@app.get("/api/stands/<stand_id>/dashboard")
def stand_dashboard(stand_id):
    report = store.dashboard(stand_id, request.args.get("code", ""))
    if not report:
        return jsonify({"error": "Código inválido."}), 401
    return jsonify(report)


@app.get("/api/stands/<stand_id>/qr")
def stand_qr(stand_id):
    access_code = request.args.get("code", "")
    if not store.authenticate_stand(stand_id, access_code):
        return jsonify({"error": "Código inválido."}), 401

    return _qr_response(_public_base_url() + f"/visitar/{stand_id}")


def _qr_response(visit_url: str) -> Response:
    """Gera o PNG do QR Code que leva direto para a avaliação do stand."""
    image  = qrcode.make(visit_url)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return Response(
        output.getvalue(),
        mimetype="image/png",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/stands/<stand_id>/export")
def export_visits(stand_id):
    csv_bytes = store.export_visits_csv(stand_id, request.args.get("code", ""))
    if csv_bytes is None:
        return jsonify({"error": "Código inválido."}), 401
    return Response(
        csv_bytes,
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="visitas-{stand_id}.csv"'},
    )


# ── API: Admin Global ──────────────────────────────────────────────────────

@app.get("/api/admin/dashboard")
def admin_dashboard():
    report = store.admin_dashboard(request.args.get("password", ""))
    if not report:
        return jsonify({"error": "Senha de administrador inválida."}), 401
    return jsonify(report)


@app.get("/api/admin/recovery/requests")
def admin_recovery_requests():
    pedidos = store.list_code_requests(request.args.get("password", ""))
    if pedidos is None:
        return jsonify({"error": "Senha de administrador inválida."}), 401
    return jsonify(pedidos)


@app.post("/api/admin/stands/<stand_id>/reset-code")
def admin_reset_code(stand_id):
    """Gera um novo código de acesso. O código aparece uma única vez."""
    data = request.get_json(silent=True) or {}
    result, error = store.admin_reset_stand_code(
        str(data.get("password") or ""),
        stand_id,
        str(data.get("request_id") or "") or None,
    )

    if error == "unauthorized":
        return jsonify({"error": "Senha de administrador inválida."}), 401
    if error == "not_found":
        return jsonify({"error": "Stand não encontrado."}), 404

    return jsonify(result), 201


@app.get("/api/admin/report")
def admin_report():
    report = store.admin_report(request.args.get("password", ""))
    if report is None:
        return jsonify({"error": "Senha de administrador inválida."}), 401
    return jsonify(report)


@app.get("/api/admin/supports/export")
def admin_supports_export():
    csv_bytes = store.admin_supports_csv(request.args.get("password", ""))
    if csv_bytes is None:
        return jsonify({"error": "Senha de administrador inválida."}), 401
    return Response(
        csv_bytes,
        mimetype="text/csv",
        headers={"Content-Disposition": 'attachment; filename="mural-feira-tech.csv"'},
    )


@app.get("/api/admin/backup")
def admin_backup():
    """Cópia de segurança completa em JSON, para não perder o histórico."""
    payload = store.admin_backup(request.args.get("password", ""))
    if payload is None:
        return jsonify({"error": "Senha de administrador inválida."}), 401
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    return Response(
        payload,
        mimetype="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="backup-feira-tech-{stamp}.json"'
        },
    )


@app.get("/api/admin/backup/sqlite")
def admin_backup_sqlite():
    """Cópia consistente do arquivo .db (segura com o servidor em uso)."""
    payload = store.admin_backup_sqlite(request.args.get("password", ""))
    if payload is None:
        return jsonify({"error": "Senha de administrador inválida."}), 401
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    return Response(
        payload,
        mimetype="application/x-sqlite3",
        headers={
            "Content-Disposition": f'attachment; filename="feira-tech-{stamp}.db"'
        },
    )


@app.get("/api/admin/qr/<stand_id>")
def admin_stand_qr(stand_id):
    """QR Code de qualquer stand, para o organizador imprimir todos de uma vez."""
    if not store.verify_admin_password(request.args.get("password", "")):
        return jsonify({"error": "Senha de administrador inválida."}), 401
    if not store.get_public_stand(stand_id):
        return jsonify({"error": "Stand não encontrado."}), 404
    return _qr_response(_public_base_url() + f"/visitar/{stand_id}")


@app.get("/api/admin/visitors/export")
def admin_visitors_export():
    csv_bytes = store.admin_visitors_csv(request.args.get("password", ""))
    if csv_bytes is None:
        return jsonify({"error": "Senha de administrador inválida."}), 401
    return Response(
        csv_bytes,
        mimetype="text/csv",
        headers={"Content-Disposition": 'attachment; filename="credenciamento-feira-tech.csv"'},
    )


@app.get("/api/admin/export")
def admin_export():
    csv_bytes = store.admin_export_csv(request.args.get("password", ""))
    if csv_bytes is None:
        return jsonify({"error": "Senha de administrador inválida."}), 401
    return Response(
        csv_bytes,
        mimetype="text/csv",
        headers={"Content-Disposition": 'attachment; filename="feira-tech-completo.csv"'},
    )


# ── Inicialização local ────────────────────────────────────────────────────

def _find_available_port(preferred: int = 5000, attempts: int = 20) -> int:
    """Retorna uma porta livre para o servidor local.

    Se PORT foi definida pelo ambiente, ela é respeitada exatamente.
    Caso contrário, tenta 5000, 5001, 5002... evitando o erro
    "Address already in use" quando outro processo estiver usando a porta.
    """
    import socket

    if os.environ.get("PORT"):
        return int(os.environ["PORT"])

    host = os.environ.get("HOST", "127.0.0.1")
    bind_host = "" if host in {"0.0.0.0", "::"} else host

    for port in range(preferred, preferred + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((bind_host, port))
                return port
            except OSError:
                continue

    raise RuntimeError(
        f"Nenhuma porta livre encontrada entre {preferred} e "
        f"{preferred + attempts - 1}. Defina PORT manualmente."
    )


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "sim", "on"}


if __name__ == "__main__":
    port = _find_available_port()
    host = os.environ.get("HOST", "127.0.0.1")
    debug = _env_bool("FLASK_DEBUG", False)

    print("\n" + "=" * 56)
    print("  1ª Feira Tech dos Jovens da Vocação")
    print("=" * 56)
    print(f"  URL:   http://localhost:{port}")
    print(f"  Admin: http://localhost:{port}/admin")
    print(f"  Debug: {'ON' if debug else 'OFF'}")
    print(f"  Dados: {store.DB_PATH}")
    if not os.environ.get("ADMIN_PASSWORD"):
        # Só no seu computador: em hospedagem a senha padrão é recusada.
        os.environ["ADMIN_PASSWORD"] = DEFAULT_ADMIN_PASSWORD
        print("  ⚠️  ADMIN_PASSWORD não definida; usando a senha padrão local.")
    if port != 5000 and not os.environ.get("PORT"):
        print("  ℹ️  A porta 5000 estava ocupada; usando uma porta livre.")
    print("  Para encerrar: Ctrl+C")
    print("=" * 56 + "\n")

    # O reloader do Flask fica desligado por padrão para evitar que o
    # processo seja iniciado duas vezes e provoque conflito de porta.
    app.run(
        host=host,
        port=port,
        debug=debug,
        use_reloader=debug,
    )
