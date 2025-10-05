#!/bin/bash

# Script to load environment variables from .env.test file
# Usage: source load_env_test.sh  (or . load_env_test.sh)

ENV_FILE=".env.test"

if [ ! -f "$ENV_FILE" ]; then
    echo "Error: $ENV_FILE file not found!"
    return 1 2>/dev/null || exit 1
fi

echo "Loading environment variables from $ENV_FILE..."

# Read the .env.test file line by line
while IFS= read -r line || [ -n "$line" ]; do
    # Skip empty lines and comments
    if [[ -z "$line" ]] || [[ "$line" =~ ^[[:space:]]*# ]]; then
        continue
    fi
    
    # Remove leading/trailing whitespace
    line=$(echo "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
    
    # Skip if line doesn't contain '='
    if [[ ! "$line" =~ = ]]; then
        continue
    fi
    
    # Export the variable
    export "$line"
    
    # Extract variable name for display (before the '=' sign)
    var_name=$(echo "$line" | cut -d '=' -f 1)
    echo "  ✓ Exported: $var_name"
    
done < "$ENV_FILE"

echo "Environment variables loaded successfully!"
echo ""
echo "Note: These variables are only set for the current shell session."
echo "To make them permanent, add 'source $(pwd)/load_env_test.sh' to your ~/.zshrc"
