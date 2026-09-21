#!/usr/bin/env bash
# Inicialização local — macOS / Linux
set -e

cd "$(dirname "$0")"

echo ""
echo "============================================"
echo "  1ª Feira Tech dos Jovens da Vocação"
echo "============================================"
echo ""

if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERRO] Python 3 não encontrado."
  echo "Instale pelo site oficial do Python ou pelo Homebrew."
  exit 1
fi

PYTHON=python3

if [ ! -d ".venv" ]; then
  echo "[1/3] Criando ambiente virtual..."
  "$PYTHON" -m venv .venv
fi

echo "[2/3] Ativando ambiente virtual..."
source .venv/bin/activate

echo "[3/3] Conferindo dependências..."
python -m pip install -r requirements.txt

# Não fixe PORT=5000: o app escolhe automaticamente uma porta livre.
export ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin@feira2025}"
export SECRET_KEY="${SECRET_KEY:-dev-secret-key-local}"

echo ""
echo "Iniciando servidor..."
python app.py
