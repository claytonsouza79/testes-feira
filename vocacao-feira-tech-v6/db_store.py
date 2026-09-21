"""
db_store.py — Camada de dados SQLite da Feira Tech dos Jovens da Vocação.

Substitui o antigo json_store.py mantendo as MESMAS funções e os mesmos
formatos de resposta, então app.py e o front-end quase não mudam.

Por que SQLite aqui:
  - o volume de uma feira (centenas de celulares, alguns milhares de notas)
    é pequeno para o SQLite;
  - é um arquivo único, fácil de copiar/fazer backup;
  - modo WAL: várias leituras simultâneas + uma escrita por vez, sem
    reescrever o arquivo inteiro a cada voto (o JSON reescrevia tudo).

ATENÇÃO — onde o arquivo fica:
  SQLite precisa de um disco que NÃO seja apagado entre reinícios.
    ✔ seu computador / notebook na rede local
    ✔ VPS (Hetzner, Contabo, DigitalOcean...)
    ✔ Render com Persistent Disk (plano pago)
    ✘ Vercel / AWS Lambda (sistema de arquivos somente leitura ou efêmero)
    ✘ Render plano gratuito (disco apagado a cada reinício/"sono")
  Defina DATA_PATH apontando para o disco persistente, ex.: /var/data/feira_tech.db

Regras de negócio (idênticas às da versão JSON):
  1. Um stand por grupo (unicidade via slug normalizado)
  2. Perfil do visitante obrigatório antes de avaliar
  3. Trava de Ouro: aluno não avalia o próprio stand
  4. Um voto por stand por dispositivo (garantido por índice UNIQUE no banco)
  5. Código de acesso nunca salvo em texto plano (PBKDF2 + hmac.compare_digest)
  6. Moderação de mensagens no mural público
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import sqlite3
import tempfile
import threading
import time
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime

log = logging.getLogger("feira.db")

# ── Configurações ──────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMA_VERSION = 1

DEFAULT_ADMIN_PASSWORD = "admin@feira2025"

_HOSTED_ENV_VARS = (
    "RENDER", "VERCEL", "VERCEL_ENV", "AWS_LAMBDA_FUNCTION_NAME",
    "LAMBDA_TASK_ROOT", "DYNO", "FLY_APP_NAME", "RAILWAY_ENVIRONMENT",
)


def is_hosted() -> bool:
    """True quando o app roda numa plataforma de hospedagem (não no seu PC)."""
    return any(os.environ.get(name) for name in _HOSTED_ENV_VARS)


def _resolve_db_path() -> str:
    """Escolhe onde fica o arquivo do banco.

    Prioridade:
      1. DATA_PATH (aponte para o disco persistente da hospedagem);
      2. pasta temporária, se o projeto for somente leitura (Vercel/Lambda);
      3. ./data/feira_tech.db ao lado do código (uso local).
    """
    custom = os.environ.get("DATA_PATH")
    if custom:
        return os.path.abspath(custom)

    local_dir = os.path.join(BASE_DIR, "data")
    serverless = any(
        os.environ.get(n)
        for n in ("VERCEL", "VERCEL_ENV", "AWS_LAMBDA_FUNCTION_NAME", "LAMBDA_TASK_ROOT")
    )
    if serverless or not os.access(BASE_DIR, os.W_OK):
        return os.path.join(tempfile.gettempdir(), "feira_tech.db")

    return os.path.join(local_dir, "feira_tech.db")


DB_PATH = _resolve_db_path()


def storage_warning() -> str | None:
    """Aviso quando o banco provavelmente será apagado num reinício."""
    tmp = os.path.realpath(tempfile.gettempdir())
    if os.path.realpath(DB_PATH).startswith(tmp + os.sep) and is_hosted():
        return "O banco está em pasta temporária: os dados serão perdidos ao reiniciar."
    if is_hosted() and not os.environ.get("DATA_PATH"):
        return (
            "DATA_PATH não definido: em hospedagem, o banco fica em disco efêmero "
            "e será apagado a cada reinício. Aponte DATA_PATH para um disco persistente."
        )
    return None


# Cursos da 1ª Feira Tech dos Jovens da Vocação
VALID_COURSES = {"webdesign", "programacao", "audiovisual", "ppt"}

COURSE_LABELS = {
    "webdesign":   "Web Design",
    "programacao": "Programação",
    "audiovisual": "Audiovisual",
    "ppt":         "Prep. para o Trabalho",
}

# Perfis de visitante
VALID_PROFILES = {"aluno", "funcionario", "visitante_externo", "empresa"}

PROFILE_LABELS = {
    "aluno":             "Aluno Vocação",
    "funcionario":       "Funcionário Vocação",
    "visitante_externo": "Visitante Externo",
    "empresa":           "Empresa",
}

VALID_NETWORKS = {
    "instagram", "instagram_stories", "tiktok", "facebook", "x",
    "whatsapp", "linkedin", "telegram", "download", "outro",
}

NETWORK_LABELS = {
    "instagram":         "Instagram",
    "instagram_stories": "Instagram Stories",
    "tiktok":            "TikTok",
    "facebook":          "Facebook",
    "x":                 "X (Twitter)",
    "whatsapp":          "WhatsApp",
    "linkedin":          "LinkedIn",
    "telegram":          "Telegram",
    "download":          "Imagem salva",
    "outro":             "Outros",
}

# ── Esquema do banco ───────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS stands (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    group_id       TEXT NOT NULL UNIQUE,
    course         TEXT NOT NULL,
    code_salt      TEXT NOT NULL,
    code_hash      TEXT NOT NULL,
    code_reset_at  TEXT,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS visitors (
    visitor_hash   TEXT PRIMARY KEY,
    profile_type   TEXT NOT NULL,
    name           TEXT,
    contact        TEXT,
    cursos         TEXT NOT NULL DEFAULT '[]',
    group_id       TEXT,
    own_stand_ids  TEXT NOT NULL DEFAULT '[]',
    department     TEXT,
    company_name   TEXT,
    created_at     TEXT NOT NULL,
    checked_in_at  TEXT,
    updated_at     TEXT
);

CREATE TABLE IF NOT EXISTS visits (
    id                TEXT PRIMARY KEY,
    stand_id          TEXT NOT NULL REFERENCES stands(id) ON DELETE CASCADE,
    stand_nome        TEXT NOT NULL,
    curso             TEXT,
    visitor_hash      TEXT NOT NULL,
    profile_type      TEXT,
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    duration_seconds  INTEGER,
    stars             INTEGER CHECK (stars IS NULL OR stars BETWEEN 1 AND 5),
    -- Um voto por stand por dispositivo, garantido pelo próprio banco
    UNIQUE (stand_id, visitor_hash)
);
CREATE INDEX IF NOT EXISTS ix_visits_visitor  ON visits(visitor_hash);
CREATE INDEX IF NOT EXISTS ix_visits_finished ON visits(finished_at);

CREATE TABLE IF NOT EXISTS engagements (
    id            TEXT PRIMARY KEY,
    stand_id      TEXT NOT NULL REFERENCES stands(id) ON DELETE CASCADE,
    stand_nome    TEXT NOT NULL,
    visitor_hash  TEXT NOT NULL,
    type          TEXT NOT NULL CHECK (type IN ('share', 'support')),
    network       TEXT,
    message       TEXT,
    stars         INTEGER,
    status        TEXT CHECK (status IS NULL OR status IN ('pending', 'approved', 'rejected')),
    created_at    TEXT NOT NULL,
    moderated_at  TEXT
);
-- Um apoio (mensagem no mural) por visitante por stand
CREATE UNIQUE INDEX IF NOT EXISTS ux_support_once
    ON engagements(stand_id, visitor_hash) WHERE type = 'support';
CREATE INDEX IF NOT EXISTS ix_eng_stand ON engagements(stand_id);

CREATE TABLE IF NOT EXISTS recoveries (
    id           TEXT PRIMARY KEY,
    stand_id     TEXT NOT NULL REFERENCES stands(id) ON DELETE CASCADE,
    stand_nome   TEXT NOT NULL,
    requester    TEXT,
    status       TEXT NOT NULL CHECK (status IN ('pending', 'resolved')),
    created_at   TEXT NOT NULL,
    resolved_at  TEXT
);
-- No máximo um pedido pendente por stand
CREATE UNIQUE INDEX IF NOT EXISTS ux_recovery_pending
    ON recoveries(stand_id) WHERE status = 'pending';
"""

_init_lock = threading.Lock()
_initialized: set[str] = set()


def _ensure_schema(path: str) -> None:
    if path in _initialized:
        return
    with _init_lock:
        if path in _initialized:
            return
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

        # Dois workers do gunicorn podem subir juntos: tenta de novo se o
        # arquivo estiver momentaneamente travado.
        last_error: Exception | None = None
        for attempt in range(8):
            try:
                conn = sqlite3.connect(path, timeout=30, isolation_level=None)
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.executescript(SCHEMA)
                    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                finally:
                    conn.close()
                _initialized.add(path)
                return
            except sqlite3.OperationalError as exc:
                last_error = exc
                time.sleep(0.25 * (attempt + 1))
        raise RuntimeError(f"Não foi possível preparar o banco em {path}: {last_error}")


def init_db() -> str:
    """Cria o arquivo e as tabelas, se ainda não existirem. Retorna o caminho."""
    _ensure_schema(DB_PATH)
    return DB_PATH


def _connect() -> sqlite3.Connection:
    _ensure_schema(DB_PATH)
    conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=ON")
    # WAL + NORMAL: seguro contra corrupção; no pior caso (queda de energia)
    # perde-se a última gravação, nunca o arquivo.
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def _read():
    """Leitura com 'foto' consistente do banco (vários SELECTs coerentes)."""
    conn = _connect()
    try:
        conn.execute("BEGIN")
        yield conn
    finally:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        conn.close()


@contextmanager
def _write():
    """Transação de escrita. BEGIN IMMEDIATE evita conflitos de 'upgrade' de trava."""
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        else:
            conn.execute("COMMIT")
    finally:
        conn.close()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ── Utilitários ────────────────────────────────────────────────────────────

def normalize_group(name: str) -> str:
    """
    Converte nome do grupo em slug comparável.
    Exemplo: "Horta Inteligente!" → "horta-inteligente"
    Garante a unicidade mesmo com variações de escrita.
    """
    name = name.strip().lower()
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    name = re.sub(r"[^a-z0-9\s]", "", name)
    name = re.sub(r"\s+", "-", name.strip())
    return name


def _hash_visitor(visitor_key: str) -> str:
    """SHA-256 da visitor_key. Nunca armazenamos a chave original."""
    return hashlib.sha256(visitor_key.encode()).hexdigest()


def _hash_code(code: str, salt: str) -> str:
    """Derivação PBKDF2-HMAC-SHA256 do código de acesso do expositor."""
    return hashlib.pbkdf2_hmac("sha256", code.encode(), salt.encode(), 120_000).hex()


def _verify_code(stand: sqlite3.Row | None, access_code: str) -> bool:
    """Compara código via hmac.compare_digest (evita timing attacks)."""
    if not stand or not access_code:
        return False
    expected = _hash_code(access_code, stand["code_salt"])
    return hmac.compare_digest(expected, stand["code_hash"])


def admin_configured() -> bool:
    """A senha do admin foi definida de forma aceitável?

    Em hospedagem, a senha padrão do README NÃO é aceita: ela é pública.
    """
    pw = os.environ.get("ADMIN_PASSWORD", "")
    if not pw:
        return False
    if pw == DEFAULT_ADMIN_PASSWORD and is_hosted():
        return False
    return True


def _verify_admin(password: str) -> bool:
    """Valida a senha do administrador via hmac.compare_digest (em bytes)."""
    if not admin_configured():
        return False
    expected = os.environ["ADMIN_PASSWORD"]
    return hmac.compare_digest(password.encode("utf-8"), expected.encode("utf-8"))


def verify_admin_password(password: str) -> bool:
    """Exposto para rotas que só precisam validar o administrador."""
    return _verify_admin(password)


def _public_stand(row: sqlite3.Row) -> dict:
    """Somente campos públicos do stand (sem salt e hash)."""
    return {
        "id":         row["id"],
        "name":       row["name"],
        "course":     row["course"],
        "group_id":   row["group_id"] or "",
        "created_at": row["created_at"],
    }


def _get_stand(conn: sqlite3.Connection, stand_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM stands WHERE id = ?", (stand_id,)).fetchone()


def _json_list(raw: str | None) -> list:
    try:
        value = json.loads(raw or "[]")
        return value if isinstance(value, list) else []
    except (TypeError, ValueError):
        return []


def _new_access_code() -> str:
    return "".join(secrets.choice("23456789ABCDEFGHJKMNPQRSTUVWXYZ") for _ in range(6))


def _avg(values: list[int | float], digits: int = 2) -> float:
    return round(sum(values) / len(values), digits) if values else 0


# ── Stands ─────────────────────────────────────────────────────────────────

def list_public_stands() -> list[dict]:
    with _read() as c:
        rows = c.execute("SELECT * FROM stands ORDER BY rowid").fetchall()
        return [_public_stand(r) for r in rows]


def get_public_stand(stand_id: str) -> dict | None:
    with _read() as c:
        row = _get_stand(c, stand_id)
        return _public_stand(row) if row else None


def create_stand(name: str, course: str) -> tuple[dict, str]:
    """
    Cadastra novo stand.
    TRAVA 1: unicidade por slug do nome do grupo.
    Retorna (stand_dict, access_code) — código exibido uma única vez.
    """
    group_id = normalize_group(name)
    if not group_id:
        raise ValueError("Use letras ou números no nome do grupo.")

    access_code = _new_access_code()
    salt = secrets.token_hex(16)
    code_hash = _hash_code(access_code, salt)   # fora da transação (é lento)
    stand_id = uuid.uuid4().hex[:12]

    duplicate_msg = (
        f"O grupo '{name}' já possui um stand cadastrado. "
        "Cada grupo pode ter apenas 1 stand na feira."
    )
    try:
        with _write() as c:
            if c.execute("SELECT 1 FROM stands WHERE group_id = ?", (group_id,)).fetchone():
                raise ValueError(duplicate_msg)
            c.execute(
                "INSERT INTO stands (id, name, group_id, course, code_salt, code_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (stand_id, name, group_id, course, salt, code_hash, _now()),
            )
            row = _get_stand(c, stand_id)
            return _public_stand(row), access_code
    except sqlite3.IntegrityError:
        raise ValueError(duplicate_msg)


def authenticate_stand(stand_id: str, access_code: str) -> dict | None:
    with _read() as c:
        row = _get_stand(c, stand_id)
    return _public_stand(row) if _verify_code(row, access_code) else None


# ── Recuperação do código de acesso do expositor ───────────────────────────

def request_code_reset(stand_id: str, requester: str) -> tuple[dict | None, str | None]:
    """Aluno expositor pede um novo código.

    O código antigo nunca é revelado: ele é guardado apenas como hash. O
    pedido fica pendente até o organizador liberar no painel administrativo,
    o que evita que qualquer pessoa assuma o stand de outro grupo.
    """
    with _write() as c:
        stand = _get_stand(c, stand_id)
        if not stand:
            return None, "not_found"

        pendente = c.execute(
            "SELECT id FROM recoveries WHERE stand_id = ? AND status = 'pending'",
            (stand_id,),
        ).fetchone()
        if pendente:
            return {"id": pendente["id"], "status": "pending", "duplicate": True}, None

        pedido_id = uuid.uuid4().hex
        c.execute(
            "INSERT INTO recoveries (id, stand_id, stand_nome, requester, status, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
            (pedido_id, stand_id, stand["name"], requester, _now()),
        )
        return {"id": pedido_id, "status": "pending"}, None


def admin_reset_stand_code(
    password: str, stand_id: str, request_id: str | None = None
) -> tuple[dict | None, str | None]:
    """Gera um novo código de acesso para o stand (somente o organizador).

    Retorna o código em texto puro uma única vez; no banco continua
    guardado apenas o hash PBKDF2.
    """
    if not _verify_admin(password):
        return None, "unauthorized"

    novo_codigo = _new_access_code()
    salt = secrets.token_hex(16)
    code_hash = _hash_code(novo_codigo, salt)
    agora = _now()

    with _write() as c:
        stand = _get_stand(c, stand_id)
        if not stand:
            return None, "not_found"

        c.execute(
            "UPDATE stands SET code_salt = ?, code_hash = ?, code_reset_at = ? WHERE id = ?",
            (salt, code_hash, agora, stand_id),
        )
        # Encerra os pedidos pendentes deste stand
        c.execute(
            "UPDATE recoveries SET status = 'resolved', resolved_at = ? "
            "WHERE stand_id = ? AND status = 'pending' AND (? IS NULL OR id = ?)",
            (agora, stand_id, request_id, request_id),
        )
        return {"stand": _public_stand(_get_stand(c, stand_id)), "access_code": novo_codigo}, None


def list_code_requests(password: str) -> list[dict] | None:
    if not _verify_admin(password):
        return None
    with _read() as c:
        rows = c.execute(
            "SELECT id, stand_id, stand_nome, requester, created_at FROM recoveries "
            "WHERE status = 'pending' ORDER BY rowid DESC"
        ).fetchall()
        return [
            {
                "id":         r["id"],
                "stand_id":   r["stand_id"],
                "stand_name": r["stand_nome"],
                "requester":  r["requester"] or "",
                "created_at": r["created_at"],
            }
            for r in rows
        ]


# ── Perfis de Visitante ────────────────────────────────────────────────────

def save_visitor_profile(
    visitor_key: str,
    profile_type: str,
    name: str,
    contact: str | None = None,
    cursos: list[str] | None = None,
    group_name: str | None = None,
    own_stand_ids: list[str] | None = None,
    department: str | None = None,
    company_name: str | None = None,
) -> dict:
    """
    Cadastro único do visitante (credenciamento).

    Feito uma só vez por aparelho. Depois disso, cada QR Code lido leva
    direto para a avaliação, sem novo cadastro. Campos por perfil:
      - aluno:             nome, contato, um ou mais cursos, um ou mais
                           stands do grupo (a Trava de Ouro protege todos);
      - funcionário:       nome, contato e departamento/setor;
      - visitante externo: nome e contato;
      - empresa:           nome do representante, contato e nome da empresa.

    Para alunos guardamos duas referências usadas pela Trava de Ouro:
      - own_stand_ids: os stands escolhidos na lista (caminho preferencial);
      - group_id: slug do nome digitado manualmente (para um grupo que
        ainda não tenha stand cadastrado).
    """
    visitor_hash = _hash_visitor(visitor_key)
    is_aluno = profile_type == "aluno"
    cursos = list(dict.fromkeys(cursos or [])) if is_aluno else []
    normalized_group = normalize_group(group_name) if (group_name and is_aluno) else None
    now = _now()

    with _write() as c:
        own_ids: list[str] = []
        if is_aluno and own_stand_ids:
            existentes = {
                r["id"] for r in c.execute(
                    f"SELECT id FROM stands WHERE id IN ({','.join('?' * len(own_stand_ids))})",
                    list(own_stand_ids),
                )
            }
            own_ids = [sid for sid in own_stand_ids if sid in existentes]

        # Se o aluno escolheu algum stand na lista, herdamos o group_id
        # oficial do primeiro deles.
        if own_ids and not normalized_group:
            primeiro = _get_stand(c, own_ids[0])
            if primeiro:
                normalized_group = primeiro["group_id"] or normalized_group

        c.execute(
            """
            INSERT INTO visitors (visitor_hash, profile_type, name, contact, cursos, group_id,
                                  own_stand_ids, department, company_name,
                                  created_at, checked_in_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(visitor_hash) DO UPDATE SET
                profile_type  = excluded.profile_type,
                name          = excluded.name,
                contact       = excluded.contact,
                cursos        = excluded.cursos,
                group_id      = excluded.group_id,
                own_stand_ids = excluded.own_stand_ids,
                department    = excluded.department,
                company_name  = excluded.company_name,
                updated_at    = ?
            """,
            (
                visitor_hash, profile_type, name, contact,
                json.dumps(cursos, ensure_ascii=False), normalized_group,
                json.dumps(own_ids), department if profile_type == "funcionario" else None,
                company_name if profile_type == "empresa" else None,
                now, now, now,
            ),
        )
    return {"ok": True, "profile_type": profile_type, "name": name}


def _profile_payload(v: sqlite3.Row) -> dict:
    return {
        "profile_type":  v["profile_type"],
        "name":          v["name"],
        "cursos":        _json_list(v["cursos"]),
        "group_id":      v["group_id"],
        "own_stand_ids": _json_list(v["own_stand_ids"]),
        "department":    v["department"],
        "company_name":  v["company_name"],
        "checked_in_at": v["checked_in_at"] or v["created_at"],
    }


def _find_visitor(conn: sqlite3.Connection, visitor_hash: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM visitors WHERE visitor_hash = ?", (visitor_hash,)
    ).fetchone()


def get_visitor_profile(visitor_key: str) -> dict | None:
    with _read() as c:
        v = _find_visitor(c, _hash_visitor(visitor_key))
        return _profile_payload(v) if v else None


# ── Passaporte do visitante ────────────────────────────────────────────────

def visitor_passport(visitor_key: str) -> dict | None:
    """Progresso do visitante: stands já avaliados e stands restantes."""
    visitor_hash = _hash_visitor(visitor_key)
    with _read() as c:
        visitor = _find_visitor(c, visitor_hash)
        if not visitor:
            return None

        rated = {
            r["stand_id"]: r["stars"]
            for r in c.execute(
                "SELECT stand_id, stars FROM visits "
                "WHERE visitor_hash = ? AND finished_at IS NOT NULL",
                (visitor_hash,),
            )
        }
        own_ids = set(_json_list(visitor["own_stand_ids"]))
        own_group = visitor["group_id"]
        is_aluno = visitor["profile_type"] == "aluno"

        stands = []
        for s in c.execute("SELECT id, name, course, group_id FROM stands ORDER BY rowid"):
            is_own = bool(
                is_aluno and (s["id"] in own_ids or (own_group and s["group_id"] == own_group))
            )
            stands.append({
                "id":     s["id"],
                "name":   s["name"],
                "course": s["course"],
                "own":    is_own,
                "rated":  s["id"] in rated,
                "stars":  rated.get(s["id"]),
            })

        available = [s for s in stands if not s["own"]]
        return {
            "profile":      _profile_payload(visitor),
            "stands":       stands,
            "rated":        sum(1 for s in available if s["rated"]),
            "available":    len(available),
            "total_stands": len(stands),
        }


# ── Avaliações (Visitas) ───────────────────────────────────────────────────

def start_visit(stand_id: str, visitor_key: str) -> tuple[str | None, str | None]:
    """
    Inicia uma visita ao stand.
    TRAVA DE OURO: aluno não pode avaliar o próprio stand.
    TRAVA DE DUPLICATA: um voto por stand por dispositivo.
    """
    visitor_hash = _hash_visitor(visitor_key)

    with _write() as c:
        stand = _get_stand(c, stand_id)
        if not stand:
            return None, "not_found"

        visitor = _find_visitor(c, visitor_hash)
        if visitor and visitor["profile_type"] == "aluno":
            own_ids = set(_json_list(visitor["own_stand_ids"]))
            if stand_id in own_ids:
                return None, "own_stand"
            if visitor["group_id"] and stand["group_id"] and visitor["group_id"] == stand["group_id"]:
                return None, "own_stand"

        existing = c.execute(
            "SELECT id, finished_at FROM visits WHERE stand_id = ? AND visitor_hash = ?",
            (stand_id, visitor_hash),
        ).fetchone()
        if existing:
            if existing["finished_at"]:
                return None, "duplicate"
            return existing["id"], None          # reutiliza a visita em andamento

        visit_id = uuid.uuid4().hex
        c.execute(
            "INSERT INTO visits (id, stand_id, stand_nome, curso, visitor_hash, profile_type, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                visit_id, stand_id, stand["name"], stand["course"], visitor_hash,
                visitor["profile_type"] if visitor else None, _now(),
            ),
        )
        return visit_id, None


def finish_visit(
    visit_id: str, stand_id: str, visitor_key: str, stars: int
) -> tuple[dict | None, str | None]:
    visitor_hash = _hash_visitor(visitor_key)

    with _write() as c:
        visit = c.execute(
            "SELECT * FROM visits WHERE id = ? AND stand_id = ? AND visitor_hash = ?",
            (visit_id, stand_id, visitor_hash),
        ).fetchone()
        if not visit:
            return None, "not_found"
        if visit["finished_at"]:
            return None, "duplicate"

        finished_at = datetime.now().astimezone()
        started_at = datetime.fromisoformat(visit["started_at"])
        duration = max(1, min(int((finished_at - started_at).total_seconds()), 43_200))

        c.execute(
            "UPDATE visits SET finished_at = ?, duration_seconds = ?, stars = ? "
            "WHERE id = ? AND finished_at IS NULL",
            (finished_at.isoformat(timespec="seconds"), duration, stars, visit_id),
        )
        return {"id": visit_id, "stars": stars, "duration_seconds": duration}, None


# ── Engajamentos ───────────────────────────────────────────────────────────

def _finished_visit(conn: sqlite3.Connection, stand_id: str, visitor_hash: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM visits WHERE stand_id = ? AND visitor_hash = ? AND finished_at IS NOT NULL",
        (stand_id, visitor_hash),
    ).fetchone()


def register_share(stand_id: str, visitor_key: str, network: str = "outro") -> bool:
    """Registra um compartilhamento, identificando a rede social usada."""
    network = network if network in VALID_NETWORKS else "outro"
    visitor_hash = _hash_visitor(visitor_key)

    with _write() as c:
        stand = _get_stand(c, stand_id)
        visit = _finished_visit(c, stand_id, visitor_hash)
        if not stand or not visit:
            return False
        c.execute(
            "INSERT INTO engagements (id, stand_id, stand_nome, visitor_hash, type, network, created_at) "
            "VALUES (?, ?, ?, ?, 'share', ?, ?)",
            (uuid.uuid4().hex, stand_id, stand["name"], visitor_hash, network, _now()),
        )
        return True


def _shares_by_network(networks: list[str | None]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for net in networks:
        net = net or "outro"
        counts[net] = counts.get(net, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))


def create_support(
    stand_id: str, visitor_key: str, message: str
) -> tuple[dict | None, str | None]:
    visitor_hash = _hash_visitor(visitor_key)

    try:
        with _write() as c:
            stand = _get_stand(c, stand_id)
            visit = _finished_visit(c, stand_id, visitor_hash)
            if not stand or not visit:
                return None, "visit_required"

            support_id = uuid.uuid4().hex
            c.execute(
                "INSERT INTO engagements (id, stand_id, stand_nome, visitor_hash, type, message, "
                "stars, status, created_at) VALUES (?, ?, ?, ?, 'support', ?, ?, 'pending', ?)",
                (support_id, stand_id, stand["name"], visitor_hash, message, visit["stars"], _now()),
            )
            return {"id": support_id, "status": "pending"}, None
    except sqlite3.IntegrityError:
        return None, "duplicate"          # índice ux_support_once


def moderate_support(
    stand_id: str, engagement_id: str, access_code: str, action: str
) -> tuple[dict | None, str | None]:
    with _read() as c:
        stand = _get_stand(c, stand_id)
    if not _verify_code(stand, access_code):    # PBKDF2 fora da transação de escrita
        return None, "unauthorized"

    status = "approved" if action == "approve" else "rejected"
    with _write() as c:
        cur = c.execute(
            "UPDATE engagements SET status = ?, moderated_at = ? "
            "WHERE id = ? AND stand_id = ? AND type = 'support'",
            (status, _now(), engagement_id, stand_id),
        )
        if cur.rowcount == 0:
            return None, "not_found"
        return {"id": engagement_id, "status": status}, None


def public_wall(limit: int = 24) -> list[dict]:
    with _read() as c:
        rows = c.execute(
            "SELECT id, stand_id, stand_nome, message, stars, created_at FROM engagements "
            "WHERE type = 'support' AND status = 'approved' "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "id":         r["id"],
                "stand_id":   r["stand_id"],
                "stand_name": r["stand_nome"],
                "message":    r["message"],
                "stars":      r["stars"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]


# ── Dashboard do Expositor ─────────────────────────────────────────────────

def dashboard(stand_id: str, access_code: str) -> dict | None:
    with _read() as c:
        stand = _get_stand(c, stand_id)
    if not _verify_code(stand, access_code):
        return None

    with _read() as c:
        visits = c.execute(
            "SELECT finished_at, duration_seconds, stars, profile_type FROM visits "
            "WHERE stand_id = ? AND finished_at IS NOT NULL ORDER BY finished_at, rowid",
            (stand_id,),
        ).fetchall()
        engs = c.execute(
            "SELECT id, type, network, message, stars, status, created_at FROM engagements "
            "WHERE stand_id = ? ORDER BY rowid",
            (stand_id,),
        ).fetchall()

    durations = [v["duration_seconds"] for v in visits if v["duration_seconds"]]
    ratings = [v["stars"] for v in visits if v["stars"]]
    distribution = {str(s): ratings.count(s) for s in range(1, 6)}

    by_profile: dict[str, int] = {}
    for v in visits:
        pt = v["profile_type"] or "desconhecido"
        by_profile[pt] = by_profile.get(pt, 0) + 1

    recent = [
        {
            "finished_at":      v["finished_at"],
            "duration_seconds": v["duration_seconds"],
            "stars":            v["stars"],
            "profile_type":     v["profile_type"],
        }
        for v in reversed(visits[-12:])
    ]

    pending_supports = [
        {"id": e["id"], "message": e["message"], "stars": e["stars"], "created_at": e["created_at"]}
        for e in engs
        if e["type"] == "support" and e["status"] == "pending"
    ]

    return {
        "stand":                    _public_stand(stand),
        "visitors":                 len(visits),
        "average_rating":           _avg(ratings),
        "average_duration_seconds": round(sum(durations) / len(durations)) if durations else 0,
        "total_duration_seconds":   sum(durations),
        "distribution":             distribution,
        "by_profile":               by_profile,
        "recent_visits":            recent,
        "shares":                   sum(1 for e in engs if e["type"] == "share"),
        "shares_by_network":        _shares_by_network([e["network"] for e in engs if e["type"] == "share"]),
        "approved_supports":        sum(
            1 for e in engs if e["type"] == "support" and e["status"] == "approved"
        ),
        "pending_supports":         list(reversed(pending_supports)),
    }


def export_visits_csv(stand_id: str, access_code: str) -> bytes | None:
    with _read() as c:
        stand = _get_stand(c, stand_id)
    if not _verify_code(stand, access_code):
        return None

    with _read() as c:
        visits = c.execute(
            "SELECT finished_at, profile_type, stars, duration_seconds FROM visits "
            "WHERE stand_id = ? AND finished_at IS NOT NULL ORDER BY finished_at, rowid",
            (stand_id,),
        ).fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["data_hora", "perfil", "estrelas", "duracao_segundos"])
    for v in visits:
        writer.writerow([v["finished_at"], v["profile_type"] or "", v["stars"], v["duration_seconds"]])
    return buf.getvalue().encode("utf-8-sig")


# ── Admin Global ───────────────────────────────────────────────────────────

def admin_dashboard(password: str) -> dict | None:
    if not _verify_admin(password):
        return None

    with _read() as c:
        stands = c.execute(
            "SELECT s.id, s.name, s.course, "
            "       COUNT(v.id) AS visitors, COUNT(v.stars) AS total_ratings, AVG(v.stars) AS avg "
            "FROM stands s LEFT JOIN visits v ON v.stand_id = s.id AND v.finished_at IS NOT NULL "
            "GROUP BY s.id ORDER BY s.rowid"
        ).fetchall()
        totals = c.execute(
            "SELECT COUNT(*) AS n, COUNT(stars) AS rated, AVG(stars) AS avg, "
            "       COUNT(DISTINCT visitor_hash) AS uniq "
            "FROM visits WHERE finished_at IS NOT NULL"
        ).fetchone()
        by_profile = {
            r["pt"]: r["n"]
            for r in c.execute(
                "SELECT COALESCE(profile_type, 'desconhecido') AS pt, COUNT(*) AS n "
                "FROM visits WHERE finished_at IS NOT NULL GROUP BY pt"
            )
        }
        networks = [
            r["network"] for r in c.execute(
                "SELECT network FROM engagements WHERE type = 'share' ORDER BY rowid"
            )
        ]
        approved = c.execute(
            "SELECT COUNT(*) FROM engagements WHERE type = 'support' AND status = 'approved'"
        ).fetchone()[0]
        checked_in = c.execute("SELECT COUNT(*) FROM visitors").fetchone()[0]

    leaderboard = [
        {
            "id":             s["id"],
            "name":           s["name"],
            "course":         s["course"],
            "visitors":       s["visitors"],
            "average_rating": round(s["avg"], 2) if s["avg"] else 0,
            "total_ratings":  s["total_ratings"],
        }
        for s in stands
    ]
    leaderboard.sort(key=lambda x: (x["average_rating"], x["total_ratings"]), reverse=True)

    return {
        "total_stands":            len(stands),
        "total_visitors":          totals["n"],
        "total_ratings":           totals["rated"],
        "global_average":          round(totals["avg"], 2) if totals["avg"] else 0,
        "by_profile":              by_profile,
        "total_shares":            len(networks),
        "shares_by_network":       _shares_by_network(networks),
        "unique_visitors":         totals["uniq"],
        "checked_in":              checked_in,
        "total_approved_supports": approved,
        "leaderboard":             leaderboard,
    }


def admin_visitors_csv(password: str) -> bytes | None:
    """Lista de credenciamento: quem se cadastrou e quantos stands avaliou."""
    if not _verify_admin(password):
        return None

    with _read() as c:
        visitors = c.execute("SELECT * FROM visitors ORDER BY rowid").fetchall()
        stats = {
            r["visitor_hash"]: (r["n"], r["avg"])
            for r in c.execute(
                "SELECT visitor_hash, COUNT(*) AS n, AVG(stars) AS avg FROM visits "
                "WHERE finished_at IS NOT NULL GROUP BY visitor_hash"
            )
        }

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "credenciado_em", "nome", "contato", "perfil",
        "cursos", "departamento_setor", "empresa",
        "stands_avaliados", "nota_media_dada",
    ])
    for v in visitors:
        n, avg = stats.get(v["visitor_hash"], (0, None))
        cursos_label = "; ".join(COURSE_LABELS.get(c, c) for c in _json_list(v["cursos"]))
        writer.writerow([
            v["checked_in_at"] or v["created_at"] or "",
            v["name"] or "",
            v["contact"] or "",
            PROFILE_LABELS.get(v["profile_type"], v["profile_type"]),
            cursos_label,
            v["department"] or "",
            v["company_name"] or "",
            n,
            round(avg, 2) if avg else "",
        ])
    return buf.getvalue().encode("utf-8-sig")


def admin_report(password: str) -> dict | None:
    """Relatório completo da feira: desempenho, público, horários e alcance.

    Usado pela página /relatorio, que pode ser impressa ou salva em PDF
    para a coordenação e para o histórico da escola.
    """
    if not _verify_admin(password):
        return None

    with _read() as c:
        stands = c.execute("SELECT id, name, course FROM stands ORDER BY rowid").fetchall()
        visits = c.execute(
            "SELECT stand_id, curso, profile_type, finished_at, duration_seconds, stars, visitor_hash "
            "FROM visits WHERE finished_at IS NOT NULL ORDER BY finished_at, rowid"
        ).fetchall()
        engs = c.execute(
            "SELECT stand_id, type, network, status FROM engagements ORDER BY rowid"
        ).fetchall()
        credenciados = c.execute("SELECT COUNT(*) FROM visitors").fetchone()[0]

    ratings = [v["stars"] for v in visits if v["stars"]]
    durs = [v["duration_seconds"] for v in visits if v["duration_seconds"]]

    # Desempenho por curso
    por_curso: dict[str, dict] = {}

    def bucket_for(curso: str) -> dict:
        return por_curso.setdefault(curso, {
            "label": COURSE_LABELS.get(curso, curso), "stands": 0, "visitas": 0, "notas": [],
        })

    for s in stands:
        bucket_for(s["course"])["stands"] += 1
    for v in visits:
        b = bucket_for(v["curso"] or "outro")
        b["visitas"] += 1
        if v["stars"]:
            b["notas"].append(v["stars"])
    for b in por_curso.values():
        notas = b.pop("notas")
        b["nota_media"] = _avg(notas)

    # Movimento por hora do dia
    por_hora: dict[str, int] = {}
    for v in visits:
        try:
            hora = datetime.fromisoformat(v["finished_at"]).strftime("%H:00")
        except (ValueError, TypeError):
            continue
        por_hora[hora] = por_hora.get(hora, 0) + 1
    por_hora = dict(sorted(por_hora.items()))

    # Público por perfil
    por_perfil: dict[str, int] = {}
    for v in visits:
        pt = v["profile_type"] or "desconhecido"
        por_perfil[pt] = por_perfil.get(pt, 0) + 1

    # Ranking completo, com engajamento por stand (agrupa uma vez: O(n))
    visits_by_stand: dict[str, list[sqlite3.Row]] = {}
    for v in visits:
        visits_by_stand.setdefault(v["stand_id"], []).append(v)
    engs_by_stand: dict[str, list[sqlite3.Row]] = {}
    for e in engs:
        engs_by_stand.setdefault(e["stand_id"], []).append(e)

    ranking = []
    for s in stands:
        s_visits = visits_by_stand.get(s["id"], [])
        s_ratings = [v["stars"] for v in s_visits if v["stars"]]
        s_durs = [v["duration_seconds"] for v in s_visits if v["duration_seconds"]]
        s_engs = engs_by_stand.get(s["id"], [])
        ranking.append({
            "id":                s["id"],
            "name":              s["name"],
            "course":            s["course"],
            "course_label":      COURSE_LABELS.get(s["course"], s["course"]),
            "visitas":           len(s_visits),
            "nota_media":        _avg(s_ratings),
            "tempo_medio":       round(sum(s_durs) / len(s_durs)) if s_durs else 0,
            "compartilhamentos": sum(1 for e in s_engs if e["type"] == "share"),
            "apoios":            sum(
                1 for e in s_engs if e["type"] == "support" and e["status"] == "approved"
            ),
        })
    ranking.sort(key=lambda x: (x["nota_media"], x["visitas"]), reverse=True)

    momentos = [v["finished_at"] for v in visits]
    satisfeitos = sum(1 for r in ratings if r >= 4)

    return {
        "gerado_em":         _now(),
        "total_stands":      len(stands),
        "stands_avaliados":  len(visits_by_stand),
        "sem_avaliacao":     [
            {"id": s["id"], "name": s["name"]} for s in stands if s["id"] not in visits_by_stand
        ],
        "total_visitas":     len(visits),
        "visitantes_unicos": len({v["visitor_hash"] for v in visits}),
        "credenciados":      credenciados,
        "nota_media":        _avg(ratings),
        "distribuicao":      {str(n): ratings.count(n) for n in range(1, 6)},
        "satisfacao_pct":    round(satisfeitos / len(ratings) * 100) if ratings else 0,
        "tempo_medio":       round(sum(durs) / len(durs)) if durs else 0,
        "tempo_total":       sum(durs),
        "por_curso":         por_curso,
        "por_perfil":        por_perfil,
        "por_hora":          por_hora,
        "compartilhamentos": sum(1 for e in engs if e["type"] == "share"),
        "shares_by_network": _shares_by_network([e["network"] for e in engs if e["type"] == "share"]),
        "apoios_aprovados":  sum(1 for e in engs if e["type"] == "support" and e["status"] == "approved"),
        "apoios_pendentes":  sum(1 for e in engs if e["type"] == "support" and e["status"] == "pending"),
        "primeira_avaliacao": momentos[0] if momentos else None,
        "ultima_avaliacao":   momentos[-1] if momentos else None,
        "ranking":            ranking,
    }


def admin_supports_csv(password: str) -> bytes | None:
    """Todas as mensagens do mural, com status de moderação."""
    if not _verify_admin(password):
        return None

    with _read() as c:
        rows = c.execute(
            "SELECT created_at, stand_nome, stars, status, message FROM engagements "
            "WHERE type = 'support' ORDER BY rowid"
        ).fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["data_hora", "stand", "estrelas", "status", "mensagem"])
    for r in rows:
        writer.writerow([r["created_at"], r["stand_nome"], r["stars"] or "", r["status"], r["message"]])
    return buf.getvalue().encode("utf-8-sig")


def admin_backup(password: str) -> bytes | None:
    """Cópia integral em JSON, sem os hashes de código de acesso."""
    if not _verify_admin(password):
        return None

    with _read() as c:
        stands = [
            {k: r[k] for k in r.keys() if k not in {"code_hash", "code_salt"}}
            for r in c.execute("SELECT * FROM stands ORDER BY rowid")
        ]
        visitas = [dict(r) for r in c.execute("SELECT * FROM visits ORDER BY rowid")]
        visitantes = []
        for r in c.execute("SELECT * FROM visitors ORDER BY rowid"):
            d = dict(r)
            d["cursos"] = _json_list(d["cursos"])
            d["own_stand_ids"] = _json_list(d["own_stand_ids"])
            visitantes.append(d)
        engajamentos = [dict(r) for r in c.execute("SELECT * FROM engagements ORDER BY rowid")]

    copia = {
        "exportado_em": _now(),
        "stands":       stands,
        "visitas":      visitas,
        "visitantes":   visitantes,
        "engajamentos": engajamentos,
    }
    return json.dumps(copia, ensure_ascii=False, indent=2).encode("utf-8")


def admin_backup_sqlite(password: str) -> bytes | None:
    """Cópia consistente do arquivo .db (usa a API de backup do SQLite).

    Seguro de rodar com o servidor em uso. Inclui os hashes dos códigos de
    acesso — guarde o arquivo como dado sensível.
    """
    if not _verify_admin(password):
        return None

    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        src = _connect()
        dst = sqlite3.connect(tmp_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def admin_export_csv(password: str) -> bytes | None:
    if not _verify_admin(password):
        return None

    with _read() as c:
        rows = c.execute(
            "SELECT v.finished_at, s.name, s.course, v.profile_type, v.stars, v.duration_seconds "
            "FROM visits v LEFT JOIN stands s ON s.id = v.stand_id "
            "WHERE v.finished_at IS NOT NULL ORDER BY v.finished_at, v.rowid"
        ).fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["data_hora", "stand", "curso", "perfil_visitante", "estrelas", "duracao_segundos"])
    for r in rows:
        writer.writerow([
            r["finished_at"], r["name"] or "", r["course"] or "",
            r["profile_type"] or "", r["stars"], r["duration_seconds"],
        ])
    return buf.getvalue().encode("utf-8-sig")


# ── Manutenção ─────────────────────────────────────────────────────────────

def reset_all() -> None:
    """Apaga todos os dados (usado por reset_data.py e pelos testes)."""
    with _write() as c:
        for table in ("recoveries", "engagements", "visits", "visitors", "stands"):
            c.execute(f"DELETE FROM {table}")
