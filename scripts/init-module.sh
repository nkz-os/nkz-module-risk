#!/bin/bash
# =============================================================================
# Nekazari Module Initializer
# =============================================================================
# Interactive script to initialize a new module from the template.
# Replaces all placeholder values with your module's configuration.
# =============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}"
echo "=============================================="
echo "   Nekazari Module Template Initializer"
echo "=============================================="
echo -e "${NC}"

# Check if we're in the right directory
if [ ! -f "manifest.json" ] || [ ! -f "vite.config.ts" ]; then
    echo -e "${RED}Error: This script must be run from the module template directory.${NC}"
    echo "Please navigate to the module template directory first."
    exit 1
fi

# Collect module information
echo -e "${YELLOW}Please provide the following information for your module:${NC}"
echo ""

# Module Name (lowercase, hyphens)
read -p "Module Name (lowercase with hyphens, e.g., 'soil-sensor'): " risk
if [ -z "$risk" ]; then
    echo -e "${RED}Error: Module name is required.${NC}"
    exit 1
fi

# Validate module name format
if [[ ! "$risk" =~ ^[a-z][a-z0-9-]*$ ]]; then
    echo -e "${RED}Error: Module name must start with a letter and contain only lowercase letters, numbers, and hyphens.${NC}"
    exit 1
fi

# Module Display Name
read -p "Display Name (e.g., 'Soil Sensor'): " Risk
if [ -z "$Risk" ]; then
    Risk=$(echo "$risk" | sed -r 's/(^|-)([a-z])/\U\2/g')
    echo -e "${YELLOW}Using default display name: ${Risk}${NC}"
fi

# Route Path
/risk="/$risk"
read -p "Route Path [${/risk}]: " CUSTOM_ROUTE
if [ -n "$CUSTOM_ROUTE" ]; then
    /risk="$CUSTOM_ROUTE"
fi

# GitHub Organization
read -p "GitHub Organization (e.g., 'nkz-os'): " YOUR_ORG
if [ -z "$YOUR_ORG" ]; then
    echo -e "${RED}Error: GitHub organization is required.${NC}"
    exit 1
fi

# Author Name
read -p "Author Name: " AUTHOR_NAME
if [ -z "$AUTHOR_NAME" ]; then
    AUTHOR_NAME="Unknown"
fi

# Confirm
echo ""
echo -e "${BLUE}=============================================="
echo "   Configuration Summary"
echo "==============================================${NC}"
echo "Module Name:   $risk"
echo "Display Name:  $Risk"
echo "Route Path:    $/risk"
echo "GitHub Org:    $YOUR_ORG"
echo "Author:        $AUTHOR_NAME"
echo ""

read -p "Proceed with initialization? (y/N): " CONFIRM
if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}Initialization cancelled.${NC}"
    exit 0
fi

echo ""
echo -e "${BLUE}Initializing module...${NC}"

# =============================================================================
# Perform replacements
# =============================================================================

replace_in_files() {
    local search="$1"
    local replace="$2"
    find . -type f \
        -not -path "*/node_modules/*" \
        -not -path "*/.git/*" \
        -not -path "*/dist/*" \
        -not -path "*/__pycache__/*" \
        -not -path "*/.venv/*" \
        -not -name "init-module.sh" \
        -exec grep -l "$search" {} \; 2>/dev/null | while read file; do
        sed -i "s|$search|$replace|g" "$file"
        echo "  Updated: $file"
    done
}

echo ""
echo -e "${GREEN}Replacing placeholders...${NC}"

replace_in_files "risk" "$risk"
replace_in_files "Risk" "$Risk"
replace_in_files "/risk" "$/risk"
replace_in_files "YOUR_ORG" "$YOUR_ORG"
replace_in_files "YOUR_NAME" "$AUTHOR_NAME"

# =============================================================================
# Additional setup
# =============================================================================

echo ""
echo -e "${GREEN}Creating local env file...${NC}"

if [ -f ".env.example" ] && [ ! -f ".env" ]; then
    cp .env.example .env
    echo "  Created: .env from .env.example (edit VITE_PROXY_TARGET)"
fi

# =============================================================================
# Done!
# =============================================================================

echo ""
echo -e "${GREEN}=============================================="
echo "   Module initialization complete!"
echo "==============================================${NC}"
echo ""
echo "Next steps:"
echo "  1. Edit .env — set VITE_PROXY_TARGET to your API domain"
echo "  2. Install dependencies:  pnpm install"
echo "  3. Start dev server:      pnpm run dev"
echo "  4. Build MF2 bundle:      pnpm run build:module  →  dist/remoteEntry.js + dist/mf-manifest.json"
echo ""
echo "For deployment see SETUP.md."
echo ""
echo -e "${BLUE}Happy coding!${NC}"
