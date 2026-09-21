"""Limpa os dados locais da Feira Tech (banco SQLite).

ATENÇÃO: apaga stands, visitas, perfis e engajamentos.
Dica: antes, baixe um backup em /admin (botão de backup) ou copie o arquivo .db.
Uso: python reset_data.py
"""

import db_store as store

confirm = input(f"Digite RESET para apagar TODOS os dados de {store.DB_PATH}: ").strip()
if confirm != "RESET":
    print("Operação cancelada.")
    raise SystemExit(0)

store.init_db()
store.reset_all()
print(f"Dados zerados em: {store.DB_PATH}")
