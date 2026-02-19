#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  Swarm Agent Stack — One-click local dev setup
#  Usage:  bash setup.sh
# ══════════════════════════════════════════════════════════════
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

VENV_DIR=".venv"

# ── Colors ──
RED='\033[0;31m'
GREEN='\033[0;32m'
DIM='\033[2m'
RESET='\033[0m'

ok()   { echo -e "  ${GREEN}✓${RESET} $1"; }
fail() { echo -e "  ${RED}✗${RESET} $1"; exit 1; }
info() { echo -e "  ${DIM}$1${RESET}"; }

echo ""
echo "  ███████╗██╗    ██╗ █████╗ ██████╗ ███╗   ███╗"
echo "  ██╔════╝██║    ██║██╔══██╗██╔══██╗████╗ ████║"
echo "  ███████╗██║ █╗ ██║███████║██████╔╝██╔████╔██║"
echo "  ╚════██║██║███╗██║██╔══██║██╔══██╗██║╚██╔╝██║"
echo "  ███████║╚███╔███╔╝██║  ██║██║  ██║██║ ╚═╝ ██║"
echo "  ╚══════╝ ╚══╝╚══╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝     ╚═╝"
echo ""

# ── 1. Find Python 3 ──
PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null; then
        version=$("$candidate" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    fail "Python 3.10+ required. Install: brew install python3"
fi
ok "Python: $($PYTHON --version)"

# ── 2. Create virtual environment ──
if [ ! -d "$VENV_DIR" ]; then
    info "Creating virtual environment..."
    "$PYTHON" -m venv "$VENV_DIR"
    ok "Virtual environment created: $VENV_DIR/"
else
    ok "Virtual environment exists: $VENV_DIR/"
fi

# Activate
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# ── 3. Install dependencies ──
info "Installing dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q 2>&1 | tail -1 || true
pip install -r requirements-dev.txt -q 2>&1 | tail -1 || true
ok "Dependencies installed"

# ── 4. Create .env if missing ──
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
        ok "Created .env from .env.example"
        info "Edit .env to add your API keys"
    fi
else
    ok ".env exists"
fi

# ── 5. Create directories ──
mkdir -p config .logs memory workflows bin

# ── 6. Create the 'swarm' alias/wrapper ──
# Make swarm executable
chmod +x "$ROOT/swarm"

# Create a venv-aware wrapper
cat > "$ROOT/bin/swarm" <<WRAPPER
#!/usr/bin/env bash
# Auto-activate venv and run swarm
ROOT="$ROOT"
source "\$ROOT/.venv/bin/activate"
exec python3 "\$ROOT/swarm" "\$@"
WRAPPER
chmod +x "$ROOT/bin/swarm"
ok "CLI wrapper: bin/swarm"

# ── 7. Run quick health check ──
info "Running health check..."
if python3 -c "import yaml, httpx, filelock, rich, questionary" 2>/dev/null; then
    ok "All core packages importable"
else
    fail "Some packages failed to import — check pip install output above"
fi

echo ""
echo -e "  ${GREEN}Setup complete!${RESET}"
echo ""
echo "  How to run:"
echo "    source .venv/bin/activate    # activate venv (once per shell)"
echo "    python3 swarm                # start interactive mode"
echo "    python3 swarm configure      # first-time setup wizard"
echo "    python3 swarm doctor         # system health check"
echo ""
echo "  Or use the wrapper (no activation needed):"
echo "    ./bin/swarm                  # auto-activates venv"
echo ""
echo "  Optional: add to PATH:"
echo "    export PATH=\"$ROOT/bin:\$PATH\""
echo ""
