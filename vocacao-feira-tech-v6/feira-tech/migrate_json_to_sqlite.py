"""Importa os dados da versão antiga (feira_tech.json) para o banco SQLite.

Uso:
    python migrate_json_to_sqlite.py caminho/para/feira_tech.json
    python migrate_json_to_sqlite.py feira_tech.json --force   # banco já tem dados

Os códigos de acesso dos expositores continuam funcionando (o hash é copiado).
O banco de destino é o mesmo que o app usa (DATA_PATH ou ./data/feira_tech.db).
Rodar duas vezes com --force não duplica nada (INSERT OR IGNORE).
"""
import json
import sys

import db_store as store


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv
    if not args:
        print(__doc__)
        return 1

    with open(args[0], encoding="utf-8") as f:
        data = json.load(f)

    store.init_db()
    with store._read() as c:
        existentes = c.execute("SELECT COUNT(*) FROM stands").fetchone()[0]
    if existentes and not force:
        print(f"O banco {store.DB_PATH} já tem {existentes} stand(s). Use --force para mesclar.")
        return 1

    contagem = {"stands": 0, "visitantes": 0, "visitas": 0, "engajamentos": 0, "recuperacoes": 0}
    ignorados = {k: 0 for k in contagem}

    def registrar(tabela, cursor):
        if cursor.rowcount:
            contagem[tabela] += 1
        else:
            ignorados[tabela] += 1

    with store._write() as c:
        for s in data.get("stands", []):
            registrar("stands", c.execute(
                "INSERT OR IGNORE INTO stands (id, name, group_id, course, code_salt, code_hash, "
                "code_reset_at, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (s["id"], s["name"], s.get("group_id") or store.normalize_group(s["name"]),
                 s["course"], s["code_salt"], s["code_hash"], s.get("code_reset_at"),
                 s.get("created_at") or store._now()),
            ))

        for v in data.get("visitantes", []):
            cursos = v.get("cursos")
            if cursos is None:                       # formato antigo: um curso só
                cursos = [v["curso_aluno"]] if v.get("curso_aluno") else []
            registrar("visitantes", c.execute(
                "INSERT OR IGNORE INTO visitors (visitor_hash, profile_type, name, contact, cursos, "
                "group_id, own_stand_ids, department, company_name, created_at, checked_in_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (v["visitor_hash"], v["profile_type"], v.get("name"), v.get("contact"),
                 json.dumps(cursos, ensure_ascii=False), v.get("group_id"),
                 json.dumps(v.get("own_stand_ids") or []), v.get("department"),
                 v.get("company_name"), v.get("created_at") or store._now(),
                 v.get("checked_in_at"), v.get("updated_at")),
            ))

        for v in data.get("visitas", []):
            registrar("visitas", c.execute(
                "INSERT OR IGNORE INTO visits (id, stand_id, stand_nome, curso, visitor_hash, profile_type, "
                "started_at, finished_at, duration_seconds, stars) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (v["id"], v["stand_id"], v.get("stand_nome") or "", v.get("curso"),
                 v["visitor_hash"], v.get("profile_type"), v["started_at"], v.get("finished_at"),
                 v.get("duration_seconds"), v.get("stars")),
            ))

        for e in data.get("engajamentos", []):
            tipo = e.get("type")
            if tipo not in ("share", "support"):
                ignorados["engajamentos"] += 1
                continue
            registrar("engajamentos", c.execute(
                "INSERT OR IGNORE INTO engagements (id, stand_id, stand_nome, visitor_hash, type, network, "
                "message, stars, status, created_at, moderated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (e["id"], e["stand_id"], e.get("stand_nome") or "", e["visitor_hash"], tipo,
                 (e.get("network") or "outro") if tipo == "share" else None,
                 e.get("message"), e.get("stars"),
                 (e.get("status") or "pending") if tipo == "support" else None,
                 e.get("created_at") or store._now(), e.get("moderated_at")),
            ))

        for r in data.get("recuperacoes", []):
            registrar("recuperacoes", c.execute(
                "INSERT OR IGNORE INTO recoveries (id, stand_id, stand_nome, requester, status, "
                "created_at, resolved_at) VALUES (?,?,?,?,?,?,?)",
                (r["id"], r["stand_id"], r.get("stand_nome") or "", r.get("requester"),
                 r.get("status") or "pending", r.get("created_at") or store._now(), r.get("resolved_at")),
            ))

    print(f"Banco: {store.DB_PATH}")
    for k in contagem:
        extra = f" ({ignorados[k]} ignorado(s))" if ignorados[k] else ""
        print(f"  {k:13s} importados: {contagem[k]}{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
