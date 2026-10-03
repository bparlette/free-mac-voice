#!/usr/bin/env bash
# Runs tests with minimal token footprint (<25 tokens on pass).
set -e
OUTPUT=$(./.venv/bin/python -m unittest discover tests 2>&1) || { echo "$OUTPUT" | grep -A 10 -B 2 "FAIL\|ERROR"; exit 1; }
echo "Suite: $(echo "$OUTPUT" | tail -n 2 | tr '\n' ' ')"

if [ -d "tests/personal" ]; then
    POUT=$(./.venv/bin/python -m unittest discover tests/personal 2>&1) || { echo "$POUT" | grep -A 10 -B 2 "FAIL\|ERROR"; exit 1; }
    echo "Personal: $(echo "$POUT" | tail -n 2 | tr '\n' ' ')"
fi
